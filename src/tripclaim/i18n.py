"""English / Korean text for everything the system sends on its own (the agent writes its replies itself).

Each traveler has `locale` ("en" or "ko"). Unknown keys or locales fall back to English.
"""
import re
from datetime import datetime, timedelta

LOCALES = ("en", "ko")
LANGUAGE_NAME = {"en": "English", "ko": "Korean (한국어)"}
WA_LANGUAGE = {"en": "en_US", "ko": "ko"}

# WhatsApp templates: one approved template per name and language in Meta. Body parameters are {1}..{n}.
TEMPLATES = {
    "en": {
        "flight_disruption_alert": "Hi {1}, your flight {2} on {3} was {4}. Your travel policy may cover extra costs while you wait. Reply YES and we'll tell you what to keep.",
        "document_reminder": "Hi {1}, your claim for flight {2} still needs: {3}. Reply here, or forward airline emails to {4} and we'll take it from there.",
        "claim_received": "Hi {1}, we received your claim {2}. We'll update you here when the insurer reviews it.",
        "claim_status_update": "Hi {1}, your claim {2} is now {3}. {4} Reply here if you have any questions.",
    },
    "ko": {
        "flight_disruption_alert": "안녕하세요 {1}님, {3} {2}편이 {4}됐어요. 기다리는 동안 쓴 비용이 여행자보험으로 보상될 수도 있어요. '네'라고 답하시면 지금 챙겨야 할 서류를 알려드릴게요.",
        "document_reminder": "안녕하세요 {1}님, {2}편 청구에 아직 필요한 서류가 있어요: {3}. 여기로 보내시거나, 항공사 메일은 {4} 주소로 전달해 주세요.",
        "claim_received": "안녕하세요 {1}님, 청구가 접수됐어요(번호 {2}). 보험사 심사가 진행되면 여기로 알려드릴게요.",
        "claim_status_update": "안녕하세요 {1}님, 청구 진행 상황을 알려드려요(번호 {2}). 현재 상태: {3}. {4} 궁금한 점은 여기로 물어봐 주세요.",
    },
}
# Per-language template name overrides. Meta classified the Korean alert as MARKETING; once the WABA may create
# templates again, submit the factual variant below as "flight_disruption_notice" and map it here:
#   "안녕하세요 {1}님, {3} {2}편이 {4}된 것으로 확인됐어요. 가입하신 여행자보험의 지연비용 청구에 필요한 서류를
#    안내받으시려면 '네'라고 답해 주세요."
TEMPLATE_NAMES = {}


def wa_template_name(name, locale):
    return TEMPLATE_NAMES.get(norm(locale), {}).get(name, name)


# Quick-reply buttons approved with the template (taps arrive as inbound "button" messages).
TEMPLATE_BUTTONS = {"flight_disruption_alert": {"en": ["YES", "Not now"], "ko": ["네", "나중에"]}}
# Example values Meta needs to review each template.
TEMPLATE_EXAMPLES = {
    "en": {"flight_disruption_alert": ["Jordan", "XX123", "5 Oct", "cancelled"],
           "document_reminder": ["Jordan", "XX123", "airline letter, receipts", "claim@claims.example.com"],
           "claim_received": ["Jordan", "clm-1a2b3c4d"],
           "claim_status_update": ["Jordan", "clm-1a2b3c4d", "approved", "Expected payment within 3 business days if nothing needs checking."]},
    "ko": {"flight_disruption_alert": ["김지수", "XX123", "10월 5일", "결항"],
           "document_reminder": ["김지수", "XX123", "항공사 확인서, 영수증", "claim@claims.example.com"],
           "claim_received": ["김지수", "clm-1a2b3c4d"],
           "claim_status_update": ["김지수", "clm-1a2b3c4d", "승인", "추가 확인이 없으면 3영업일 안에 지급 예정이에요(보험사 심사에 따라 달라질 수 있어요)."]},
}

