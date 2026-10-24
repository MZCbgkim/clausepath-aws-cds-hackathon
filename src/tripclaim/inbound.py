"""Normalise inbound events from every channel into Inbound / DeliveryStatus records.

- WhatsApp (AWS End User Messaging Social): SNS message whose `whatsAppWebhookEntry` is a JSON string in
  Meta's webhook format (messages[] and statuses[]).
- SMS / RCS two-way (AWS End User Messaging): SNS message with originationNumber / messageBody.
- SMS / RCS delivery events: configuration-set event destination on SNS with eventType / messageId.
- Email (Amazon SES receiving): raw MIME stored to S3 by a receipt rule.
"""
import email
import email.policy
import html as htmllib
import json
import re
from dataclasses import dataclass, field


@dataclass
class Inbound:
    channel: str
    sender: str
    text: str = ""
    media: list = field(default_factory=list)   # [{"media_id"| "bytes", "mime", "filename"}]
    provider_id: str = ""
    subject: str = ""
    claim_id: str = ""   # set for web chat: the link already identifies the claim


@dataclass
class DeliveryStatus:
    channel: str
    provider_id: str
    status: str            # sent | delivered | read | failed
    detail: str = ""


def e164(number):
    number = (number or "").strip()
    return number if number.startswith("+") or not number else "+" + number


def parse_sns_message(message, rcs_agent=""):
    """message: the SNS `Message` (string or dict). Returns a list of Inbound / DeliveryStatus."""
    body = json.loads(message) if isinstance(message, str) else message
    if "whatsAppWebhookEntry" in body:
        return _whatsapp(body)
    if "eventType" in body and "messageId" in body:
        return [_sms_event(body)]
    if "messageBody" in body and "originationNumber" in body:
        channel = "rcs" if rcs_agent and body.get("destinationNumber") == rcs_agent else "sms"
        return [Inbound(channel=channel, sender=e164(body["originationNumber"]), text=body.get("messageBody", ""),
                        provider_id=body.get("inboundMessageId", ""))]
    return []


def _whatsapp(body):
    entry = body["whatsAppWebhookEntry"]
    entry = json.loads(entry) if isinstance(entry, str) else entry
    out = []
    for change in entry.get("changes", []):
        value = change.get("value", {})
        for m in value.get("messages", []):
            kind = m.get("type")
            text, media = "", []
            if kind == "text":
                text = m["text"]["body"]
            elif kind in ("image", "document"):
                part = m[kind]
                text = part.get("caption", "")
                media.append({"media_id": part["id"], "mime": part.get("mime_type", "image/jpeg"),
                              "filename": part.get("filename", f"{part['id']}.jpg")})
            elif kind == "button":
                text = m["button"].get("text") or m["button"].get("payload", "")
            elif kind == "interactive":
                reply = m["interactive"].get("button_reply") or m["interactive"].get("list_reply") or {}
                text = reply.get("title", "")
            out.append(Inbound(channel="whatsapp", sender=e164(m["from"]), text=text, media=media, provider_id=m["id"]))
        for s in value.get("statuses", []):
            detail = "; ".join(e.get("title", "") for e in s.get("errors", []))
            out.append(DeliveryStatus(channel="whatsapp", provider_id=s["id"], status=s.get("status", ""), detail=detail))
    return out


def _sms_event(body):
    event = body["eventType"].upper()
    channel = "rcs" if event.startswith("RCS") else "sms"
    if any(w in event for w in ("FAILED", "BLOCKED", "INVALID", "UNREACHABLE", "TTL_EXPIRED", "CARRIER_UNREACHABLE")):
        status = "failed"
    elif "DELIVERED" in event:
        status = "delivered"
    else:
        status = "sent"
    return DeliveryStatus(channel=channel, provider_id=body["messageId"], status=status, detail=event)


def parse_email(raw_bytes):
    """Raw MIME (as stored by SES) -> Inbound with text body and image/PDF attachments."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    sender = email.utils.parseaddr(msg.get("From", ""))[1].lower()
    text, html_body, media = "", "", []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disposition = part.get_content_disposition()
        if ctype.startswith("image/") or ctype == "application/pdf":
            media.append({"bytes": part.get_payload(decode=True), "mime": ctype,
                          "filename": part.get_filename() or f"attachment.{ctype.split('/')[1]}"})
        elif ctype == "text/plain" and disposition != "attachment" and not text:
            text = part.get_content()
        elif ctype == "text/html" and disposition != "attachment" and not html_body:
            html_body = part.get_content()
    if not text and html_body:
        text = htmllib.unescape(re.sub(r"<[^>]+>", " ", html_body))
    return Inbound(channel="email", sender=sender, text=text.strip(), media=media,
                   provider_id=(msg.get("Message-ID") or "").strip("<>"), subject=msg.get("Subject", ""))
