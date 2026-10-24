"""Web chat reached from the SMS link: the link is the credential, scoped to one claim, stored only as a hash."""
import base64
import json
from datetime import datetime, timezone

import pytest

from tripclaim.handlers import api

from .conftest import EVENT


@pytest.fixture
def chat(env, monkeypatch):
    # as in the live demo: SMS only (WhatsApp templates are fixed text and carry no link)
    env["svc"].repo.update_traveler("trv-1", preferred_channel="sms", consent={"sms": True, "email": True})
    api.SERVICE = env["svc"]
    monkeypatch.setattr(api, "process_web_async", api.handle_web_inbound)  # run the turn inline in tests
    yield env
    api.SERVICE = None


def call(method, path, body=None):
    from urllib.parse import parse_qsl, urlsplit
    url = urlsplit(path)
    res = api.handler({"requestContext": {"http": {"method": method}}, "rawPath": url.path,
                       "queryStringParameters": dict(parse_qsl(url.query)),
                       "body": json.dumps(body) if body is not None else None}, None)
    is_json = res["headers"]["content-type"] == "application/json"
    return res["statusCode"], (json.loads(res["body"]) if is_json else res["body"]), res["headers"]


def test_sms_link_opens_chat_and_agent_answers_there(chat):
    svc, outbox = chat["svc"], chat["outbox"]
    [claim_id] = svc.on_disruption(dict(EVENT))
    link = outbox[-1]["text"].split("https://demo.test")[1].split()[0]   # alert carries the chat link
    assert link.startswith("/c/")

    status, page, headers = call("GET", link)
    assert status == 200 and "ClausePath" in page and headers["referrer-policy"] == "no-referrer"

    assert call("POST", link + "/send", {"text": "Is my hotel covered?", "client_id": "a1"})[0] == 202
    status, data, _ = call("GET", link + "/messages")
    last = data["messages"][-1]
    assert last["direction"] == "out" and last["channel"] == "web" and "TD-3" in last["text"]
    assert [m["channel"] for m in svc.repo.messages(claim_id) if m["direction"] == "in"] == ["web"]


def test_photo_upload_becomes_document(chat):
    svc, outbox = chat["svc"], chat["outbox"]
    [claim_id] = svc.on_disruption(dict(EVENT))
    link = outbox[-1]["text"].split("https://demo.test")[1].split()[0]
    image = {"data": base64.b64encode(b"HOTEL").decode(), "mime": "image/jpeg", "filename": "hotel.jpg"}
    assert call("POST", link + "/send", {"text": "", "image": image, "client_id": "p1"})[0] == 202
    [doc] = svc.repo.documents(claim_id)
    assert doc["extracted"]["doc_type"] == "receipt" and doc["source_channel"] == "web"
    assert svc.repo.get_claim(claim_id)["checklist"]["expense_receipts"]


def test_link_scope_and_storage(chat):
    svc, outbox = chat["svc"], chat["outbox"]
    [claim_id] = svc.on_disruption(dict(EVENT))
    chat_token = outbox[-1]["text"].split("/c/")[1].split()[0]
    secure_token = svc.secure_link(claim_id).rsplit("/", 1)[1]
    assert call("GET", f"/c/{secure_token}")[0] == 404          # a secure-page link cannot open the chat
    assert call("GET", "/c/not-a-real-token")[0] == 404
    stored = json.dumps(list(svc.repo.db.items.values()))
    assert chat_token not in stored and secure_token not in stored  # only hashes are kept


def test_secure_link_in_chat_is_masked_and_minted_on_tap(chat):
    svc, outbox = chat["svc"], chat["outbox"]
    svc.on_disruption(dict(EVENT))
    link = outbox[-1]["text"].split("https://demo.test")[1].split()[0]
    call("POST", link + "/send", {"text": "my bank account is 123", "client_id": "b1"})
    _, data, _ = call("GET", link + "/messages")
    reply = data["messages"][-1]["text"]
    assert "/secure/••••" in reply                                  # what is stored and shown is masked
    res = api.handler({"requestContext": {"http": {"method": "GET"}}, "rawPath": link + "/secure"}, None)
    assert res["statusCode"] == 302
    status, page, _ = call("GET", res["headers"]["location"].split("https://demo.test")[1])
    assert status == 200 and "Secure claims page" in page


def test_agent_attaches_numbered_sources_and_folds_them(chat):
    svc, outbox = chat["svc"], chat["outbox"]
    svc.repo.update_traveler("trv-1", locale="ko", locale_source="explicit")
    svc.on_disruption(dict(EVENT))
    link = outbox[-1]["text"].split("https://demo.test")[1].split()[0]

    call("POST", link + "/send", {"text": "오늘 밤 호텔비도 보상되나요?", "client_id": "e1"})
    data = call("GET", link + "/messages")[1]
    reply = data["messages"][-1]
    [src] = reply["ev"]                                   # one source card under the reply, numbered [1]
    assert src["n"] == 1 and "[1]" in reply["text"] and src["chunk"].startswith(("faq_ko#", "policy_ko#"))
    page = data["evidence"][src["chunk"]]["pages"][0]     # passage details sent once, keyed by id
    assert page["image"].startswith("/docs/") and all(0 <= v <= 1 for r in page["rects"] for v in r)
    status, _, headers = call("GET", page["image"])
    assert status == 200 and headers["content-type"] == "image/jpeg" and "immutable" in headers["cache-control"]

    image = {"data": base64.b64encode(b"HOTEL").decode(), "mime": "image/jpeg", "filename": "h.jpg"}
    call("POST", link + "/send", {"text": "", "image": image, "client_id": "e2"})
    data = call("GET", link + "/messages")[1]
    assert data["messages"][-1].get("fold") and "ev" not in data["messages"][-1]  # agent folded the open card
    assert list(data["evidence"]) == [src["chunk"]]                                  # earlier card still resolvable

    svc.evidence_panel(None, "close")  # outside a turn: no effect, no error
    assert svc.evidence_panel("x", "show", "no-such-chunk")["ok"] is False


def test_rag_keyword_fallback_finds_right_passages():
    from tripclaim import rag
    assert rag.search("공항에서 확인서를 못 받았어요", "ko", 1, kinds={"faq"})[0]["id"] == "faq_ko#13"
    assert rag.search("When will I get paid? CL-4", "en", 1, kinds={"policy"})[0]["clause"] == "CL-4"


def test_demo_personas_get_their_own_chats(chat):
    svc = chat["svc"]
    status, data, _ = call("POST", "/api/demo/personas")
    personas = data["personas"]
    assert status == 200 and {p["plan"] for p in personas} == {"Plus", "Premier", "Basic"} and len(personas) == 7
    basic = next(p for p in personas if p["plan"] == "Basic")
    link = basic["chat"].split("https://demo.test")[1]
    assert call("GET", link)[0] == 200
    call("POST", link + "/send", {"text": "호텔비 보상되나요?", "client_id": "pb"})
    case = svc.coverage_case(svc.repo.get_claim(basic["claim_id"]), "x")
    assert case["traveler"]["plan"] == "Basic"   # the coverage tool sees this persona's policy, not the others'