STRINGS = {
    "en": {
        "progress_policy": "Checking your policy, one moment…",
        "wording_safe": "Whether and how much the insurer pays is decided in its review; I can tell you what the policy says and what is still needed.",
        "callback_requested": "Thanks — we will call you back{on} as soon as possible during business hours (Korea time). You don't need to call us from abroad.", "callback_on": " on your number ending {last4}", "callback_slot": "Thanks — we will call you{on} around {when} your time. You don't need to call us from abroad.", "tpl_why_less": "Decision: {result}. {lines}Basis: {clauses}. The full reasons and the decision letter are in your chat.", "tpl_result_partial": "paid in part, ${amount}", "tpl_line": "{merchant} ${frm} → ${to} ({why}). ", "tpl_pay_date": "If nothing needs checking, payment is expected on {date}. The insurer confirms the date.", "tpl_pay_date_appeal": "Your objection is being reviewed by a different reviewer, with a reply by {reply}. For the amount already decided, payment is expected on {date} if nothing needs checking; the insurer confirms the date.", "tpl_result_deny": "not payable", "callback_change_requested": "Got it — we'll find another time in the window you chose and let you know here. You don't need to call us.", "callback_cancelled": "Your call request is cancelled. Ask again any time from the checklist.", "appeal_withdrawn": "Your objection is withdrawn. The decision and the payment date stay as they were.",
        "agent_unavailable": "Sorry, I can't answer right now. Your message and photos are saved — please try again in a minute, or call {phone}.",
        "receipt_next": " What happens next: estimated claim ${usd}{krw}{acct}. {when} You'll hear here and by text.",
        "when_pay": "Expected payment: {date}, if nothing needs checking (the insurer confirms the date).",
        "when_pay_short": "{date}",
        "when_check": "Some documents need a check; the insurer will update you by {date}.",
        "when_none": "The insurer will confirm whether and when it pays after checking your documents.",
        "secure_done_complete": "Thanks, your form and ID are in. Your checklist is complete. Reply SUBMIT (we'll show a summary to confirm), or tap \"Submit to the insurer\" in the chat.",
        "secure_done_complete_web": "Thanks, your form and ID are in. Your checklist is complete. Tap \"Submit to the insurer\" below to send your claim.",
        "secure_done_missing": "Thanks, your form and ID are in. Still needed: {missing}.",
        "status_ready": "ready to submit", "status_missing": "missing documents",
        "status_approved": "payment decided", "status_info_requested": "waiting for information", "status_reviewing": "under review", "status_denied": "not payable",
        "decision_full": "The insurer decided to pay {usd}.", "decision_partial": "The insurer decided to pay part of the claim: {usd}, less than the estimated claim.", "status_partial": "paid in part",
        "decision_deny": "The insurer decided it cannot pay this claim.", "decision_basis": "(Policy: {clauses})",
        "ref_no": "Claim number {claim}",
        "appeal_upheld": "[Objection reviewed] A different reviewer looked at your claim again and the decision stays the same. {note} If you still disagree, you can ask Korea's Financial Supervisory Service for dispute mediation: 1332 in Korea, or online at www.fcsc.kr. Questions: {phone}.",
        "appeal_received": "We received your objection. A different reviewer will look at the claim again and reply here by {date}. The payment date for the amount already decided does not change.", "dec_h_result": "Result", "dec_h_why_partial": "Why less", "dec_h_why_deny": "Why", "dec_h_when": "When", "dec_h_appeal": "If you disagree", "dec_h_letter": "Decision letter", "letter_line": "The official letter, to print or keep for your records: {link}",
        "decision_line": "{merchant}: ${frm} → ${to} ({why}).",
        "decision_corrected": "Correction to the earlier decision:",
        "contact_changed": "Your contact details for this claim were just changed at our contact centre. If this was not you, call {phone} now; bank details cannot be changed for 24 hours.",
        "adj_excluded_item": "item not covered", "adj_airline_paid": "paid by the airline", "adj_other_insurance": "paid by another insurance",
        "adj_over_reasonable": "above a reasonable cost", "adj_other": "see the reason below",
        "appeal": "If you disagree, use \"Object to this decision\" under Details below, or call {phone}, by {deadline}; a different reviewer looks again. If you still disagree, you can ask Korea's Financial Supervisory Service for dispute mediation: 1332 in Korea, or online at www.fcsc.kr from abroad.",
        "resend_link": "Here is your claim chat link again (no app or login needed).\n{link}",
        "bank_correction": "As you asked, please enter your bank account again on the secure page. Your form and ID stay as they are.\n{link}",
        "bank_corrected": "Thanks, your payout account is now {bank} •••{last4}. We've told the insurer.",
        "no_note": "No action is needed from you.",
        "receipt_copy": " A copy of what we sent is attached below.",
        "subject_claim": "Your travel claim", "subject_received": "We received your claim {claim}",
        "subject_update": "Update on claim {claim}",
        "event_cancelled": "cancelled", "event_delayed": "delayed",
        "dont_send": "Please don't send that by message. Use the secure claims page instead.\n{link}",
        "got_it": "Got it: {summary}", "next": " Next: {item}.",
        "complete_submit": " Your checklist is complete. Reply SUBMIT and we'll send you a summary to confirm before anything is filed.",
        "submit_confirm": "Before we file: estimated claim {usd} (the insurer decides the amount), {n} documents, payment to {acct}. Reply YES within 30 minutes to send it to the insurer, NO to cancel, or open the chat to check first.", "submit_cancelled": "Cancelled — nothing was sent. Reply SUBMIT when you are ready, or use the chat.",
        "complete_submit_web": " Your checklist is complete. Tap \"Submit to the insurer\" below to send the claim.",
        "submitted": "Submitted claim {claim}. If nothing needs checking, the insurer expects to pay within 3 business days of receiving everything.",
        "almost_done": "Almost done. Please complete the claim form and ID on the secure page.\n{link}",
        "still_needed": "Thanks. Still needed: {items}.", "noted": "Thanks, noted.",
        "language_switched": "OK, I'll reply in English from now on.",
        "chat_link": "Continue here (no app or login needed).\n{link}",
        # SMS: one Korean character switches the whole text to UCS-2 (70 chars per part), so keep it to a line + link.
        "sms_alert": "[{insurer}] Flight {flight} on {date} was {event}. Your travel insurance claim helper is ready. Questions: {phone} {link}",
        "sms_reminder": "[{insurer}] Your claim for {flight} still needs documents. Questions: {phone} {link}",
    },
    "ko": {
        "progress_policy": "약관을 확인하고 있어요. 잠시만 기다려 주세요.",
        "wording_safe": "보상 여부와 금액은 보험사 심사에서 정해져요. 약관 내용과 아직 필요한 서류는 제가 안내해 드릴게요.",
        "callback_requested": "전화 요청을 받았어요. 근무 시간에{on} 최대한 빨리 전화드릴게요. 따로 전화하지 않으셔도 돼요.", "callback_on": " 끝자리 {last4} 번호로", "callback_slot": "전화 요청을 받았어요. 현지 시간 {when} 무렵{on} 전화드릴게요. 따로 전화하지 않으셔도 돼요.", "tpl_why_less": "결정: {result}. {lines}근거: {clauses}. 자세한 이유와 결정 통지서는 대화창에 있어요.", "tpl_result_partial": "일부 지급 ${amount}", "tpl_line": "{merchant} ${frm} → ${to} ({why}). ", "tpl_pay_date": "추가 확인이 없으면 {date} 지급 예정이에요. 보험사 심사에 따라 달라질 수 있어요.", "tpl_pay_date_appeal": "이의 신청은 처음 결정한 사람이 아닌 다른 심사자가 검토해 {reply}까지 알려 드려요. 이미 결정된 금액은 추가 확인이 없으면 {date} 지급 예정이에요(보험사 심사에 따라 달라질 수 있어요).", "tpl_result_deny": "지급하지 않음", "callback_change_requested": "시간 변경 요청을 받았어요. 고르신 시간대에서 새 시각을 정해 이 대화창으로 알려 드릴게요. 따로 전화하지 않으셔도 돼요.", "callback_cancelled": "전화 요청을 취소했어요. 필요하시면 체크리스트에서 언제든 다시 요청하실 수 있어요.", "appeal_withdrawn": "이의 신청을 철회했어요. 결정과 지급 예정일은 그대로예요.",
        "agent_unavailable": "지금은 답변이 늦어지고 있어요. 보내신 내용과 사진은 저장됐으니 잠시 후 다시 보내 주시거나 {phone}로 전화해 주세요.",
        "receipt_next": " 다음 단계: 예상 청구액 ${usd}{krw}{acct}. {when} 결과는 이 대화창과 문자로 알려 드릴게요.",
        "when_pay": "추가 확인이 없으면 {date} 지급 예정이에요(보험사 심사에 따라 달라질 수 있어요).",
        "when_check": "확인이 필요한 서류가 있어 {date}까지 확인 결과를 알려 드려요.",
        "when_none": "보험사가 서류를 확인한 뒤 지급 여부와 일정을 알려 드려요.",
        "secure_done_complete": "청구서와 신분증을 받았어요. 서류가 모두 준비됐어요. '제출'이라고 답하시거나 대화창의 '보험사에 제출하기'를 누르시면 청구를 보낼게요.",
        "secure_done_complete_web": "청구서와 신분증을 받았어요. 서류가 모두 준비됐어요. 아래 '보험사에 제출하기'를 누르시면 청구를 보낼게요.",
        "secure_done_missing": "청구서와 신분증을 받았어요. 아직 필요한 서류: {missing}.",
        "status_ready": "제출 준비 완료", "status_missing": "서류 보완 필요",
        "status_approved": "지급 결정", "status_info_requested": "추가 정보 요청", "status_reviewing": "심사 중", "status_denied": "지급 불가",
        "decision_full": "보험사가 {usd} 지급을 결정했어요.", "decision_partial": "보험사가 청구액 중 일부인 {usd}를 지급하기로 했어요. 예상 청구액보다 적어요.", "status_partial": "일부 지급",
        "decision_deny": "보험사가 이번 청구는 지급할 수 없다고 결정했어요.", "decision_basis": "(약관 근거: {clauses})",
        "ref_no": "접수번호 {claim}",
        "appeal_upheld": "[이의 검토 결과] 처음 결정한 사람이 아닌 다른 심사자가 다시 검토했지만 결정은 그대로예요. {note} 그래도 동의하기 어려우면 금융감독원 분쟁조정(국번 없이 1332, www.fcsc.kr)을 신청할 수 있어요. 문의 {phone}",
        "appeal_received": "이의 신청을 받았어요. 처음 결정한 사람이 아닌 다른 심사자가 다시 검토해서 {date}까지 이 대화창으로 알려 드릴게요. 이미 결정된 금액의 지급 예정일은 바뀌지 않아요.", "dec_h_result": "결과", "dec_h_why_partial": "줄어든 이유", "dec_h_why_deny": "지급하지 않는 이유", "dec_h_when": "지급 예정", "dec_h_appeal": "동의하지 않으면", "dec_h_letter": "결정 통지서", "letter_line": "보관하거나 다른 곳에 제출할 수 있는 공식 통지서예요: {link}",
        "decision_line": "{merchant}: ${frm} → ${to} ({why}).",
        "decision_corrected": "앞서 안내한 결정을 정정해요.",
        "contact_changed": "고객센터에서 이 청구의 연락처가 방금 변경됐어요. 본인이 요청하지 않았다면 바로 {phone}로 전화해 주세요. 앞으로 24시간 동안은 입금 계좌를 바꿀 수 없어요.",
        "adj_excluded_item": "보상하지 않는 항목", "adj_airline_paid": "항공사가 부담한 비용", "adj_other_insurance": "다른 보험에서 받는 비용",
        "adj_over_reasonable": "합리적인 비용을 넘는 부분", "adj_other": "아래 사유 참고",
        "appeal": "결과에 동의하지 않으시면 {deadline}까지 아래 '자세히' → '이의 신청하기'를 누르시거나 {phone}로 전화해 주세요. 처음 결정한 사람이 아닌 다른 심사자가 다시 검토해요. 그래도 동의하기 어려우면 금융감독원 분쟁조정(국번 없이 1332, www.fcsc.kr)을 신청할 수 있어요.",
        "resend_link": "요청하신 청구 대화창 링크를 다시 보내 드려요(앱 설치·로그인 없음).\n{link}",
        "bank_correction": "요청하신 대로 보안 페이지에서 입금 계좌만 다시 입력해 주세요. 청구서와 신분증은 그대로 유지돼요.\n{link}",
        "bank_corrected": "입금 계좌가 {bank} •••{last4}(으)로 바뀌었어요. 보험사에도 알렸어요.",
        "no_note": "따로 하실 일은 없어요.",
        "receipt_copy": " 보험사에 보낸 내용은 아래에 붙여 둘게요.",
        "subject_claim": "여행자보험 청구 안내", "subject_received": "청구 접수 확인 (번호 {claim})",
        "subject_update": "청구 진행 상황 (번호 {claim})",
        "event_cancelled": "결항", "event_delayed": "지연",
        "dont_send": "이 정보는 메시지로 보내지 마세요. 보안 청구 페이지에서만 입력해 주세요.\n{link}",
        "got_it": "받았어요: {summary}", "next": " 다음으로 필요한 서류: {item}.",
        "complete_submit": " 서류가 모두 준비됐어요. '제출'이라고 답하시면 보내기 전에 요약을 한 번 더 보여 드릴게요.",
        "submit_confirm": "보내기 전에 확인해 주세요: 예상 청구액 {usd}(금액은 보험사가 정해요), 서류 {n}건, 받을 계좌 {acct}. 맞으면 30분 안에 '네', 취소하려면 '아니요'라고 답해 주세요. 대화창에서 먼저 확인하셔도 돼요.", "submit_cancelled": "취소했어요. 아무것도 보내지 않았어요. 준비되면 '제출'이라고 답하시거나 대화창을 이용해 주세요.",
        "complete_submit_web": " 서류가 모두 준비됐어요. 아래 '보험사에 제출하기'를 누르시면 청구를 보낼게요.",
        "submitted": "청구를 제출했어요(번호 {claim}). 추가 확인이 없으면 서류를 모두 받은 날부터 3영업일 안에 지급 예정이에요(보험사 심사에 따라 달라질 수 있어요).",
        "almost_done": "거의 끝났어요. 보안 페이지에서 청구서와 신분증을 제출해 주세요.\n{link}",
        "still_needed": "아직 필요한 서류: {items}.", "noted": "알겠어요.",
        "language_switched": "네, 이제부터 한국어로 안내할게요.",
        "chat_link": "여기서 바로 이어서 진행하세요(앱 설치·로그인 없음).\n{link}",
        "sms_alert": "[{insurer}] {date} {flight}편 {event}. 여행자보험 청구 안내를 보내 드려요. 문의 {phone} {link}",
        "sms_reminder": "[{insurer}] {flight}편 청구 서류가 남았어요. 링크에서 이어서 진행하세요. 문의 {phone} {link}",
    },
}

