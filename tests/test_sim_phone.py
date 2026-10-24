"""The simulated phone (SIM_PHONE, demo only): the real router's SMS/RCS reach /api/sim/phone, replies come back in."""
import dataclasses
import json

import pytest

from tripclaim.channels.live import live_channels
from tripclaim.channels.simphone import SimPhone
from tripclaim.config import Settings
from tripclaim.handlers import api


@pytest.fixture
def sim(env, monkeypatch):
    env["svc"].s = dataclasses.replace(env["svc"].s, sim_phone=True)
    api.SERVICE = env["svc"]
    monkeypatch.setattr(api, "process_web_async", api.handle_web_inbound)  # run the turn inline in tests
    yield env
    api.SERVICE = None


def call(method, path, body=None, user=None):
    res = api.handler({"requestContext": {"http": {"method": method}}, "rawPath": path,
                       "headers": {"x-demo-user": user} if user else {},
                       "body": json.dumps(body) if body is not None else None}, None)
    return res["statusCode"], json.loads(res["body"]) if res["headers"]["content-type"] == "application/json" else res["body"]


def test_sim_phone_takes_sms_and_rcs_only_when_asked():
    on = live_channels(Settings(sim_phone=True, rcs_agent="agent-1"))
    assert isinstance(on["sms"], SimPhone) and isinstance(on["rcs"], SimPhone)  # even with a real RCS agent set
    off = live_channels(Settings(sim_phone=False))
    assert "sms" not in off and "rcs" not in off
    assert SimPhone("sms").send_text("+12025550100", "hi").startswith("sim-sms-")


def test_alert_reaches_the_phone_and_a_reply_goes_back_through_the_inbound_path(sim):
    svc = sim["svc"]
    personas = call("POST", "/api/demo/personas")[1]["personas"]
    jisoo = svc.repo.get_traveler("trv-persona-plus")
    assert jisoo["phone"] == "+12025550100" and jisoo["consent"] == {"sms": True}
    ev = svc.repo.get_event(svc.repo.get_claim(personas[0]["claim_id"])["event_id"])
    alerted = svc.on_disruption({k: v for k, v in ev.items() if k not in ("id", "received_at")})  # the same disruption, reported
    assert personas[0]["claim_id"] in alerted  # the alert links to the persona's claim, not a second one
    assert len([c for c in svc.repo.list_claims() if c["traveler_id"] == "trv-persona-plus"]) == 1

    status, phones = call("GET", "/api/sim/phone")
    assert status == 200 and any(p["id"] == "trv-persona-plus" for p in phones["phones"])
    thread = call("GET", "/api/sim/phone/trv-persona-plus")[1]
    assert [m["channel"] for m in thread["messages"]] == ["sms"] and "/c/" in thread["messages"][0]["text"]
    assert thread["messages"][0]["simulated"] is True

    assert "••••" in thread["messages"][0]["text"]  # stored masked; a tap gets a fresh link to the same claim
    link = call("POST", "/api/sim/phone/trv-persona-plus/link", {"claim_id": thread["messages"][0]["claim_id"]})[1]["url"]
    assert "/c/" in link and "••••" not in link
    assert call("POST", "/api/sim/phone/trv-persona-plus/link", {"claim_id": personas[1]["claim_id"]})[0] == 404  # not theirs

    status, body = call("POST", "/api/sim/phone/trv-persona-plus", {"text": "호텔비도 보상되나요?"})
    assert status == 202 and body["channel"] == "sms"
    thread = call("GET", "/api/sim/phone/trv-persona-plus")[1]
    assert [m["direction"] for m in thread["messages"]][:2] == ["out", "in"]
    assert thread["messages"][-1]["direction"] == "out"  # the agent answered on SMS, through the router


def test_sim_phone_is_for_admins_and_off_in_production(sim):
    call("POST", "/api/demo/personas")
    assert call("GET", "/api/sim/phone", user="reviewer-1@demo")[0] == 403
    assert call("POST", "/api/sim/phone/trv-persona-plus", {"text": "hi"}, user="agent@demo")[0] == 403
    svc = sim["svc"]
    svc.s = dataclasses.replace(svc.s, sim_phone=False, channels="live")
    assert call("GET", "/api/sim/phone")[0] == 404
    assert call("GET", "/api/me")[1]["sim_phone"] is False


def test_personas_have_no_phone_without_the_simulator(env):
    api.SERVICE = env["svc"]
    try:
        call("POST", "/api/demo/personas")
        assert not env["svc"].repo.get_traveler("trv-persona-plus").get("phone")
    finally:
        api.SERVICE = None


def test_demo_email_goes_to_the_english_and_korean_demo_travelers_only(env):
    env["svc"].s = dataclasses.replace(env["svc"].s, demo_email="judge@example.com")
    api.SERVICE = env["svc"]
    try:
        call("POST", "/api/demo/personas")
        jordan = env["svc"].repo.get_traveler("trv-persona-jordan")
        assert jordan["email"] == "judge@example.com" and jordan["consent"]["email"] is True
        assert env["svc"].repo.get_traveler("trv-persona-plus")["email"] == "judge@example.com"  # 김지수, the Korean demo
        assert not env["svc"].repo.get_traveler("trv-persona-premier").get("email")
    finally:
        api.SERVICE = None