def test_concurrent_turns_do_not_mix(chat):
    import threading
    svc = chat["svc"]
    personas = call("POST", "/api/demo/personas")[1]["personas"]
    links = [p["chat"].split("https://demo.test")[1] for p in personas]
    threads = [threading.Thread(target=call, args=("POST", l + "/send", {"text": "호텔 보상되나요?", "client_id": f"c{i}"}))
               for i, l in enumerate(links)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    for p, link in zip(personas, links):
        msgs = call("GET", link + "/messages")[1]
        assert msgs["messages"][-1]["ev"]                                     # each chat got its own source card
        assert [m["direction"] for m in msgs["messages"]].count("out") == 1  # its own reply only, nothing from others


def test_tools_on_worker_threads_still_reach_the_turn(chat):
    """Strands runs tools on worker threads; panel actions and progress messages must still land on the turn."""
    import threading
    from tripclaim.agent import ScriptedAgent
    svc = chat["svc"]

    class ThreadedTools(ScriptedAgent):
        def run(self, service, claim_id, text, context):
            out = {}
            t = threading.Thread(target=lambda: out.update(r=super(ThreadedTools, self).run(service, claim_id, text, context)))
            t.start(); t.join()
            return out["r"]
    svc.agent = ThreadedTools()
    persona = call("POST", "/api/demo/personas")[1]["personas"][0]
    link = persona["chat"].split("https://demo.test")[1]
    call("POST", link + "/send", {"text": "호텔 보상되나요?", "client_id": "w1"})
    msgs = call("GET", link + "/messages")[1]
    reply = msgs["messages"][-1]
    assert reply["ev"] and [s["kind"] for s in reply["steps"]][:2] == ["search", "found"]  # trail recorded from tool threads
    assert msgs["activity"] is None                                                       # cleared once the reply is in


def test_same_client_id_in_two_chats_is_not_a_duplicate(chat):
    ps = call("POST", "/api/demo/personas")[1]["personas"]
    links = [p["chat"].split("https://demo.test")[1] for p in ps[:2]]
    for link in links:
        call("POST", link + "/send", {"text": "호텔 보상되나요?", "client_id": "same"})
    for link in links:
        assert any(m["direction"] == "in" for m in call("GET", link + "/messages")[1]["messages"])


def test_sources_are_numbered_and_deduplicated(chat):
    svc = chat["svc"]
    ps = call("POST", "/api/demo/personas")[1]["personas"]
    claim_id = ps[0]["claim_id"]
    with svc._turns_lock:
        svc._turns[claim_id] = turn = {"claim_id": claim_id, "channel": "web", "subject": None, "acked": False, "ui": None, "steps": []}
    assert svc.evidence_panel(claim_id, "show", "policy_ko#TD-3", "a")["ref"] == 1
    assert svc.evidence_panel(claim_id, "show", "policy_ko#PLAN-2", "b")["ref"] == 2
    assert svc.evidence_panel(claim_id, "show", "policy_ko#TD-3", "a")["ref"] == 1   # same passage, same number
    svc.evidence_panel(claim_id, "show", "policy_ko#TD-6", "c")
    assert svc.evidence_panel(claim_id, "show", "policy_ko#EX-3", "d")["ok"] is False  # at most three
    assert [x["n"] for x in turn["ui"]["sources"]] == [1, 2, 3]


def test_sources_show_while_the_reply_is_written_and_captions_pass_the_wording_check(chat):
    svc = chat["svc"]
    p = call("POST", "/api/demo/personas")[1]["personas"][0]
    claim_id, link = p["claim_id"], p["chat"].split("https://demo.test")[1]
    with svc._turns_lock:
        svc._turns[claim_id] = {"claim_id": claim_id, "channel": "web", "subject": None, "acked": False, "ui": None, "steps": []}
    svc.evidence_panel(claim_id, "show", "policy_ko#TD-3", "호텔비는 (b)항에 해당해요")
    svc.evidence_panel(claim_id, "show", "policy_ko#PLAN-2", "호텔비 150달러는 보상됩니다")  # a promise: caption dropped
    d = call("GET", link + "/messages")[1]
    assert [(x["n"], x["caption"]) for x in d["activity"]["sources"]] == [(1, "호텔비는 (b)항에 해당해요"), (2, None)]
    assert {"policy_ko#TD-3", "policy_ko#PLAN-2"} <= set(d["evidence"])  # the card can draw before the reply exists
    assert any(a["action"] == "agent_wording_blocked" and a["after"]["where"] == "caption" for a in svc.repo.audits(datetime.now(timezone.utc).date().isoformat()))


def test_several_photos_in_one_message(chat):
    svc = chat["svc"]
    p = call("POST", "/api/demo/personas")[1]["personas"][0]
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"LUNCH")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "m1"})
    msgs = call("GET", link + "/messages")[1]["messages"]
    ins = [m for m in msgs if m["direction"] == "in"]
    assert len(ins) == 1 and len(ins[0]["media"]) == 3
    assert [m["direction"] for m in msgs].count("out") == 1  # one reply for the batch


def test_clause_atlas_links_clauses_by_type():
    status, atlas, headers = call("GET", "/docs/atlas-ko.json?v=" + __import__("tripclaim.rag").rag.version())
    assert status == 200 and "immutable" in headers["cache-control"]
    edges = {(a.split("#")[1], b.split("#")[1], t) for a, b, t in atlas["edges"]}
    assert ("EX-3", "TD-6", "contrast") in edges and ("TD-6", "DEF-2", "extends") in edges
    assert ("TD-1", "PLAN-2", "limit") in edges                        # authored relation (also an explicit mention)
    assert any(t == "explains" for _, _, t in edges)                   # FAQ answers point at clauses
    ids = {n["id"] for n in atlas["nodes"]}
    assert all(a in ids and b in ids for a, b, _ in atlas["edges"])
    assert atlas["types"]["excludes"]["label"] == "제외"


def _as(groups):
    return {"authorizer": {"jwt": {"claims": {"email": "staff@example.com", "cognito:groups": groups}}}}


def test_staff_roles_limit_risky_actions(chat):
    svc = chat["svc"]
    claim_id = call("POST", "/api/demo/personas")[1]["personas"][0]["claim_id"]

    def staff(groups, method, path, body):
        res = api.handler({"requestContext": {"http": {"method": method}, **_as(groups)}, "rawPath": path,
                           "body": json.dumps(body)}, None)
        return res["statusCode"], json.loads(res["body"])
    assert staff("[agents]", "POST", f"/api/claims/{claim_id}/actions", {"action": "approve"})[0] == 403
    assert staff("[agents]", "POST", f"/api/claims/{claim_id}/actions", {"action": "note", "note": "called back"})[0] == 200
    assert staff("[reviewers]", "POST", "/api/events", {})[0] == 403          # real SMS to every traveler: admins only
    assert staff("[reviewers]", "POST", f"/api/claims/{claim_id}/actions", {"action": "request_info", "note": "boarding pass"})[0] == 200
    status, me = staff("[admins reviewers]", "GET", "/api/me", {})
    assert status == 200 and "disrupt" in me["can"] and me["roles"] == ["admins", "reviewers"]
    assert staff("", "GET", "/api/me", {})[1]["roles"] == []                  # signed in without a group: no rights at all
    assert staff("[guests]", "POST", f"/api/claims/{claim_id}/actions", {"action": "note", "note": "x"})[0] == 403


def jordan():  # the fake documents in conftest are in Jordan Taylor's name
    return next(p for p in call("POST", "/api/demo/personas")[1]["personas"] if p["name"] == "Jordan Taylor")


def test_traveler_sees_checklist_and_submits_from_chat(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "s1"})
    data = call("GET", link + "/messages")[1]
    items = {i["key"]: i for i in data["items"]}
    assert items["airline_letter"]["done"] and not items["photo_id"]["done"] and items["photo_id"]["secure"]
    svc.repo.update_claim(p["claim_id"], secure_submitted=True)
    res = call("POST", link + "/submit")[1]
    assert res["submitted"] is True
    assert call("GET", link + "/messages")[1]["status"] == "submitted"


def test_every_clause_has_a_link_on_the_map():
    from tripclaim import rag
    for lang in ("en", "ko"):
        atlas = rag.atlas(lang)
        linked = {x for a, b, _ in atlas["edges"] for x in (a, b)}
        orphans = [n["clause"] for n in atlas["nodes"] if n["kind"] == "policy" and n["id"] not in linked]
        assert orphans == [], (lang, orphans)


def test_submitting_from_the_chat_confirms_in_the_chat(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "s1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    web = [m["text"] for m in call("GET", link + "/messages")[1]["messages"] if m["direction"] == "out"]
    assert any(p["claim_id"] in t for t in web)   # the receipt shows up where the traveler pressed the button


def test_someone_elses_letter_needs_a_check_not_a_tick(chat):
    svc = chat["svc"]
    p = call("POST", "/api/demo/personas")[1]["personas"][0]          # 김지수, while the letter says TAYLOR/JORDAN
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(b"LETTER").decode(), "mime": "image/jpeg", "filename": "x.jpg"}]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "n1"})
    items = {i["key"]: i for i in call("GET", link + "/messages")[1]["items"]}
    assert not items["airline_letter"]["done"] and items["airline_letter"]["check"] == 1
    assert not items["original_eticket"]["done"]                       # derived from the same letter: not done either


def test_approving_with_open_checks_needs_a_reason(chat):
    svc = chat["svc"]
    p = call("POST", "/api/demo/personas")[1]["personas"][0]          # someone else's letter -> open check
    link = p["chat"].split("https://demo.test")[1]
    call("POST", link + "/send", {"text": "", "images": [{"data": base64.b64encode(b"LETTER").decode(), "mime": "image/jpeg"}], "client_id": "a1"})
    status, body, _ = call("POST", f"/api/claims/{p['claim_id']}/actions", {"action": "approve"})
    assert status == 409 and "check:airline_letter" in body["unresolved"]
    assert call("POST", f"/api/claims/{p['claim_id']}/actions", {"action": "approve", "reason": "airline confirmed by phone"})[0] == 200
    sent = [m["text"] for m in svc.repo.messages(p["claim_id"]) if m["direction"] == "out"]
    assert not any("airline confirmed by phone" in t for t in sent)    # the reason is for the audit trail only


def test_someone_elses_boarding_pass_needs_a_check(chat):
    p = call("POST", "/api/demo/personas")[1]["personas"][0]          # 김지수; the boarding pass says TAYLOR/JORDAN
    link = p["chat"].split("https://demo.test")[1]
    call("POST", link + "/send", {"text": "", "images": [{"data": base64.b64encode(b"BOARDING").decode(), "mime": "image/jpeg"}], "client_id": "b1"})
    items = {i["key"]: i for i in call("GET", link + "/messages")[1]["items"]}
    assert not items["replacement_boarding_pass"]["done"] and items["replacement_boarding_pass"]["check"] == 1