CHECKLIST_LABELS = {
    "en": {
        "airline_letter": "Airline confirmation of the cancellation or delay with flight, date and reason",
        "original_eticket": "Original ticket showing the scheduled departure",
        "replacement_boarding_pass": "Boarding pass for the replacement flight",
        "expense_receipts": "Itemised receipts for meals, hotel, transport and calls",
        "claim_form": "Signed claim form (secure claims page)",
        "photo_id": "Copy of photo ID (secure claims page)",
    },
    "ko": {
        "airline_letter": "항공사 결항·지연 확인서 (편명, 날짜, 사유 포함)",
        "original_eticket": "원래 항공권 (예정 출발 시각이 보이는 것)",
        "replacement_boarding_pass": "대체 항공편 탑승권",
        "expense_receipts": "식비·숙박·교통·통신 영수증 (항목이 보이는 것)",
        "claim_form": "서명한 청구서 (보안 청구 페이지)",
        "photo_id": "신분증 사본 (보안 청구 페이지)",
    },
}
SHORT_LABELS = {
    "en": {"airline_letter": "airline letter", "original_eticket": "original ticket",
           "replacement_boarding_pass": "replacement boarding pass", "expense_receipts": "receipts",
           "claim_form": "claim form", "photo_id": "photo ID"},
    "ko": {"airline_letter": "항공사 확인서", "original_eticket": "원래 항공권", "replacement_boarding_pass": "대체편 탑승권",
           "expense_receipts": "영수증", "claim_form": "청구서", "photo_id": "신분증"},
}

