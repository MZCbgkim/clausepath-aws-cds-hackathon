"""Korean mode: templates, fixed messages, language detection, secure page, packet copy."""
import uuid

from tripclaim.handlers import api
from tripclaim.i18n import detect, explicit_switch
from tripclaim.inbound import Inbound

from .conftest import EVENT, PHONE


def wa(text, media=None):
    return Inbound(channel="whatsapp", sender=PHONE, text=text, media=media or [], provider_id=uuid.uuid4().hex)


def test_detect_and_switch():
    assert detect("오늘 밤 호텔비도 보상되나요?") == "ko"
    assert detect("Is my hotel covered?") == "en"
    assert detect("XX123 호텔") == "ko" and detect("123") is None
    assert explicit_switch("English") == "en" and explicit_switch("한국어로") == "ko" and explicit_switch("hotel") is None


def test_korean_traveler_end_to_end(env):
    svc, outbox = env["svc"], env["outbox"]
    svc.repo.update_traveler("trv-1", name="김지수", locale="ko")
    [claim_id] = svc.on_disruption(dict(EVENT))
    alert = outbox[-1]
    assert alert["locale"] == "ko" and alert["text"].startswith("안녕하세요 김지수님, 10월 5일 XX123편이 결항됐어요.")

    svc.handle_inbound(wa("네. 오늘 밤 호텔비도 보상되나요?"))
    assert outbox[-2]["text"] == "약관을 확인하고 있어요. 잠시만 기다려 주세요."

    token = svc.secure_link(claim_id).rsplit("/", 1)[1]
    status, page = _get(f"/secure/{token}", svc)
    assert status == 200 and "보안 청구 페이지" in page and 'lang="ko"' in page
    svc.secure_submit(token, {"signed_name": "김지수", "holder": "김지수", "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    assert outbox[-1]["text"].startswith("청구서와 신분증을 받았어요. 아직 필요한 서류: 항공사 결항·지연 확인서")

    svc.reviewer_action(claim_id, "rev", "request_info", "택시 영수증을 보내 주세요.")
    assert "현재 상태: 추가 정보 요청" in outbox[-1]["text"]


def test_language_follows_traveler_unless_explicit(env):
    svc = env["svc"]
    svc.on_disruption(dict(EVENT))
    svc.handle_inbound(wa("호텔 영수증 어디로 보내요?"))
    assert svc.repo.get_traveler("trv-1")["locale"] == "ko"
    svc.handle_inbound(wa("English"))
    assert env["outbox"][-1]["text"] == "OK, I'll reply in English from now on."
    svc.handle_inbound(wa("감사합니다"))  # explicit choice sticks over detection
    assert svc.repo.get_traveler("trv-1")["locale"] == "en"


def test_korean_reminder(env):
    svc, outbox = env["svc"], env["outbox"]
    svc.repo.update_traveler("trv-1", name="김지수", locale="ko")
    svc.on_disruption(dict(EVENT))
    svc.run_reminders(force=True)
    assert "아직 필요한 서류가 있어요: 항공사 확인서, 원래 항공권" in outbox[-1]["text"]


def _get(path, svc):
    api.SERVICE = svc
    try:
        res = api.handler({"requestContext": {"http": {"method": "GET"}}, "rawPath": path}, None)
        return res["statusCode"], res["body"]
    finally:
        api.SERVICE = None


def test_hangul_name_needs_passport_name(env):
    svc, outbox = env["svc"], env["outbox"]
    svc.repo.update_traveler("trv-1", name="김지수", locale="ko")
    [claim_id] = svc.on_disruption(dict(EVENT))
    svc.channels["whatsapp"].media["m1"] = b"LETTER"   # letter says TAYLOR/JORDAN MR
    svc.handle_inbound(wa("카운터에서 받았어요", [{"media_id": "m1", "mime": "image/jpeg", "filename": "l.jpg"}]))
    claim = svc.repo.get_claim(claim_id)
    assert "name_unverified" in {f["code"] for f in claim["flags"]} and claim["status"] == "review"

    svc.repo.update_traveler("trv-1", passport_name="TAYLOR/JORDAN MR")   # passport name on file -> compared and passes
    claim, _, _ = svc.recheck(claim_id)
    assert not {"name_unverified", "name_mismatch"} & {f["code"] for f in claim["flags"]}


def test_email_does_not_change_language(env):
    from email.message import EmailMessage
    from tripclaim.inbound import parse_email
    svc = env["svc"]
    svc.repo.update_traveler("trv-1", locale="ko", locale_source="detected")
    svc.on_disruption(dict(EVENT))
    m = EmailMessage()
    m["From"], m["Subject"] = "jordan@example.com", "Fwd: Kumo Air cancellation"
    m.set_content("Forwarded message. Kumo Air: your flight XX123 was cancelled due to Typhoon Hagi. Booking K7Q2LM.")
    svc.handle_inbound(parse_email(m.as_bytes()))
    assert svc.repo.get_traveler("trv-1")["locale"] == "ko"


def test_sms_alert_link_never_split(env):
    """Korean SMS is UCS-2 (70 chars per part) and international routes often deliver parts unjoined."""
    from dataclasses import replace
    svc, outbox = env["svc"], env["outbox"]
    svc.s = replace(svc.s, public_base_url="https://abcdefghij.execute-api.us-east-1.amazonaws.com")  # real length
    svc.repo.update_traveler("trv-1", name="김지수", locale="ko", preferred_channel="sms", consent={"sms": True})
    svc.on_disruption(dict(EVENT))
    words, link = outbox[-2], outbox[-1]   # sent as two SMS so the link is never cut across parts
    assert words["text"] == "[샘플손해보험] 10월 5일 XX123편 결항. 여행자보험 청구 안내를 보내 드려요. 문의 02-000-0000" and len(words["text"]) <= 70
    assert link["text"].startswith("SampleIns: https://abcdefghij.execute-api.us-east-1.amazonaws.com/c/") and len(link["text"]) <= 160 and link["text"].isascii()


def test_sms_parts():
    from tripclaim.channels import sms_parts
    assert sms_parts("Short English text https://x.y/c/abc") == ["Short English text https://x.y/c/abc"]
    assert sms_parts("짧은 안내") == ["짧은 안내"]
    long_ko = "가" * 60 + " https://example.com/c/" + "a" * 22
    assert sms_parts(long_ko) == ["가" * 60, "SampleIns: https://example.com/c/" + "a" * 22]