def test_estimate_converts_and_caps():
    from tripclaim.estimate import estimate
    hotel = {"id": "h", "flags": [], "extracted": {"doc_type": "receipt", "receipt": {"merchant": "Hotel", "currency": "JPY", "total": 30000,
             "datetime": "2026-10-05 22:00", "service_date": None, "line_items": [{"description": "room", "category": "accommodation", "amount": 30000}]}}}
    blurry = {"id": "b", "flags": [{"code": "retake", "detail": ""}], "extracted": {"doc_type": "receipt", "receipt": {"currency": "JPY", "total": 5000,
              "datetime": "2026-10-05", "line_items": [{"description": "x", "category": "meal", "amount": 5000}]}}}
    est = estimate([hotel, blurry], "Plus")
    assert est["receipts_usd"] == 200.0 and est["estimate_usd"] == 150 and est["remaining_usd"] == 350   # capped at USD 150/day
    assert est["rows"][1]["excluded"] == ["retake"] and estimate([hotel], "Basic")["covered"] is False


def test_a_document_from_another_claim_on_the_flight_is_flagged(chat):
    svc = chat["svc"]
    ps = {p["name"]: p for p in call("POST", "/api/demo/personas")[1]["personas"]}
    jordan, jisoo = ps["Jordan Taylor"], ps["김지수"]
    letter = [{"data": base64.b64encode(b"LETTER").decode(), "mime": "image/jpeg"}]   # Jordan's letter (TAYLOR/JORDAN)
    call("POST", jordan["chat"].split("https://demo.test")[1] + "/send", {"text": "", "images": letter, "client_id": "j"})
    call("POST", jisoo["chat"].split("https://demo.test")[1] + "/send", {"text": "", "images": letter, "client_id": "k"})
    flags = svc.repo.get_claim(jisoo["claim_id"])["flags"]
    hit = [f for f in flags if f["code"] == "other_claim_document"]
    assert hit and jordan["claim_id"] in hit[0]["detail"]                              # same photo / Jordan's name
    assert "check:airline_letter" in svc.unresolved(jisoo["claim_id"])                 # counted once, as a document to check
    claim, docs, state = svc.recheck(jisoo["claim_id"])
    traveler = svc.repo.get_traveler(claim["traveler_id"])
    seen = json.dumps(svc.turn_context(claim, traveler, state, [d["id"] for d in docs]), ensure_ascii=False)
    assert jordan["claim_id"] not in seen and "Jordan" not in seen                    # the traveler never hears whose it is
    call("POST", jordan["chat"].split("https://demo.test")[1] + "/send", {"text": "hotel?", "client_id": "j2"})  # Jordan rechecked later
    assert not [f for f in svc.repo.get_claim(jordan["claim_id"])["flags"] if f["code"] == "other_claim_document"]   # first upload keeps it


def test_model_outage_gets_an_honest_reply_not_a_hang(chat):
    svc = chat["svc"]

    class Down:
        def run(self, *a):
            raise RuntimeError("ServiceUnavailable")
    svc.agent = Down()
    p = call("POST", "/api/demo/personas")[1]["personas"][0]
    link = p["chat"].split("https://demo.test")[1]
    call("POST", link + "/send", {"text": "호텔비 되나요?", "client_id": "x"})
    data = call("GET", link + "/messages")[1]
    assert data["messages"][-1]["direction"] == "out" and "02-000-0000" in data["messages"][-1]["text"]
    assert data["activity"] is None


def test_chat_strings_have_the_same_keys_in_both_languages():
    """A key missing in one language breaks the page for those travelers (e.g. a comment swallowing a key)."""
    import pathlib
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    html = (pathlib.Path(api.__file__).parent.parent / "web" / "chat.html").read_text()
    start = html.index("const T = {")
    end = html.index("\n}[CFG.locale]", start)  # the object literal, before it is narrowed to one language
    script = html[start:end] + "\n};\nconst [en, ko] = [T.en, T.ko].map(o => Object.keys(o).sort());" \
        "\nconsole.log(JSON.stringify({en, ko}));"
    keys = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    assert set(keys["en"]) ^ set(keys["ko"]) == set()


def test_due_date_skips_weekends(env):
    from tripclaim.service import business_days_after
    assert business_days_after("2026-10-09T10:00:00+00:00", 3) == "2026-10-14"   # Fri -> Wed
    svc = env["svc"]
    claim = {"submitted_at": "2026-10-09T10:00:00+00:00", "status": "submitted", "needs_review": True}
    assert svc.due(claim) == {"kind": "check", "date": "2026-10-23"}
    import dataclasses
    svc.s = dataclasses.replace(svc.s, due_dates=False)                              # mass event: no dates promised
    assert svc.due(claim) is None


def test_contact_centre_can_resend_and_correct_the_account(chat):
    """An agent fixes "I typed the wrong account" without a reviewer: identity check noted, old account void until re-entered."""
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "c1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")

    def agent(body):
        res = api.handler({"requestContext": {"http": {"method": "POST"}, **_as("[agents]")},
                           "rawPath": f"/api/claims/{p['claim_id']}/actions", "body": json.dumps(body)}, None)
        return res["statusCode"]
    assert agent({"action": "bank_correction"}) == 409                                 # who is calling? say how it was checked
    assert agent({"action": "message", "note": "Your claim is with the reviewer."}) == 200
    assert agent({"action": "resend_link", "checks": ["dob"]}) == 200
    assert agent({"action": "bank_correction", "checks": ["dob"], "reason": "ok"}) == 409             # free text does not count
    assert agent({"action": "bank_correction", "checks": ["dob", "policy"]}) == 200
    claim = svc.repo.get_claim(p["claim_id"])
    assert claim["claim_form"]["account_last4"] is None and "bank_pending" in svc.unresolved(p["claim_id"])
    sent = len(chat["outbox"])
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"holder": "Kim", "country": "kr", "bank_name": "신한은행", "account": "110-222-333444"})
    form = svc.repo.get_claim(p["claim_id"])["claim_form"]
    assert form["account_last4"] == "3444" and form["previous"]["account_last4"] == "7890" and form["signed_name"] == "Kim"
    assert "bank_pending" not in svc.unresolved(p["claim_id"])
    assert any("payout account changed" in str(o) for o in chat["outbox"][sent:])        # the claims inbox hears about it
    web = [m["text"] for m in call("GET", link + "/messages")[1]["messages"] if m["direction"] == "out"]
    assert any("Your claim is with the reviewer." in t for t in web) and any("3444" in t for t in web)