SUBMIT_WORDS = {"submit", "제출", "제출할게요", "제출해주세요", "제출해 주세요"}
YES_WORDS = {"yes", "네", "예", "응", "좋아요"}


def norm(locale):
    return locale if locale in LOCALES else "en"


def t(locale, key, **kw):
    text = STRINGS[norm(locale)].get(key) or STRINGS["en"][key]
    return text.format(**kw)


def render_template(name, params, locale="en"):
    text = TEMPLATES[norm(locale)][name]
    for i, p in enumerate(params, 1):
        text = text.replace("{" + str(i) + "}", str(p))
    return text


def label(locale, item, short=False):
    return (SHORT_LABELS if short else CHECKLIST_LABELS)[norm(locale)][item]


def due_date(iso, locale="en", year=False):
    """"10/14(수)" / "Wed, Oct 14" — the same form the chat shows; year=True for deadlines ("2026년 11/4(수)", "Wed, Nov 4, 2026 (KST)")."""
    d = datetime.fromisoformat(iso[:10])
    if norm(locale) == "ko":
        return (f"{d.year}년 " if year else "") + f"{d.month}/{d.day}({'월화수목금토일'[d.weekday()]})"
    return f"{d.strftime('%a, %b')} {d.day}" + (f", {d.year} (Korea time)" if year else "")


