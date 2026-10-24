"""End-to-end claim flow with fake channels, fake LLM and the scripted agent."""
from email.message import EmailMessage

from tripclaim.inbound import DeliveryStatus, Inbound, parse_email

from .conftest import EMAIL, EVENT, PHONE


def wa(text="", media=None, pid=None):
    import uuid
    return Inbound(channel="whatsapp", sender=PHONE, text=text, media=media or [], provider_id=pid or uuid.uuid4().hex)


def test_full_claim_flow(env):
    svc, outbox = env["svc"], env["outbox"]

    # 1. disruption -> proactive WhatsApp template (no open 24h window yet)
    [claim_id] = svc.on_disruption(dict(EVENT))
    assert outbox[-1]["channel"] == "whatsapp" and outbox[-1]["template"] == "flight_disruption_alert"

    # 2. traveler answers -> session text inside the 24h window, coverage via the tool
    res = svc.handle_inbound(wa("YES. Is my hotel covered?"))
    assert res["channel"] == "whatsapp" and "TD-3" in res["reply"]
    assert [m["text"] for m in outbox[-2:]] == ["Checking your policy, one moment…", res["reply"]]
    timeline = svc.repo.messages(claim_id)
    assert [(m["direction"], m["kind"]) for m in timeline[:2]] == [("out", "template"), ("in", "text")]
    assert timeline[-1]["subject"] is None  # WhatsApp replies carry no email subject
    assert svc.repo.get_claim(claim_id)["coverage"]["cited_clauses"] == ["TD-1", "TD-3"]

    # 3. airline letter + hotel receipt + a lunch before the scheduled departure
    svc.channels["whatsapp"].media.update({"m1": b"LETTER", "m2": b"HOTEL", "m3": b"LUNCH"})
    svc.handle_inbound(wa("here", [{"media_id": "m1", "mime": "image/jpeg", "filename": "letter.jpg"}]))
    svc.handle_inbound(wa("", [{"media_id": "m2", "mime": "image/jpeg", "filename": "hotel.jpg"},
                               {"media_id": "m3", "mime": "image/jpeg", "filename": "lunch.jpg"}]))
    view = svc.claim_view(claim_id)
    flags = {f["code"] for d in view["documents"] for f in d["flags"]}
    assert flags == {"out_of_window"}                       # lunch at 12:10 is before 17:00
    assert view["checklist"]["airline_letter"]["done"] and view["checklist"]["original_eticket"]["done"]
    assert "outside the delay window" in outbox[-1]["text"]

    # 4. WhatsApp delivery fails later -> same message re-sent by another channel, reason recorded
    last_wa = outbox[-1]
    svc.handle_status(DeliveryStatus(channel="whatsapp", provider_id=last_wa["provider_id"], status="failed"))
    assert outbox[-1]["channel"] == "rcs" and outbox[-1]["text"] == last_wa["text"]
    resent = [m for m in svc.repo.messages(claim_id) if m.get("provider_id") == outbox[-1]["provider_id"]][0]
    assert resent["chosen_reason"].startswith("whatsapp reported undelivered")
    assert svc.repo.get_traveler("trv-1")["unreachable"]["whatsapp"]

    # 5. boarding pass by WhatsApp again (inbound clears the unreachable mark)
    svc.channels["whatsapp"].media["m4"] = b"BOARDING"
    svc.handle_inbound(wa("boarding now", [{"media_id": "m4", "mime": "image/jpeg", "filename": "bp.jpg"}]))
    assert outbox[-1]["channel"] == "whatsapp"

    # 6. bank details by message are refused and routed to the secure page
    res = svc.handle_inbound(wa("my bank account number is 123"))
    assert "secure" in res["reply"] and "https://demo.test/secure/" in outbox[-1]["text"]  # sent in full, stored masked
    token = svc.secure_link(claim_id).rsplit("/", 1)[1]

    # 7. secure page completes the checklist; SUBMIT sends the packet to the reviewer and a copy to the traveler
    svc.secure_submit(token, {"signed_name": "Jordan Taylor", "holder": "Jordan Taylor", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    assert svc.repo.get_claim(claim_id)["status"] == "ready"
    res = svc.handle_inbound(wa("SUBMIT"))                 # one word by text: a summary to confirm, nothing filed yet
    assert "Reply YES" in res["reply"] and "•••7890" in res["reply"]
    assert svc.repo.get_claim(claim_id)["status"] == "ready"
    assert "Cancelled" in svc.handle_inbound(wa("NO"))["reply"]          # NO cancels; a stray "y" does nothing
    svc.handle_inbound(wa("SUBMIT"))
    res = svc.handle_inbound(wa("YES"))
    assert "Submitted" in res["reply"]
    packet = next(m for m in outbox if m["kind"] == "email" and m["to"] == "reviewer@example.com")
    assert len(packet["attachments"]) == 4 and "Expense outside the delay window" in packet["html"]
    receipt_mail = next(m for m in outbox if m["kind"] == "email" and m["to"] == EMAIL)
    assert "We received your claim" in receipt_mail["subject"]
    assert svc.repo.get_claim(claim_id)["status"] == "submitted"

    # 8. reviewer asks for information -> traveler gets a status update
    svc.reviewer_action(claim_id, "reviewer@example.com", "request_info", "Please send the taxi receipt.")
    assert svc.repo.get_claim(claim_id)["status"] == "info_requested"
    assert "taxi receipt" in outbox[-1]["text"]


def test_duplicate_inbound_is_ignored(env):
    svc = env["svc"]
    svc.on_disruption(dict(EVENT))
    assert svc.handle_inbound(wa("hi", pid="same")) is not None
    assert svc.handle_inbound(wa("hi", pid="same")) is None


def test_forwarded_airline_email_becomes_letter(env):
    svc = env["svc"]
    [claim_id] = svc.on_disruption(dict(EVENT))
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"Jordan <{EMAIL}>", "claim@example.com", "Fwd: Kumo Air flight cancelled"
    m.set_content("---------- Forwarded message ---------\nFrom: Kumo Air\nYour flight XX123 on 05 Oct was cancelled "
                  "due to Typhoon Hagi. Booking K7Q2LM. New flight XX123 06 Oct 17:00.")
    inb = parse_email(m.as_bytes())
    res = svc.handle_inbound(inb)
    assert res["channel"] == "email"
    docs = svc.repo.documents(claim_id)
    assert docs[0]["extracted"]["doc_type"] == "airline_letter" and docs[0]["source_channel"] == "email"


def test_reminders_respect_limit(env):
    svc, outbox, clock = env["svc"], env["outbox"], env["clock"]
    svc.on_disruption(dict(EVENT))
    from datetime import timedelta
    sent = 0
    for _ in range(5):
        clock.now += timedelta(hours=25)
        sent += len(svc.run_reminders())
    assert sent == 3 and outbox[-1]["template"] == "document_reminder"


def test_basic_plan_gets_no_alert(env):
    svc, outbox = env["svc"], env["outbox"]
    svc.repo.put_policy("trv-1", {"id": "pol-1", "plan": "Basic", "purchased_at": "2026-09-20T10:00+09:00"})
    assert svc.on_disruption(dict(EVENT)) == [] and outbox == []


def test_malformed_receipt_date_asks_for_retake(env):
    from .conftest import DOCS, receipt
    svc = env["svc"]
    [claim_id] = svc.on_disruption(dict(EVENT))
    DOCS[b"BAD"] = receipt("Taxi", "Ota", 3200, "local_transport")  # model put the city in the date field
    svc.channels["whatsapp"].media["mb"] = b"BAD"
    res = svc.handle_inbound(wa("taxi", [{"media_id": "mb", "mime": "image/jpeg", "filename": "t.jpg"}]))
    assert res is not None
    [doc] = svc.repo.documents(claim_id)
    assert doc["flags"][0]["code"] == "retake" and "not readable" in doc["flags"][0]["detail"]