def test_reviewer_decides_full_partial_or_deny(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "d1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid = p["claim_id"]

    def decide(body):
        res = api.handler({"requestContext": {"http": {"method": "POST"}, **_as("[reviewers]")},
                           "rawPath": f"/api/claims/{cid}/actions", "body": json.dumps(body)}, None)
        return res["statusCode"]
    est = svc.claim_view(cid)["estimate"]["estimate_usd"]
    assert est > 0
    assert decide({"action": "approve", "amount_usd": est - 10, "reason": "ok"}) == 400            # partial: why, under which clause?
    assert decide({"action": "approve", "amount_usd": est - 10, "clauses": ["TD-4"], "note": "Minibar is not covered.", "reason": "ok"}) == 200
    assert svc.repo.get_claim(cid)["pending_decision"]["type"] == "partial"                       # four eyes on partial payments
    svc.confirm_decision(cid, "second@example.com", True)
    claim = svc.repo.get_claim(cid)
    assert claim["status"] == "approved" and claim["decision"]["type"] == "partial" and claim["decision"]["amount_usd"] == round(est - 10, 2)
    data = call("GET", link + "/messages")[1]
    assert data["decision"]["type"] == "partial"
    last = [m["text"] for m in data["messages"] if m["direction"] == "out"][-1]
    assert "Minibar is not covered." in last and "1332" in last and " by " in last                 # why, by when, and where else to object
    assert decide({"action": "deny", "clauses": ["EX-1"], "note": "x"}) == 409                       # final: only a correction
    assert decide({"action": "correct", "deny": True, "clauses": ["EX-1"], "note": "x"}) == 400      # a correction says why
    assert decide({"action": "correct", "deny": True, "note": "x", "reason": "new evidence"}) == 400  # no clause
    assert decide({"action": "correct", "deny": True, "clauses": ["EX-1"], "note": "Known before purchase.", "reason": "new evidence"}) == 200
    claim = svc.repo.get_claim(cid)
    assert claim["status"] == "approved" and claim["pending_decision"]["type"] == "deny"           # four eyes: waits, nothing sent
    assert decide({"action": "confirm"}) == 409                                                     # not by the same person
    from tripclaim.service import SecondPersonRequired
    svc.confirm_decision(cid, "second@example.com", True)
    claim = svc.repo.get_claim(cid)
    assert claim["status"] == "denied" and claim["decision"]["confirmed_by"] == "second@example.com" and not claim["pending_decision"]
    assert claim["decision"]["corrects"]["type"] == "partial"                                       # what it replaced is kept
    assert svc.due(claim) is None


def test_bulk_sends_are_previewed_with_count_and_cost(chat):
    svc = chat["svc"]
    from tripclaim.service import sms_parts
    assert sms_parts("a" * 160) == 1 and sms_parts("가" * 71) == 2                       # Hangul: 70 chars a part
    sent = len(chat["outbox"])
    p = svc.preview_disruption(dict(EVENT))
    assert len(chat["outbox"]) == sent                                                   # nothing went out
    assert p["count"] >= 1 and sum(p["by_channel"].values()) == p["count"] and p["cost_usd"] >= 0 and p["samples"]
    res = api.handler({"requestContext": {"http": {"method": "POST"}, **_as("[reviewers]")}, "rawPath": "/api/events/preview",
                       "body": json.dumps({"flight": "XX123", "date": "2026-10-05", "type": "cancelled"})}, None)
    assert res["statusCode"] == 403                                                      # admins only, like the send


def test_audit_log_records_who_opened_and_did_what(chat):
    claim_id = call("POST", "/api/demo/personas")[1]["personas"][0]["claim_id"]

    def staff(groups, method, path, body=None):
        from urllib.parse import parse_qsl, urlsplit
        u = urlsplit(path)
        res = api.handler({"requestContext": {"http": {"method": method}, **_as(groups)}, "rawPath": u.path,
                           "queryStringParameters": dict(parse_qsl(u.query)), "body": json.dumps(body or {})}, None)
        return res["statusCode"], json.loads(res["body"])
    staff("[agents]", "GET", f"/api/claims/{claim_id}?open=1")                      # a person opened it
    staff("[agents]", "GET", f"/api/claims/{claim_id}")                             # the 5-second refresh: not logged
    staff("[agents]", "POST", f"/api/claims/{claim_id}/actions", {"action": "note", "note": "called"})
    assert staff("[agents]", "GET", "/api/audit")[0] == 403
    assert staff("[agents]", "POST", f"/api/claims/{claim_id}/actions", {"action": "disrupt"})[0] == 403  # not a claim action
    status, log = staff("[admins]", "GET", "/api/audit")
    mine = [e for e in log["entries"] if e.get("target") == claim_id]
    assert status == 200 and [e["action"] for e in mine].count("view_claim") == 1
    assert any(e["action"] == "reviewer_note" and e["actor"] == "staff@example.com" for e in mine)


def test_big_bulk_send_waits_for_a_second_admin(chat):
    import dataclasses
    svc = chat["svc"]
    svc.s = dataclasses.replace(svc.s, bulk_second_admin_over=0)

    def as_user(user, method, path, body=None):
        res = api.handler({"requestContext": {"http": {"method": method}}, "headers": {"x-demo-user": user},
                           "rawPath": path, "body": json.dumps(body or {})}, None)
        return res["statusCode"], json.loads(res["body"])
    sent = len(chat["outbox"])
    status, r = as_user("admin@demo", "POST", "/api/events", dict(EVENT))
    assert status == 202 and len(chat["outbox"]) == sent                                      # queued, nothing sent
    bid = r["pending"]["id"]
    assert as_user("admin@demo", "POST", f"/api/bulk/{bid}/confirm")[0] == 409           # not by the requester
    assert as_user("agent@demo", "GET", "/api/bulk")[0] == 403
    status, r = as_user("admin2@nowhere", "POST", f"/api/bulk/{bid}/confirm")                   # unknown demo user = local all-roles
    assert status == 200 and r["alerted"] and len(chat["outbox"]) > sent


def test_kpis_count_claims_and_flag_banned_wording(chat):
    svc = chat["svc"]
    p = call("POST", "/api/demo/personas")[1]["personas"][0]
    svc.repo.add_message(p["claim_id"], {"direction": "out", "channel": "web", "kind": "text", "purpose": "reply",
                                         "text": "호텔비는 보상될 가능성이 높아요."})
    k = svc.kpi()
    assert sum(e["claims"] for e in k["events"]) >= 1
    assert any(b["claim"] == p["claim_id"] and "가능성이 높" in b["phrase"] for b in k["banned"])


def test_reviewer_sees_the_original_photo_and_it_is_logged(chat):
    svc = chat["svc"]
    ps = {p["name"]: p for p in call("POST", "/api/demo/personas")[1]["personas"]}
    jordan_, jisoo = ps["Jordan Taylor"], ps["김지수"]
    letter = [{"data": base64.b64encode(b"LETTER").decode(), "mime": "image/jpeg"}]
    call("POST", jordan_["chat"].split("https://demo.test")[1] + "/send", {"text": "", "images": letter, "client_id": "j"})
    call("POST", jisoo["chat"].split("https://demo.test")[1] + "/send", {"text": "", "images": letter, "client_id": "k"})
    doc = svc.claim_view(jisoo["claim_id"])["documents"][0]
    assert doc["twin"]["claim"] == jordan_["claim_id"]                                    # compare with the first upload

    def staff(groups, path):
        return api.handler({"requestContext": {"http": {"method": "GET"}, **_as(groups)}, "rawPath": path}, None)
    assert staff("[agents]", f"/api/claims/{jisoo['claim_id']}/docs/{doc['id']}")["statusCode"] == 403
    res = staff("[reviewers]", f"/api/claims/{jisoo['claim_id']}/docs/{doc['id']}")
    assert res["statusCode"] == 200 and base64.b64decode(res["body"]) == b"LETTER"
    assert any(a["action"] == "view_document" for a in svc.repo.audits(__import__("tripclaim.store").store.now_iso()[:10]))


def test_sent_photos_come_back_as_thumbnails(chat):
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(b"LETTER").decode(), "mime": "image/jpeg", "filename": "a.jpg"}]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "t1"})
    sent = [m for m in call("GET", link + "/messages")[1]["messages"] if m["direction"] == "in" and m.get("photos")]
    assert sent and sent[0]["photos"][0]["state"] in ("ok", "retake", "check")
    status, body, headers = call("GET", f"{link}/photo/{sent[0]['id']}/0")
    assert status == 200 and headers["content-type"].startswith("image/")
    assert call("GET", f"{link}/photo/{sent[0]['id']}/5")[0] == 404


def test_traveler_can_switch_language(chat):
    svc = chat["svc"]
    p = call("POST", "/api/demo/personas")[1]["personas"][0]          # Korean traveler
    link = p["chat"].split("https://demo.test")[1]
    assert call("POST", link + "/lang", {"locale": "xx"})[0] == 400
    assert call("POST", link + "/lang", {"locale": "en"})[0] == 200
    t = svc.repo.get_traveler(svc.repo.get_claim(p["claim_id"])["traveler_id"])
    assert t["locale"] == "en" and t["locale_source"] == "explicit"     # sticks: a Korean word later won't flip it back
    assert '"locale": "en"' in call("GET", link)[1]


@pytest.mark.parametrize("page", ["chat.html", "index.html"])
def test_page_scripts_parse(page, tmp_path):
    """A syntax error (e.g. a name declared twice) blanks the whole page; catch it here, not in a demo."""
    import pathlib
    import re
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    html = (pathlib.Path(api.__file__).parent.parent / "web" / page).read_text()
    for i, js in enumerate(re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", html, re.S)):
        f = tmp_path / f"{i}.js"
        f.write_text(js)
        res = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr


def test_no_side_door_around_four_eyes(chat):
    """The API cannot approve without an amount, or deny by approving $0, to skip the second reviewer."""
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "f1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid = p["claim_id"]
    import dataclasses
    svc.s = dataclasses.replace(svc.s, dual_approval_over_usd=1)                       # every amount needs a second look
    assert call("POST", f"/api/claims/{cid}/actions", {"action": "approve"})[0] == 200    # no amount: still decide()
    claim = svc.repo.get_claim(cid)
    assert claim["status"] != "approved" and claim["pending_decision"]["why"] == ["amount"]
    svc.repo.update_claim(cid, pending_decision=None)
    call("POST", f"/api/claims/{cid}/actions", {"action": "approve", "amount_usd": 0, "clauses": ["EX-1"], "note": "Known before purchase."})
    assert svc.repo.get_claim(cid)["pending_decision"]["type"] == "deny"                 # $0 is a denial, with its four eyes


def test_no_token_means_no_rights_outside_the_local_server(chat):
    import dataclasses
    svc = chat["svc"]
    assert call("GET", "/api/me")[1]["roles"] == ["admins", "agents", "reviewers"]       # local in-memory demo
    svc.s = dataclasses.replace(svc.s, store="dynamodb")
    assert call("GET", "/api/me")[1]["roles"] == []
    assert call("GET", "/api/audit")[0] == 403


def test_declared_other_compensation_is_an_open_check(chat):
    svc = chat["svc"]
    p = jordan()
    form = {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890",
            "other_insurance": "no", "airline_paid": "yes", "dup_detail": "meal voucher ¥1,500"}
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), form)
    assert "airline_paid_declared" in svc.unresolved(p["claim_id"])                    # settle the split before paying
    d = svc.repo.get_claim(p["claim_id"])["claim_form"]["declared"]
    assert d == {"other_insurance": False, "airline_paid": True, "detail": "meal voucher ¥1,500", "asked": True}
    page = api.secure_page("t", svc.repo.get_claim(p["claim_id"]), locale="ko")["body"]
    assert 'name="other_insurance"' in page and 'name="airline_paid"' in page