def letter_date(iso, locale="en", time=False):
    """One form for a document kept as proof: "2026년 10월 5일(월)" / "Mon, Oct 5, 2026"; time=True adds the
    Korea time of a UTC timestamp ("2026년 10월 10일(토) 11:55(한국 시간)")."""
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return iso or ""
    if time and d.tzinfo:
        d = d + timedelta(hours=9)
    if norm(locale) == "ko":
        out = f"{d.year}년 {d.month}월 {d.day}일({'월화수목금토일'[d.weekday()]})"
        return out + (f" {d:%H:%M}(한국 시간)" if time else "")
    out = f"{d:%a, %b} {d.day}, {d.year}"
    return out + (f", {d:%H:%M} Korea time" if time else "")


def pretty_date(iso, locale="en"):
    try:
        d = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso
    return f"{d.month}월 {d.day}일" if norm(locale) == "ko" else f"{d.strftime('%b')} {d.day}"  # one US style: "Oct 6"


HANGUL = re.compile(r"[가-힣]")


def detect(text):
    """'ko' if the message is mostly Korean, 'en' if it is Latin text, None if unclear (photos, numbers)."""
    letters = re.findall(r"[A-Za-z가-힣]", text or "")
    if len(letters) < 2:
        return None
    korean = sum(1 for c in letters if HANGUL.match(c))
    return "ko" if korean / len(letters) > 0.3 else "en"


def explicit_switch(text):
    """User asked for a language: "English", "영어로", "한국어", "Korean"."""
    s = (text or "").strip().lower()
    if s in ("english", "영어", "영어로", "in english", "english please"):
        return "en"
    if s in ("korean", "한국어", "한국어로", "한글", "한글로"):
        return "ko"
    return None
