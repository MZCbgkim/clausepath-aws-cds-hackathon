import json
from email.message import EmailMessage

from tripclaim.inbound import DeliveryStatus, Inbound, parse_email, parse_sns_message


def wa_event(value):
    return json.dumps({"context": {"MetaPhoneNumberIds": [{"metaPhoneNumberId": "123"}]},
                       "whatsAppWebhookEntry": json.dumps({"id": "waba", "changes": [{"field": "messages", "value": value}]})})


def test_whatsapp_text_image_and_status():
    out = parse_sns_message(wa_event({
        "messages": [{"from": "15551230001", "id": "wamid.1", "type": "text", "text": {"body": "hello"}},
                     {"from": "15551230001", "id": "wamid.2", "type": "image",
                      "image": {"id": "media-9", "mime_type": "image/jpeg", "caption": "receipt"}}],
        "statuses": [{"id": "wamid.out", "status": "failed", "errors": [{"title": "Message undeliverable"}]}]}))
    text, image, status = out
    assert text == Inbound(channel="whatsapp", sender="+15551230001", text="hello", provider_id="wamid.1")
    assert image.media == [{"media_id": "media-9", "mime": "image/jpeg", "filename": "media-9.jpg"}] and image.text == "receipt"
    assert status == DeliveryStatus(channel="whatsapp", provider_id="wamid.out", status="failed", detail="Message undeliverable")


def test_whatsapp_button_reply():
    [m] = parse_sns_message(wa_event({"messages": [{"from": "1555", "id": "w3", "type": "button",
                                                     "button": {"text": "YES", "payload": "YES"}}]}))
    assert m.text == "YES"


def test_sms_and_rcs_inbound():
    sms = {"originationNumber": "+15551230001", "destinationNumber": "+18885550000", "messageBody": "1",
           "inboundMessageId": "in-1"}
    assert parse_sns_message(json.dumps(sms))[0].channel == "sms"
    rcs = dict(sms, destinationNumber="agent-abc")
    assert parse_sns_message(json.dumps(rcs), rcs_agent="agent-abc")[0].channel == "rcs"


def test_delivery_events():
    [f] = parse_sns_message({"eventType": "TEXT_CARRIER_UNREACHABLE", "messageId": "m1"})
    [d] = parse_sns_message({"eventType": "RCS_DELIVERED", "messageId": "m2"})
    assert (f.status, f.channel) == ("failed", "sms") and (d.status, d.channel) == ("delivered", "rcs")


def test_email_with_attachment():
    m = EmailMessage()
    m["From"], m["Subject"] = "Jordan <JORDAN@example.com>", "Fwd: hotel"
    m.set_content("see attached")
    m.add_attachment(b"\xff\xd8jpeg", maintype="image", subtype="jpeg", filename="hotel.jpg")
    inb = parse_email(m.as_bytes())
    assert inb.sender == "jordan@example.com" and inb.text == "see attached"
    assert inb.media[0]["filename"] == "hotel.jpg" and inb.media[0]["bytes"] == b"\xff\xd8jpeg"