def test_receipt_level_deduction_sets_the_amount_and_is_recorded(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "r1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid = p["claim_id"]
    row = next(r for r in svc.claim_view(cid)["estimate"]["rows"] if r["counted_usd"])
    adjust = {row["doc"]: {"usd": row["counted_usd"] - 20, "reason": "excluded_item"}}
    preview = call("POST", f"/api/claims/{cid}/estimate", {"adjust": adjust})[1]
    assert round(preview["estimate_usd"], 2) == round(svc.claim_view(cid)["estimate"]["estimate_usd"] - 20, 2)
    call("POST", f"/api/claims/{cid}/actions", {"action": "approve", "adjust": adjust, "clauses": ["TD-4"], "note": "Minibar."})
    svc.confirm_decision(cid, "second@example.com", True)
    claim = svc.repo.get_claim(cid)
    line = claim["decision"]["lines"][0]
    assert line["reason"] == "excluded_item" and round(line["from"] - line["to"], 2) == 20
    assert "Paid in part" in claim["packet_html"] or "일부 지급" in claim["packet_html"]          # packet is the record now
    data = call("GET", link + "/messages")[1]
    assert data["decision"]["lines"] and data["decision"]["appeal_by"]


def test_deciding_before_submission_needs_a_reason(chat):
    p = jordan()                                                                         # nothing submitted yet
    path = f"/api/claims/{p['claim_id']}/actions"
    status, body, _ = call("POST", path, {"action": "deny", "clauses": ["EX-1"], "note": "Known before purchase."})
    assert status == 400 and body["error"] == "not_submitted"
    assert call("POST", path, {"action": "deny", "clauses": ["EX-1"], "note": "Known before purchase.", "reason": "SIU confirmed"})[0] == 200


def test_audit_copy_and_looking_at_the_log_is_logged(chat):
    svc = chat["svc"]
    copies = []
    svc.repo.audit_sink = copies.append                                                  # the Object Lock copy in AWS
    claim_id = call("POST", "/api/demo/personas")[1]["personas"][0]["claim_id"]
    call("POST", f"/api/claims/{claim_id}/actions", {"action": "note", "note": "called"})
    assert any(c["action"] == "reviewer_note" and c["target"] == claim_id for c in copies)
    call("GET", "/api/audit?tz=kst")
    assert copies[-1]["action"] == "view_audit"
    svc.repo.audit_sink = lambda e: 1 / 0                                                # a failed copy never blocks the work
    assert call("POST", f"/api/claims/{claim_id}/actions", {"action": "note", "note": "again"})[0] == 200


def test_contact_centre_owns_hands_off_undoes_and_changes_contacts(chat):
    svc = chat["svc"]
    p = jordan()
    cid = p["claim_id"]
    svc.secure_submit(svc.repo.new_secure_token(cid), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})

    def agent(body):
        res = api.handler({"requestContext": {"http": {"method": "POST"}, **_as("[agents]")},
                           "rawPath": f"/api/claims/{cid}/actions", "body": json.dumps(body)}, None)
        return res["statusCode"]
    assert agent({"action": "assign"}) == 200 and svc.repo.get_claim(cid)["assignee"] == "staff@example.com"
    assert agent({"action": "handoff", "to": "b@x.com"}) == 400                          # what should they do?
    assert agent({"action": "handoff", "to": "b@x.com", "note": "call back about the receipt", "due": "2026-10-10T01:00:00Z"}) == 200
    row = next(r for r in call("GET", "/api/claims")[1] if r["id"] == cid)
    assert row["assignee"] == "b@x.com" and row["handoff"]["from"] == "staff@example.com"
    assert agent({"action": "bank_correction", "checks": ["dob", "policy"]}) == 200
    assert agent({"action": "bank_correction_cancel"}) == 200                            # pressed by mistake
    assert svc.repo.get_claim(cid)["claim_form"]["account_last4"] == "7890" and "bank_pending" not in svc.unresolved(cid)
    assert agent({"action": "contact_update", "phone": "+82 10 1234 5678"}) == 409       # identity first
    assert agent({"action": "contact_update", "phone": "+82 10 1234 5678", "checks": ["dob", "booking"]}) == 200
    assert any("contact details" in (m.get("text") or "") for m in svc.repo.messages(cid))   # the OLD contact is told first
    assert agent({"action": "bank_correction", "checks": ["dob", "policy"]}) == 409     # new phone + new bank in a day: no
    t = svc.repo.get_traveler(svc.repo.get_claim(cid)["traveler_id"])
    assert t["phone"] == "+821012345678"
    assert any(a["action"] == "staff_contact_update" and a["after"]["phone"] == "•••5678"
               for a in svc.repo.audits(__import__("tripclaim.store").store.now_iso()[:10]))   # masked in the log


