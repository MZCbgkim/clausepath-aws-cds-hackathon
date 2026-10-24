from datetime import datetime, timedelta, timezone

from tripclaim.channels.fake import fake_channels
from tripclaim.router import Router
from tripclaim.store import MemoryBackend, Repository

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


def setup(**traveler):
    repo = Repository(MemoryBackend())
    t = {"id": "t", "name": "A B", "phone": "+1555", "email": "a@b.c", "consent": {"whatsapp": True, "sms": True, "email": True}}
    t.update(traveler)
    repo.put_traveler(t)
    claim = repo.create_claim("t")
    outbox = []
    chans = fake_channels(outbox)
    return Router(repo, chans, lambda: NOW), repo.get_traveler("t"), claim["id"], outbox, chans


def test_outside_window_uses_template_then_text_inside():
    router, t, cid, outbox, _ = setup()
    router.send(t, cid, "alert", "hi", template=("claim_received", ["A", "c1"]))
    assert outbox[-1]["kind"] == "template"
    t["last_inbound"] = {"whatsapp": (NOW - timedelta(hours=1)).isoformat()}
    msg = router.send(t, cid, "reply", "hello", reply_on="whatsapp")
    assert outbox[-1]["kind"] == "text" and "inside 24h" in msg["chosen_reason"]


def test_no_template_outside_window_falls_to_rcs():
    router, t, cid, outbox, _ = setup()
    msg = router.send(t, cid, "reply", "free text")
    assert outbox[-1]["channel"] == "rcs" and "outside 24h window" in msg["chosen_reason"]


def test_failure_falls_back_and_marks_unreachable():
    router, t, cid, outbox, chans = setup(last_inbound={"whatsapp": NOW.isoformat()})
    chans["whatsapp"].fail.add("+1555")
    msg = router.send(t, cid, "reply", "x", reply_on="whatsapp")
    assert msg["channel"] == "rcs" and msg["chosen_reason"].startswith("whatsapp: whatsapp: delivery")
    assert "whatsapp" in router.repo.get_traveler("t")["unreachable"]


def test_consent_and_packet_goes_by_email():
    router, t, cid, outbox, _ = setup(consent={"email": True})
    msg = router.send(t, cid, "receipt", "copy", email_subject="Claim")
    assert msg["channel"] == "email" and outbox[-1]["subject"] == "Claim"
    msg = router.send(t, cid, "reply", "x")
    assert msg["channel"] == "email"  # no messaging consent