def test_decision_notice_goes_by_email_first_and_personas_prefer_different_channels(sim):
    svc = sim["svc"]
    call("POST", "/api/demo/personas")
    prefs = {svc.repo.get_traveler(f"trv-persona-{k}")["preferred_channel"] for k in ("plus", "premier", "jordan")}
    assert prefs == {"sms", "rcs"}
    t = {"id": "x", "phone": "+12025550199", "email": "a@example.com", "preferred_channel": "rcs",
         "consent": {"sms": True, "email": True}}
    assert svc.router.candidates(t, "decision")[0] == ("email", "documents and formal notices go by email")
    assert svc.router.candidates(t, "status")[0][0] == "rcs"  # ordinary updates follow the preference


def test_phone_simulator_never_shows_a_real_number(sim):
    svc = sim["svc"]
    call("POST", "/api/demo/personas")
    svc.repo.put_traveler({"id": "trv-demo", "name": "Real", "phone": "+821012345678", "consent": {"sms": True}})
    svc._new_claim(svc.repo.get_traveler("trv-demo"), None)
    phones = [p["phone"] for p in call("GET", "/api/sim/phone")[1]["phones"]]
    assert phones and all(p.startswith("+120255501") for p in phones)
    assert call("GET", "/api/sim/phone/trv-demo")[0] == 404


def test_demo_reset_removes_demo_travelers_only_and_is_audited(sim):
    svc = sim["svc"]
    personas = call("POST", "/api/demo/personas")[1]["personas"]
    ev = svc.repo.get_event(svc.repo.get_claim(personas[0]["claim_id"])["event_id"])
    svc.on_disruption({k: v for k, v in ev.items() if k not in ("id", "received_at")})
    svc.repo.put_traveler({"id": "trv-real", "name": "Customer", "phone": "+15550000000", "consent": {"sms": True}})
    keep = svc._new_claim(svc.repo.get_traveler("trv-real"), None)["id"]
    assert call("POST", "/api/demo/reset", user="reviewer-1@demo")[0] == 403
    status, body = call("POST", "/api/demo/reset")
    assert status == 200 and body["removed"]["travelers"] == 7 and body["removed"]["claims"] == 7
    left = svc.repo.list_claims()
    assert keep in [c["id"] for c in left] and not any(c["traveler_id"].startswith("trv-persona-") for c in left)
    assert svc.repo.get_traveler("trv-persona-jordan") is None and svc.repo.find_traveler("sms", "+12025550106") is None
    still_booked = svc.repo.travelers_on_flight("XX123", ev["date"])  # a real traveler on the flight keeps its events
    assert bool(svc.repo.events_for_flight("XX123", ev["date"])) == bool(still_booked)
    from datetime import datetime, timezone
    assert any(a["action"] == "demo_reset" for a in svc.repo.audits(datetime.now(timezone.utc).date().isoformat()))
    assert call("POST", "/api/demo/personas")[0] == 200  # and the demo can start again


def test_demo_reset_is_off_in_production(sim):
    svc = sim["svc"]
    svc.s = dataclasses.replace(svc.s, sim_phone=False, channels="live")
    assert call("POST", "/api/demo/reset")[0] == 404


def test_demo_personas_fly_on_the_date_given(sim):
    svc = sim["svc"]
    p = call("POST", "/api/demo/personas", {"date": "2026-10-19"})[1]["personas"][0]
    assert svc.repo.get_claim(p["claim_id"])["flight_date"] == "2026-10-19"
    assert call("POST", "/api/demo/personas", {"date": "19/10"})[0] == 400


def test_the_phone_mailbox_shows_email_to_the_traveler_not_the_packet(sim):
    svc = sim["svc"]
    svc.s = dataclasses.replace(svc.s, demo_email="judge@example.com")
    p = next(x for x in call("POST", "/api/demo/personas")[1]["personas"] if x["name"] == "Jordan Taylor")
    svc.router.send(svc.repo.get_traveler("trv-persona-jordan"), p["claim_id"], "decision", "Decision notice",
                    email_subject="Your claim decision")
    svc.repo.add_message(p["claim_id"], {"direction": "out", "channel": "email", "kind": "packet", "to": "reviewer",
                                         "subject": "[ClausePath] packet", "text": "packet", "provider_id": "x"})
    box = call("GET", "/api/sim/phone/trv-persona-jordan/mail")[1]
    assert [m["subject"] for m in box["mail"]] == ["Your claim decision"]  # the insurer's packet is not the traveler's mail
    assert box["email"] == "j•••@example.com"  # the demo inbox is a real address: never shown whole on screen
    assert box["mail"][0]["via"] == "local (not sent)" and box["mail"][0]["message_id"] is None  # fake channels say so
    url = call("POST", "/api/sim/phone/trv-persona-jordan/link", {"claim_id": p["claim_id"], "kind": "decision"})[1]["url"]
    assert url.endswith("/decision")