def test_paying_more_than_the_estimate_or_overwriting_needs_a_second_person(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "o1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid, path = p["claim_id"], f"/api/claims/{p['claim_id']}/actions"
    est = svc.claim_view(cid)["estimate"]["estimate_usd"]
    assert call("POST", path, {"action": "approve", "amount_usd": est + 50})[0] == 400                # more than supported: say why
    assert call("POST", path, {"action": "approve", "amount_usd": est + 50, "reason": "hotel paid twice"})[0] == 200
    pend = svc.repo.get_claim(cid)["pending_decision"]
    assert "over_estimate" in pend["why"]
    assert call("POST", path, {"action": "approve", "amount_usd": est})[1]["error"] == "pending_exists"  # no overwriting
    from tripclaim.service import DecisionConflict
    try:
        svc.confirm_decision(cid, "second@example.com", True, pid="stale")
        assert False
    except DecisionConflict:
        pass                                                                                       # changed under the reviewer
    svc.confirm_decision(cid, "second@example.com", True, pid=pend["pid"])
    assert svc.repo.get_claim(cid)["decision"]["amount_usd"] == round(est + 50, 2)


def test_staff_messages_cannot_promise_payment(chat):
    p = jordan()
    path = f"/api/claims/{p['claim_id']}/actions"
    status, body, _ = call("POST", path, {"action": "message", "note": "호텔비는 보상돼요. 걱정 마세요."})
    assert status == 422 and "보상돼요" in body["phrase"]
    assert call("POST", path, {"action": "message", "note": "약관상 호텔비는 보상하는 비용에 해당해요. 금액은 보험사가 정해요."})[0] == 200


def test_traveler_can_object_from_the_decision_card(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "a1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    assert call("POST", link + "/appeal", {"text": "the minibar was dinner"})[0] == 400          # nothing to object to yet
    cid = p["claim_id"]
    est = svc.claim_view(cid)["estimate"]["estimate_usd"]
    call("POST", f"/api/claims/{cid}/actions", {"action": "approve", "amount_usd": est - 10, "clauses": ["TD-4"], "note": "Minibar."})
    svc.confirm_decision(cid, "second@example.com", True)
    last = [m["text"] for m in call("GET", link + "/messages")[1]["messages"] if m["direction"] == "out"][-1]
    assert "[Result]" in last and "[Why less]" in last and "[If you disagree]" in last and last.strip().endswith(cid)
    assert call("POST", link + "/appeal", {"text": "The minibar line was my dinner, receipt attached."})[0] == 200
    assert call("POST", link + "/appeal", {"text": "again please"})[0] == 400                   # once
    data = call("GET", link + "/messages")[1]
    assert data["decision"]["appeal"]["reply_by"] and "objection" in data["messages"][-1]["text"].lower()
    assert next(r for r in call("GET", "/api/claims")[1] if r["id"] == cid)["appeal"]


def test_decision_and_information_request_wording_is_checked(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "w1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    path = f"/api/claims/{p['claim_id']}/actions"
    assert call("POST", path, {"action": "request_info", "note": "탑승권만 보내 주시면 꼭 입금돼요"})[0] == 422
    assert call("POST", path, {"action": "approve", "amount_usd": 10, "clauses": ["TD-4"], "note": "나머지는 다음에 보상해 드릴게요"})[0] == 422


def test_objection_goes_to_a_new_reviewer_and_can_be_closed(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "u1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid = p["claim_id"]
    est = svc.claim_view(cid)["estimate"]["estimate_usd"]

    def as_user(user, body):
        res = api.handler({"requestContext": {"http": {"method": "POST"}}, "headers": {"x-demo-user": user},
                           "rawPath": f"/api/claims/{cid}/actions", "body": json.dumps(body)}, None)
        return res["statusCode"], json.loads(res["body"])
    as_user("reviewer-1@demo", {"action": "approve", "amount_usd": est - 10, "clauses": ["TD-4"], "note": "Minibar."})
    as_user("reviewer-2@demo", {"action": "confirm"})
    # the traveler objects on the phone; the agent records it after one identity check
    assert as_user("agent@demo", {"action": "appeal_by_phone", "note": "The minibar line was dinner."})[0] == 409
    assert as_user("agent@demo", {"action": "appeal_by_phone", "note": "The minibar line was dinner.", "checks": ["dob"]})[0] == 200
    assert next(r for r in call("GET", "/api/claims")[1] if r["id"] == cid)["appeal"] == "open"
    # the two people behind the first decision cannot review the objection
    assert as_user("reviewer-1@demo", {"action": "appeal_uphold", "note": "Minibar is listed in TD-4(a)."})[0] == 409
    assert as_user("reviewer-2@demo", {"action": "correct", "amount_usd": est, "reason": "dinner"})[0] == 409
    assert as_user("staff3@demo", {"action": "appeal_uphold", "note": "Room service bill shows minibar items only."})[0] == 200
    last = [m["text"] for m in call("GET", link + "/messages")[1]["messages"] if m["direction"] == "out"][-1]
    assert "fcsc.kr" in last and "stays the same" in last
    assert next(r for r in call("GET", "/api/claims")[1] if r["id"] == cid)["appeal"] is None


def test_decision_letter_can_be_kept(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    assert call("GET", link + "/decision")[0] == 404
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "l1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    est = svc.claim_view(p["claim_id"])["estimate"]["estimate_usd"]
    call("POST", f"/api/claims/{p['claim_id']}/actions", {"action": "approve", "amount_usd": est - 10, "clauses": ["TD-4"], "note": "Minibar."})
    svc.confirm_decision(p["claim_id"], "second@example.com", True)
    status, html, _ = call("GET", link + "/decision")
    assert status == 200 and "Claim decision letter" in html and "[TD-4]" in html and "fcsc.kr" in html and "print()" in html


def test_call_log_and_hand_off_to_a_known_colleague(chat):
    import dataclasses
    svc = chat["svc"]
    svc.s = dataclasses.replace(svc.s, staff_directory="a@x.com,b@x.com")
    cid = jordan()["claim_id"]
    path = f"/api/claims/{cid}/actions"
    assert call("POST", path, {"action": "handoff", "to": "b@x.co", "note": "call back"})[0] == 400        # typo: refused
    assert call("POST", path, {"action": "handoff", "to": "b@x.com", "note": "call back"})[0] == 200
    assert svc.repo.get_claim(cid)["handoff"]["to"] == "b@x.com"
    assert call("POST", path, {"action": "handoff_done"})[0] == 200 and not svc.repo.get_claim(cid)["handoff"]
    assert call("POST", path, {"action": "call_log", "to": "smishing", "due": "answered", "note": "official number given"})[0] == 200
    log = [a for a in svc.repo.actions(cid) if a["type"] == "call_log"][0]
    assert log["detail"] == {"topic": "smishing", "result": "answered"}
    assert "a@x.com" in call("GET", "/api/staff")[1]["staff"]


def test_call_back_waits_in_a_queue_until_the_next_call(chat):
    svc = chat["svc"]
    cid = jordan()["claim_id"]
    path = f"/api/claims/{cid}/actions"
    assert call("POST", path, {"action": "call_log", "due": "answered"})[0] == 400                          # topic must be chosen
    assert call("POST", path, {"action": "call_log", "to": "pay_date", "due": "call_back"})[0] == 400       # when to call back
    assert call("POST", path, {"action": "call_log", "to": "pay_date", "due": "call_back", "note": "after 3pm",
                               "callback_at": "2026-10-06T06:00:00Z"})[0] == 200
    assert call("POST", path, {"action": "call_log", "to": "bank", "due": "answered"})[0] == 200                # another call keeps it
    assert next(r for r in call("GET", "/api/claims")[1] if r["id"] == cid)["callback"]
    row = next(r for r in call("GET", "/api/claims")[1] if r["id"] == cid)
    assert row["callback"]["due"] == "2026-10-06T06:00:00Z" and row["callback"]["note"] == "after 3pm"
    assert call("POST", path, {"action": "call_log", "to": "pay_date", "due": "answered", "callback_done": True})[0] == 200  # the promised call
    assert next(r for r in call("GET", "/api/claims")[1] if r["id"] == cid)["callback"] is None


def test_partial_decision_reads_as_part_payment_and_letter_goes_with_the_text(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "pp1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    est = svc.claim_view(p["claim_id"])["estimate"]["estimate_usd"]
    call("POST", f"/api/claims/{p['claim_id']}/actions", {"action": "approve", "amount_usd": est - 10, "clauses": ["TD-4"], "note": "Minibar."})
    svc.confirm_decision(p["claim_id"], "second@example.com", True)
    assert next(r for r in call("GET", "/api/claims")[1] if r["id"] == p["claim_id"])["decision"] == "partial"
    sent = [m for m in svc.repo.messages(p["claim_id"]) if m.get("purpose") == "decision" and "/decision" in (m.get("text") or "")]
    assert sent, "the decision text/email carries the letter link"


def test_people_behind_an_earlier_version_stay_out_of_the_objection(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "e1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid = p["claim_id"]
    est = svc.claim_view(cid)["estimate"]["estimate_usd"]
    svc.decide(cid, "r1", "approve", est - 10, ["TD-4"], "Minibar.")
    svc.confirm_decision(cid, "r2", True)
    svc.decide(cid, "r3", "approve", est - 20, ["TD-4"], "Minibar and more.", reason="recount", correction=True)
    svc.confirm_decision(cid, "r4", True)
    assert set(svc.repo.get_claim(cid)["decision"]["earlier_people"]) == {"r1", "r2"}
    svc.appeal(cid, "The minibar line was dinner.")
    from tripclaim.service import SecondPersonRequired
    with pytest.raises(SecondPersonRequired):
        svc.resolve_appeal(cid, "r1", "Room service bill shows minibar items only.")
    assert svc.resolve_appeal(cid, "r5", "Room service bill shows minibar items only.")["appeal"]["resolved"]


def test_secure_done_page_says_how_to_fix_the_account(chat):
    svc = chat["svc"]
    p = jordan()
    token = svc.repo.new_secure_token(p["claim_id"])
    from urllib.parse import urlencode
    res = api.handler({"requestContext": {"http": {"method": "POST"}}, "rawPath": f"/secure/{token}",
                       "body": urlencode({"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})}, None)
    assert res["statusCode"] == 200 and "7890" in res["body"] and ("Wrong account" in res["body"] or "wrong account" in res["body"])


def test_past_call_back_refused_team_hand_off_and_traveler_call_request(chat):
    import dataclasses
    svc = chat["svc"]
    svc.s = dataclasses.replace(svc.s, staff_directory="a@x.com")
    p = jordan()
    cid = p["claim_id"]
    path = f"/api/claims/{cid}/actions"
    assert call("POST", path, {"action": "call_log", "to": "pay_date", "due": "call_back", "callback_at": "2020-01-01T00:00:00Z"})[0] == 400
    assert call("POST", path, {"action": "handoff", "to": "team:reviewers", "note": "objection review"})[0] == 200
    assert "team:reviewers" in call("GET", "/api/staff")[1]["teams"]
    link = p["chat"].split("https://demo.test")[1]
    assert call("POST", link + "/callback", {"text": "I'm in LA"})[0] == 200
    cb = svc.repo.get_claim(cid)["callback"]
    assert cb["by"] == "traveler" and cb["requested"]


def test_traveler_call_window_is_in_their_own_time():
    """The traveler picks a window in local time; we call in the first slot that is also contact-centre hours."""
    from datetime import datetime, timezone
    from tripclaim.service import Service
    fri = datetime(2026, 10, 9, 1, 10, tzinfo=timezone.utc)                       # Fri 10:10 KST
    assert Service.callback_slot("am", 120, fri) == (datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc), True)    # 09:00 local = 16:00 KST
    assert Service.callback_slot("pm", 120, fri) == (datetime(2026, 10, 9, 11, 0, tzinfo=timezone.utc), False)  # 13:00 local = 20:00 KST: flagged
    assert Service.callback_slot("pm", -420, fri) == (datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc), True)  # LA Sun 17:00 = Mon 09:00 KST


def test_traveler_call_request_keeps_window_and_offset(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    tid = svc.repo.get_claim(p["claim_id"])["traveler_id"]
    svc.repo.update_traveler(tid, last_inbound={"web": "2026-10-09T00:00:00Z"})  # uses the web chat: the reply lands there too
    assert call("POST", link + "/callback", {"pref": "am", "offset_min": 120})[0] == 200
    cb = svc.repo.get_claim(p["claim_id"])["callback"]
    assert cb["pref"] == "am" and cb["tz_offset"] == 120 and cb["due"].endswith("Z")
    assert any("your time" in (m.get("text") or "") for m in svc.repo.messages(p["claim_id"]))


def test_upheld_reply_reads_as_sentences(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "s1"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid = p["claim_id"]
    est = svc.claim_view(cid)["estimate"]["estimate_usd"]
    svc.decide(cid, "r1", "approve", est - 10, ["TD-4"], "Minibar.")
    svc.confirm_decision(cid, "r2", True)
    svc.appeal(cid, "The minibar line was dinner.")
    svc.resolve_appeal(cid, "r3", "The receipt lists minibar items only")
    assert svc.repo.get_claim(cid)["appeal"]["resolved"]["note"].endswith("only.")


def test_team_queue_is_taken_by_its_team_and_keeps_the_request(chat):
    svc = chat["svc"]
    cid = jordan()["claim_id"]
    path = f"/api/claims/{cid}/actions"

    def as_user(user, body):
        res = api.handler({"requestContext": {"http": {"method": "POST"}}, "headers": {"x-demo-user": user},
                           "rawPath": path, "body": json.dumps(body)}, None)
        return res["statusCode"]
    assert as_user("agent@demo", {"action": "handoff", "to": "team:reviewers", "note": "objection review", "due": "2030-01-01T00:00:00Z"}) == 200
    assert as_user("agent@demo", {"action": "assign"}) == 403                 # not a reviewer
    assert as_user("reviewer-3@demo", {"action": "assign"}) == 200
    c = svc.repo.get_claim(cid)
    assert c["assignee"] == "reviewer-3@demo" and c["handoff"]["note"] == "objection review" and c["handoff"]["taken_by"] == "reviewer-3@demo"


def test_overdue_objection_is_escalated_once(env):
    svc, clock = env["svc"], env["clock"]
    from .conftest import EVENT
    [cid] = svc.on_disruption(dict(EVENT))
    svc.repo.update_claim(cid, appeal={"text": "x", "reply_by": "2000-01-01", "at": "2000-01-01T00:00:00+00:00"})
    assert svc.escalate_overdue_appeals() == [cid]
    c = svc.repo.get_claim(cid)
    assert c["assignee"] == "team:reviewers" and c["appeal"]["escalated"]
    first = c["appeal"]["escalated"]
    svc.escalate_overdue_appeals()
    assert svc.repo.get_claim(cid)["appeal"]["escalated"] == first


def test_takeover_needs_a_reason_and_only_the_handee_closes_a_handoff(chat):
    cid = jordan()["claim_id"]
    path = f"/api/claims/{cid}/actions"

    def as_user(user, body):
        res = api.handler({"requestContext": {"http": {"method": "POST"}}, "headers": {"x-demo-user": user},
                           "rawPath": path, "body": json.dumps(body)}, None)
        return res["statusCode"]
    assert as_user("reviewer-1@demo", {"action": "assign"}) == 200
    assert as_user("agent@demo", {"action": "assign"}) == 409                                   # someone else's claim
    assert as_user("agent@demo", {"action": "assign", "reason": "reviewer-1 is off today"}) == 200
    assert as_user("agent@demo", {"action": "handoff", "to": "reviewer-2@demo", "note": "check the receipt"}) == 200
    assert as_user("reviewer-3@demo", {"action": "handoff_done"}) == 403
    assert as_user("reviewer-2@demo", {"action": "handoff_done"}) == 200


def test_upheld_reply_needs_what_was_checked_on_the_server(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "w9"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid = p["claim_id"]
    est = svc.claim_view(cid)["estimate"]["estimate_usd"]
    svc.decide(cid, "r1", "approve", est - 10, ["TD-4"], "Minibar.")
    svc.confirm_decision(cid, "r2", True)
    svc.appeal(cid, "The minibar line was dinner.")
    with pytest.raises(ValueError):
        svc.resolve_appeal(cid, "r3", "다시 검토한 줄: Hotel $110→$100 (TD-4). 확인한 내용: 봄")
    assert svc.resolve_appeal(cid, "r3", "다시 검토한 줄: Hotel. 확인한 내용: 영수증에 미니바 2건이 적혀 있음")["appeal"]["resolved"]


def test_traveler_can_withdraw_an_objection(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "wd"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid = p["claim_id"]
    est = svc.claim_view(cid)["estimate"]["estimate_usd"]
    svc.decide(cid, "r1", "approve", est - 10, ["TD-4"], "Minibar.")
    svc.confirm_decision(cid, "r2", True)
    assert call("POST", link + "/appeal_withdraw", {})[0] == 400              # nothing to withdraw yet
    svc.appeal(cid, "The minibar line was dinner.")
    assert call("POST", link + "/appeal_withdraw", {})[0] == 200
    assert svc.repo.get_claim(cid)["appeal"]["resolved"]["outcome"] == "withdrawn"
    status, html, _ = call("GET", link + "/decision")
    assert "withdrew the objection" in html and "no transfer fee" in html
    assert "Objection on record:" in html and "The minibar line was dinner." in html  # the letter keeps what was objected


def test_link_tokens_are_letters_and_digits_only():
    """A '-' or '_' inside an SMS link is where phones break it; tokens use letters and digits only."""
    import re
    from tripclaim.store import link_token
    toks = [link_token() for _ in range(500)]
    assert all(re.fullmatch(r"[A-Za-z0-9]{22}", t) for t in toks) and len(set(toks)) == 500


def test_handoff_of_someone_elses_claim_needs_a_reason_and_never_to_self(chat):
    svc = chat["svc"]
    cid = jordan()["claim_id"]
    path = f"/api/claims/{cid}/actions"

    def as_user(user, body):
        res = api.handler({"requestContext": {"http": {"method": "POST"}}, "headers": {"x-demo-user": user},
                           "rawPath": path, "body": json.dumps(body)}, None)
        return res["statusCode"]
    assert as_user("reviewer-1@demo", {"action": "assign"}) == 200
    assert as_user("agent@demo", {"action": "handoff", "to": "agent@demo", "note": "mine now"}) == 400
    assert as_user("agent@demo", {"action": "handoff", "to": "reviewer-2@demo", "note": "check"}) == 409
    assert as_user("agent@demo", {"action": "handoff", "to": "reviewer-2@demo", "note": "check", "reason": "reviewer-1 is off"}) == 200
    a = [x for d in [svc.clock().date().isoformat()] for x in svc.repo.audits(__import__("tripclaim.store", fromlist=["now_iso"]).now_iso()[:10])
         if x["action"] == "staff_handoff"][-1]
    assert a.get("takeover_reason") == "reviewer-1 is off" and "identity_check" not in a


def test_traveler_can_cancel_a_call_request_and_topic_is_guessed(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    assert call("POST", link + "/callback", {"pref": "pm", "offset_min": -420})[0] == 200
    cb = svc.repo.get_claim(p["claim_id"])["callback"]
    assert cb["topic"] == "documents" and cb["topic_guess"]          # nothing submitted yet: probably about documents
    cbv = call("GET", link + "/messages")[1]["callback"]
    assert cbv["requested"] and cbv["due"].endswith("Z")                          # the promised time, for the traveler's clock
    assert call("POST", link + "/callback", {"cancel": True})[0] == 200
    assert svc.repo.get_claim(p["claim_id"])["callback"] is None
    assert call("GET", link + "/messages")[1]["callback"] is None


def test_letter_after_an_objection_says_it_is_in(chat):
    svc = chat["svc"]
    p = jordan()
    link = p["chat"].split("https://demo.test")[1]
    imgs = [{"data": base64.b64encode(d).decode(), "mime": "image/jpeg", "filename": "x.jpg"} for d in (b"LETTER", b"HOTEL", b"BOARDING")]
    call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "lt"})
    svc.secure_submit(svc.repo.new_secure_token(p["claim_id"]), {"signed_name": "Kim", "holder": "Kim", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    call("POST", link + "/submit")
    cid = p["claim_id"]
    est = svc.claim_view(cid)["estimate"]["estimate_usd"]
    svc.decide(cid, "r1", "approve", est - 10, ["TD-4"], "The minibar is not covered.")
    svc.confirm_decision(cid, "r2", True)
    html = call("GET", link + "/decision")[1]
    assert "Reviewer's explanation:</b> The minibar is not covered." in html and "a reviewer who did not make this decision" in html
    svc.appeal(cid, "The minibar line was dinner.")
    html = call("GET", link + "/decision")[1]
    assert "no need to send it again" in html and "Object to this decision" not in html
    assert "Korea time" in html and "KST" not in html                    # one date style in the letter


def test_handing_on_someone_elses_claim_needs_a_reason_even_for_admins(chat):
    def as_user(user, body):
        res = api.handler({"requestContext": {"http": {"method": "POST"}}, "headers": {"x-demo-user": user},
                           "rawPath": f"/api/claims/{cid}/actions", "body": json.dumps(body)}, None)
        return res["statusCode"], json.loads(res["body"])
    cid = jordan()["claim_id"]
    assert as_user("reviewer-1@demo", {"action": "assign"})[0] == 200
    s, b = as_user("admin@demo", {"action": "handoff", "to": "team:reviewers", "note": "objection review"})
    assert s == 409 and b["error"] == "takeover_needs_reason"
    assert as_user("admin@demo", {"action": "handoff", "to": "team:reviewers", "note": "objection review", "reason": "reviewer-1 on leave"})[0] == 200
    assert as_user("agent@demo", {"action": "handoff", "to": "agent@demo", "note": "x"})[0] == 400


def test_denying_with_documents_to_check_needs_a_reason(chat):
    """Another name or an unreadable photo is usually a request for documents; a denial past it says why."""
    svc = chat["svc"]
    cid = jordan()["claim_id"]
    svc.unresolved = lambda _cid: ["check:airline_letter"]
    with pytest.raises(PermissionError):
        svc.decide(cid, "r1", "deny", 0, ["EX-2"], "These documents are in another name.", reason="x" * 0)
    svc.decide(cid, "r1", "deny", 0, ["EX-2"], "These documents are in another name.", reason="asked twice, no reply in 14 days")


def test_denial_on_exclusions_with_document_checks_is_flagged_for_the_second_reviewer(chat):
    svc = chat["svc"]
    cid = jordan()["claim_id"]
    svc.unresolved = lambda _cid: ["check:airline_letter"]
    svc.decide(cid, "r1", "deny", 0, ["EX-2"], "These documents are in another name.", reason="name mismatch")
    p = svc.repo.get_claim(cid)["pending_decision"]
    assert p["basis_check"] == {"open": ["check:airline_letter"], "clauses": ["EX-2"]}
    with pytest.raises(PermissionError):                                       # past the warning only with a reason
        svc.confirm_decision(cid, "r2", True)
    svc.confirm_decision(cid, "r2", False, note="ask for documents instead")
    svc.decide(cid, "r1", "deny", 0, ["EX-2", "TD-1"], "Documents in another name.", reason="name mismatch")
    assert svc.repo.get_claim(cid)["pending_decision"]["basis_check"]          # a non-EX clause mixed in: still flagged
    svc.confirm_decision(cid, "r2", False, note="again")
    svc.decide(cid, "r1", "deny", 0, ["CL-1", "EX-2"], "Documents in another name.", reason="asked twice, no reply")
    assert "basis_check" not in svc.repo.get_claim(cid)["pending_decision"]   # CL-1 cited: the documents are the ground


def test_a_document_from_another_claim_can_be_held_for_siu(chat):
    """Referring to the special investigation unit holds the claim quietly: no message to the traveler, no decision."""
    svc = chat["svc"]
    p = jordan()
    cid = p["claim_id"]
    other = svc.repo.create_claim(svc.repo.get_claim(cid)["traveler_id"])["id"]
    svc.cross_claim_flags = lambda claim, docs: [{"code": "other_claim_document", "doc": "d1", "detail": f"{other} (Kim)"}]
    sent = len(chat["outbox"])

    def act(body, user="reviewer-1@demo"):
        res = api.handler({"requestContext": {"http": {"method": "POST"}}, "headers": {"x-demo-user": user},
                           "rawPath": f"/api/claims/{cid}/actions", "body": json.dumps(body)}, None)
        return res["statusCode"], json.loads(res["body"])
    assert act({"action": "siu"})[0] == 400                                         # a reason is required
    s, c = act({"action": "siu", "reason": "documents first used in another claim"})
    assert s == 200 and c["siu"]["linked"] == [other] and svc.repo.get_claim(other)["siu_linked"]["from"] == cid
    assert act({"action": "message", "note": "Please send documents in your name."})[1]["error"] == "siu_hold"
    assert act({"action": "deny", "clauses": ["EX-2"], "note": "x"})[0] == 409
    assert len(chat["outbox"]) == sent                                               # the traveler heard nothing
    assert act({"action": "contact_update", "phone": "+15550000000", "checks": ["dob", "policy"]}, "agent@demo")[1]["error"] == "siu_hold"
    seen = api.handler({"requestContext": {"http": {"method": "GET"}}, "headers": {"x-demo-user": "agent@demo"},
                        "rawPath": f"/api/claims/{cid}", "body": ""}, None)
    assert "reason" not in json.loads(seen["body"])["claim"]["siu"]                   # the contact centre sees the hold, not why
    s, b = act({"action": "siu_hold_payment", "reason": "possible collusion"})
    assert s == 400                                                                   # only a linked claim's payment
    hold_other = api.handler({"requestContext": {"http": {"method": "POST"}}, "headers": {"x-demo-user": "reviewer-1@demo"},
                              "rawPath": f"/api/claims/{other}/actions", "body": json.dumps({"action": "siu_hold_payment", "reason": "possible collusion"})}, None)
    assert hold_other["statusCode"] == 200 and svc.repo.get_claim(other)["siu_linked"]["hold_payment"]["reason"] == "possible collusion"
    body = json.loads(seen["body"])
    assert all(not a.get("reason") for a in body["actions"] if a["type"].startswith("siu"))      # nor in the action history
    assert body["unresolved"] == [] and all(not d["flags"] for d in body["documents"])         # nor in the findings
    assert body["claim"]["flags"] == [] and "Kim" not in json.dumps(body)                        # nor another traveler's name
    rows = api.handler({"requestContext": {"http": {"method": "GET"}}, "headers": {"x-demo-user": "agent@demo"},
                        "rawPath": "/api/claims", "body": ""}, None)
    row = next(r for r in json.loads(rows["body"]) if r["id"] == cid)
    assert row["flags"] == [] and row["open"] == []                                              # nor in the list
    pk = api.handler({"requestContext": {"http": {"method": "GET"}}, "headers": {"x-demo-user": "agent@demo"},
                      "rawPath": f"/api/claims/{cid}/packet", "body": ""}, None)
    assert pk["statusCode"] == 403                                                               # the reviewer packet: reviewers only
    assert act({"action": "siu_clear", "reason": "done", "outcome": "no_fraud"}, "reviewer-2@demo")[0] == 403  # admins record SIU's conclusion
    assert act({"action": "siu_clear", "reason": "done"}, "admin@demo")[0] == 400                # the conclusion is required
    assert act({"action": "siu_clear", "reason": "confirmed", "outcome": "fraud_confirmed"}, "admin@demo")[0] == 200
    assert svc.repo.get_claim(cid)["siu"]["outcome"] == "fraud_confirmed"                       # still held: only a denial may go ahead
    assert act({"action": "message", "note": "x"})[1]["error"] == "siu_hold"
    svc.repo.update_claim(cid, siu={**svc.repo.get_claim(cid)["siu"], "outcome": None})
    assert act({"action": "siu_clear", "reason": "SIU: not fraud, a family member's photo", "outcome": "no_fraud"}, "admin@demo")[0] == 200
    assert svc.repo.get_claim(other)["siu_linked"]["hold_payment"]                               # a held payment is released on its own
    rel = lambda user: api.handler({"requestContext": {"http": {"method": "POST"}}, "headers": {"x-demo-user": user},
                                    "rawPath": f"/api/claims/{other}/actions", "body": json.dumps({"action": "siu_release_payment", "reason": "SIU closed"})}, None)["statusCode"]
    assert rel("reviewer-1@demo") == 409 and rel("reviewer-2@demo") == 200                      # not by the person who held it
    assert svc.repo.get_claim(cid)["siu"] is None
    assert svc.repo.get_claim(other)["siu_linked"] is None                           # the linked claim is released too


def test_traveler_can_ask_for_another_time_for_a_staff_call_back(chat):
    svc = chat["svc"]
    p = jordan()
    cid = p["claim_id"]
    link = p["chat"].split("https://demo.test")[1]
    svc.repo.update_claim(cid, callback={"due": "2026-10-12T00:00:00Z", "by": "agent@demo", "topic": "pay_date"})
    assert call("POST", link + "/callback", {"change": True, "pref": "pm", "offset_min": -420})[0] == 200
    cb = svc.repo.get_claim(cid)["callback"]
    assert cb["due"] == "2026-10-12T00:00:00Z" and cb["change_request"]["pref"] == "pm"   # staff set the new time
    assert call("GET", link + "/messages")[1]["callback"]["change_requested"] is True
