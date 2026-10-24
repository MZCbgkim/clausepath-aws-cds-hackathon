"""Claim packet: one HTML summary for the reviewer plus the original documents as attachments."""
import html
import re

from . import checklist
from .estimate import estimate
from .policy import clauses
from .validate import FX_TO_USD

FLAGS = {
    "en": {"out_of_window": "Expense outside the delay window", "excluded_item": "Item not covered",
           "airline_provided": "Already provided by the airline", "duplicate": "Duplicate receipt",
           "over_daily_limit": "Above the daily limit", "over_trip_limit": "Above the trip limit",
           "location_mismatch": "Receipt far from the airports", "flight_mismatch": "Flight number differs",
           "name_mismatch": "Passenger name differs", "cause_not_covered": "Cause not covered",
           "delay_below_threshold": "Delay below 4 hours", "missing_airline_letter": "Airline letter missing",
           "retake": "Unreadable, retake requested", "name_unverified": "Name not verified (no passport name on file)", "other_claim_document": "Also used in another claim",
           "other_insurance_declared": "Traveler declared another insurance for these costs", "airline_paid_declared": "Traveler declared airline compensation"},
    "ko": {"out_of_window": "지연 기간 밖의 지출", "excluded_item": "보상 제외 항목", "airline_provided": "항공사가 이미 제공",
           "duplicate": "중복 영수증", "over_daily_limit": "하루 한도 초과", "over_trip_limit": "여행 한도 초과",
           "location_mismatch": "공항과 먼 지역의 영수증", "flight_mismatch": "편명 불일치", "name_mismatch": "승객 이름 불일치",
           "cause_not_covered": "보장되지 않는 사유", "delay_below_threshold": "4시간 미만 지연",
           "missing_airline_letter": "항공사 확인서 없음", "retake": "판독 불가, 재촬영 요청",
           "name_unverified": "이름 확인 불가 (여권 영문명 없음)", "other_claim_document": "다른 청구에 쓰인 서류",
           "other_insurance_declared": "다른 보험 청구 고지", "airline_paid_declared": "항공사 보상 수령 고지"},
}
FLAG_TEXT = FLAGS["en"]  # the agent's CONTEXT and logs stay in English

L = {
    "en": {"title": "Travel delay claim", "insured": "Insured", "plan": "Plan", "flight": "Flight", "on": "on",
           "event": "Event", "status": "Status", "view": "Assistant's coverage view",
           "guidance": "guidance only; the insurer decides [CL-5]", "checklist": "Checklist", "documents": "Documents",
           "type": "Type", "via": "Received via", "extracted": "Extracted", "checks": "Checks",
           "clean": "Receipts without flags", "fx": "demo FX; limits per [PLAN-2]", "night": "night of stay; paid {paid}", "decision": "Decision", "confirmed": "confirmed by", "auto_est": "automatic estimate", "decided_amount": "Amount decided", "decision_clauses": "Clauses the decision rests on",
           "dec_full": "Paid in full", "dec_partial": "Paid in part", "dec_deny": "Denied", "dup": "Other compensation", "declared": "declared by the traveler",
           "dup_ins_yes": "another insurance: YES", "dup_ins_no": "no other insurance", "dup_air_yes": "airline compensation: YES", "dup_air_no": "no airline compensation", "rate": "1 USD = {r} {cur}", "claim_checks": "Claim-level checks",
           "clauses": "Clauses relied on", "channels": "Conversation used: {c}. {n} messages on file.",
           "unreadable": "Unreadable photo (traveler asked to retake)", "missing": "Missing", "none": "none",
           "retake": "{n} more photo(s) waiting for a retake",
           "policy": "Policy", "bought": "Bought", "period": "Cover period", "payto": "Pay to", "worksheet": "Amount worksheet",
           "date": "Date", "merchant": "Merchant", "amount": "Amount", "counted": "Counts", "daytotal": "day total", "estimate": "Estimate",
           "limits": "limits", "day": "day", "trip": "trip", "left": "trip limit left", "nocover": "This plan has no travel delay cover [PLAN-1].", "check": "{n} document(s) need a check ({why})",
           "via_letter": "accepted from the airline letter (booking ref and scheduled departure on it)",
           "statuses": {"likely_covered": "policy terms met (as asked)", "likely_not_covered": "policy terms not met (as asked)", "needs_info": "needs information",
                        "review": "needs review", "info_requested": "information requested"}},
    "ko": {"title": "여행 지연 청구", "insured": "피보험자", "plan": "플랜", "flight": "항공편", "on": "",
           "event": "사고", "status": "상태", "view": "어시스턴트의 보장 판단",
           "guidance": "안내용이며 최종 결정은 보험사 [CL-5]", "checklist": "체크리스트", "documents": "서류",
           "type": "종류", "via": "받은 채널", "extracted": "읽은 내용", "checks": "점검",
           "clean": "문제없는 영수증 합계", "fx": "데모 환율, 한도는 [PLAN-2]", "night": "투숙일 기준, 결제 {paid}", "decision": "심사 결정", "confirmed": "확인", "auto_est": "예상 청구액", "decided_amount": "심사 인정액", "decision_clauses": "심사 결정의 근거 조항",
           "dec_full": "전액 지급", "dec_partial": "일부 지급", "dec_deny": "부지급", "dup": "중복 보상", "declared": "고객 고지",
           "dup_ins_yes": "다른 보험 청구: 있음", "dup_ins_no": "다른 보험 없음", "dup_air_yes": "항공사 보상: 있음", "dup_air_no": "항공사 보상 없음", "rate": "1 USD = {r} {cur}", "claim_checks": "청구 단위 점검",
           "clauses": "근거 조항", "channels": "사용한 채널: {c}. 메시지 {n}건 보관.",
           "unreadable": "판독 불가 사진 (재촬영 요청함)", "missing": "부족한 서류", "none": "없음",
           "retake": "재촬영 대기 {n}건",
           "policy": "증권", "bought": "가입일", "period": "보험기간", "payto": "입금 계좌", "worksheet": "산정표",
           "date": "일자", "merchant": "가맹점", "amount": "금액", "counted": "인정", "daytotal": "일 합계", "estimate": "예상 청구액",
           "limits": "한도", "day": "일", "trip": "여행", "left": "여행 한도 잔여", "nocover": "이 플랜에는 항공기 지연 담보가 없습니다 [PLAN-1].", "check": "확인 필요 {n}건 ({why})",
           "via_letter": "항공사 확인서로 확인 (예약번호·예정 출발 시각 기재)",
           "statuses": {"submitted": "제출됨", "approved": "지급 결정", "info_requested": "추가 정보 요청", "review": "검토 필요",
                        "ready": "제출 준비", "collecting": "서류 수집 중", "likely_covered": "약관상 해당 (문의 기준)",
                        "likely_not_covered": "약관상 해당 안 됨 (문의 기준)", "needs_info": "정보 필요", "cancelled": "결항", "delayed": "지연"}},
}


def _e(x):
    return html.escape(str(x if x is not None else "-"))


def receipt_usd(doc):
    r = (doc.get("extracted") or {}).get("receipt") or {}
    rate = FX_TO_USD.get((r.get("currency") or "").upper())
    return round(r["total"] * rate, 2) if rate and r.get("total") else None


DOC_TYPES = {"en": {"airline_letter": "Airline letter", "receipt": "Receipt", "eticket": "E-ticket", "boarding_pass": "Boarding pass"},
             "ko": {"airline_letter": "항공사 확인서", "receipt": "영수증", "eticket": "전자항공권", "boarding_pass": "탑승권"}}
STYLE = ("font-family:Arial,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;font-size:15px;line-height:1.55;"
         "color:#1b2230;max-width:880px;margin:24px auto;padding:0 16px")


CHANNELS = {"en": {"web": "web chat", "sms": "SMS", "email": "email"}, "ko": {"web": "웹 채팅", "sms": "문자", "email": "이메일", "whatsapp": "WhatsApp"}}


def plain_clause(text):
    """Clause text for the packet as lines: no markdown bold, a table row reads "플러스: 1일 한도 150 미국달러 · 여행당 한도 500 미국달러"."""
    out, head = [], None
    for line in text.replace("**", "").splitlines():
        if re.match(r"^\|?\s*-{3}", line) or not line.strip():
            continue
        if "|" not in line:
            out.append(line.strip())
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if head is None:
            head = cells
            continue
        out.append(f"{cells[0]}: " + " · ".join(f"{h} {c}" for h, c in zip(head[1:], cells[1:])))
    return out


def clause_html(text, limit=400):
    """Escaped clause lines joined with <br>, cut at about `limit` characters."""
    lines, n = [], 0
    for line in plain_clause(text):
        if n + len(line) > limit:
            lines.append(_e(line[:max(0, limit - n)]) + "…")
            break
        lines.append(_e(line))
        n += len(line)
    return "<br>".join(lines)


LETTER = {
    "ko": {"title": "보험금 심사 결정 통지서", "to": "받는 분", "claim": "접수번호", "flight": "항공편", "result": "결정", "amount": "지급액",
           "estimate": "예상 청구액(서류 기준 자동 계산)", "in_review": "이의 검토 중", "in_review_text": "${amt}에 대해 {date}까지 다시 검토합니다. 결정된 지급액의 지급은 늦춰지지 않습니다.",
           "reviewed": "이의 검토 결과", "appeal_rec": "접수한 이의:", "evidence_n": "증빙 사진 {n}장", "reviewed_upheld": "다시 검토한 결과 결정을 유지했습니다({date}).", "reviewed_corrected": "다시 검토해 결정을 정정했습니다({date}).", "lines": "줄어든 내역", "basis": "약관 근거", "appeal": "이의 신청",
           "appeal_text": "{deadline}까지 대화창의 '이의 신청하기' 또는 {phone}로 신청하시면 처음 결정한 사람이 아닌 다른 심사자가 다시 검토합니다.",
           "appeal_more": "이의는 접수되었습니다. 다시 신청하실 필요는 없고, 추가 증빙은 {date}까지 대화창에서 보내실 수 있습니다. 문의: {phone}", "why": "심사자 설명",
           "fss": "재검토 뒤에도 동의하기 어려우시면 금융감독원 분쟁조정(국번 없이 1332, www.fcsc.kr)을 신청하실 수 있습니다.",
           "issued": "발행", "print": "인쇄하거나 파일로 저장", "full": "전액 지급", "partial": "일부 지급", "deny": "지급하지 않음",
           "note": "이 통지서는 심사 결정을 알리는 사본이며, 실제 지급은 등록하신 계좌로 이루어집니다. 금액은 미국 달러로 정하며, 엔화 영수증은 지출일(숙박비는 투숙일)의 서울외국환중개 매매기준율로 달러로 환산합니다(데모는 고정 환율). 국내 계좌는 지급일 환율로 원화, 해외 계좌는 미국 달러 해외송금(지급일부터 보통 영업일 2–5일)으로 지급하며, 보험사는 송금 수수료를 떼지 않지만 중개 은행이나 받는 은행이 수수료를 뗄 수 있습니다.", "reviewed_withdrawn": "고객이 이의 신청을 철회했습니다({date}).",},
    "en": {"title": "Claim decision letter", "to": "To", "claim": "Claim number", "flight": "Flight", "result": "Decision", "amount": "Amount to pay",
           "estimate": "Estimated claim (calculated from your documents)", "in_review": "Objection under review", "in_review_text": "${amt} is being reviewed again by {date}. Payment of the decided amount is not held back.",
           "reviewed": "Objection reviewed", "appeal_rec": "Objection on record:", "evidence_n": "{n} evidence photo(s)", "reviewed_upheld": "Reviewed again; the decision stays ({date}).", "reviewed_corrected": "Reviewed again; the decision was corrected ({date}).", "lines": "What was reduced", "basis": "Policy basis", "appeal": "Objection",
           "appeal_text": "By {deadline}, use \"Object to this decision\" in the chat or call {phone}; a reviewer who did not make this decision looks again.",
           "appeal_more": "Your objection is in; there is no need to send it again. You can send more evidence in the chat until {date}. Questions: {phone}", "why": "Reviewer's explanation",
           "fss": "If you still disagree, Korea's Financial Supervisory Service offers dispute mediation: 1332 (in Korea), www.fcsc.kr.",
           "issued": "Issued", "print": "Print / save as PDF", "full": "Paid in full", "partial": "Paid in part", "deny": "Not payable",
           "note": "This letter is a copy of the decision; payment goes to the account you registered. The amount is set in US dollars; yen receipts are converted at the Seoul Money Brokerage base rate on each expense date (for a hotel, the night of stay; demo: a fixed rate). A Korean account receives won at the rate on the payment day; an account abroad receives the US dollars by international transfer, usually 2–5 business days after the payment date. The insurer deducts no transfer fee; an intermediary bank or your bank may deduct one.", "reviewed_withdrawn": "The traveler withdrew the objection ({date}).",},
}


def decision_letter(claim, traveler, insurer, locale, phone, deadline, clause_text, evidence=0):
    """A printable letter the traveler can keep or hand to an employer or card company."""
    from .i18n import norm, t
    lc = norm(locale)
    tx, d = LETTER[lc], claim.get("decision") or {}
    lines = "".join(f"<li>{_e(ln.get('merchant'))}: ${ln['from']:,.2f} → ${ln['to']:,.2f} ({_e(t(lc, 'adj_' + ln['reason']))})</li>" for ln in d.get("lines") or [])
    basis = "".join(f"<li><b>[{_e(c)}]</b> {clause_html(clause_text(c))}</li>" for c in d.get("clauses") or [])
    a = claim.get("appeal") or {}
    from .i18n import letter_date
    # where the objection stands (top banner) and what is on record (appeal section), so the letter works as proof
    review = appeal_record = ""
    if a:
        r = a.get("resolved")
        head, state = (tx["reviewed"], tx["reviewed_" + r.get("outcome", "upheld")].format(date=_e(letter_date(r.get("at"), lc)))) if r else \
            (tx["in_review"], tx["in_review_text"].format(amt=f"{max(0, (d.get('estimate_usd') or 0) - d.get('amount_usd', 0)):,.2f}",
                                                          date=_e(letter_date(a.get('reply_by') or '', lc))))
        review = f"<p class='rv'><b>{head}</b> · {state}</p>"
        appeal_record = (f"<p class='rv'><b>{tx['appeal_rec']}</b> {_e(letter_date(a.get('at'), lc, time=True))} · “{_e(a.get('text'))}”"
                         f"{' · ' + tx['evidence_n'].format(n=evidence) if evidence else ''}<br>{state}</p>")
    # an objection on record: say it is in and what can still be done, instead of how to object
    appeal_how = (tx["appeal_more"].format(date=_e(letter_date(a.get("reply_by") or "", lc)), phone=_e(phone)) if a and not a.get("resolved")
                  else tx["appeal_text"].format(deadline=_e(deadline), phone=_e(phone)))
    krw = f" (약 {round(d.get('amount_usd', 0) * 1380, -4) / 10000:,.0f}만 원)" if lc == "ko" and d.get("type") != "deny" else ""
    return f"""<!doctype html><html lang="{lc}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{tx['title']} {_e(claim['id'])}</title><style>body{{font:16px/1.6 system-ui,-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;max-width:720px;margin:0 auto;padding:1.2rem;color:#1b2230}}
h1{{font-size:1.4rem;margin:.2rem 0 1rem}} .ins{{color:#1f4fd8;font-weight:700}} table{{border-collapse:collapse;width:100%;margin:.6rem 0}} td{{border-bottom:1px solid #e3e6ee;padding:.45rem .2rem;vertical-align:top}}
td:first-child{{color:#5b6474;width:34%;white-space:nowrap}} .rv{{background:#fff8e6;border-radius:10px;padding:.6rem .8rem}} .big{{font-size:1.3rem;font-weight:700}} h2{{font-size:1.05rem;margin:1.2rem 0 .3rem}} .muted{{color:#5b6474;font-size:.9rem}}
button{{margin:1rem 0;padding:.7rem 1.1rem;border:0;border-radius:10px;background:#1f4fd8;color:#fff;font:inherit;font-weight:600}} @media print{{button{{display:none}}}}</style></head>
<body><div class="ins">{_e(insurer)}</div><h1>{tx['title']}</h1>{review}
<table><tr><td>{tx['to']}</td><td>{_e(traveler['name'])}</td></tr><tr><td>{tx['claim']}</td><td>{_e(claim['id'])}</td></tr>
<tr><td>{tx['flight']}</td><td>{_e(claim.get('flight'))} · {_e(letter_date(claim.get('flight_date'), lc))}</td></tr>
<tr><td>{tx['result']}</td><td class="big">{tx.get(d.get('type'), '')}</td></tr>
<tr><td>{tx['amount']}</td><td class="big">${d.get('amount_usd', 0):,.2f}{krw}</td></tr>
<tr><td>{tx['estimate']}</td><td>${(d.get('estimate_usd') or 0):,.2f}</td></tr></table>
{f"<h2>{tx['lines']}</h2><ul>{lines}</ul>" if lines else ""}{f"<p><b>{tx['why']}:</b> {_e(d['note'])}</p>" if d.get('note') else ""}{f"<h2>{tx['basis']}</h2><ul>{basis}</ul>" if basis else ""}
{f"<h2>{tx['appeal']}</h2>{appeal_record}<p>{appeal_how}</p><p>{tx['fss']}</p>" if d.get('type') != 'full' else ""}
<p class="muted">{tx['issued']}: {_e(letter_date(d.get('at'), lc, time=True))} · {tx['note']}</p>
<button onclick="print()">{tx['print']}</button></body></html>"""


def kst(iso):
    from datetime import datetime, timedelta
    try:
        return (datetime.fromisoformat(iso) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return iso or ""


def decision_block(dec, tx, lc):
    """The final decision as the core system needs it: type, amount, clauses, receipt lines, who asked and who confirmed."""
    if not dec:
        return ""
    from .i18n import t
    lines = "".join(f"<li>{_e(ln.get('merchant'))}: ${ln['from']:,.2f} → ${ln['to']:,.2f} ({_e(t(lc, 'adj_' + ln['reason']))})</li>"
                    for ln in dec.get("lines") or [])
    who = _e(dec.get("by")) + (f" · {tx['confirmed']} {_e(dec['confirmed_by'])}" if dec.get("confirmed_by") else "")
    return (f"<div style=\"border:2px solid #1f4fd8;border-radius:8px;padding:8px 12px;margin:10px 0\"><b>{tx['decision']}:</b> "
            f"{_e(tx['dec_' + dec['type']])} ${dec['amount_usd']:,.2f} ({tx['auto_est']} ${dec.get('estimate_usd') or 0:,.2f})"
            f"{' · ' + _e(', '.join(dec.get('clauses') or [])) if dec.get('clauses') else ''}<br>"
            f"<small>{who} · {_e(kst(dec.get('at')))} KST ({_e((dec.get('at') or '')[11:16])} UTC)</small>{f'<ul>{lines}</ul>' if lines else ''}</div>")


def dup_line(d, tx):
    """What the traveler declared about other compensation: "다른 보험 없음 · 항공사 보상 없음 (고지)"."""
    parts = [tx["dup_ins_yes"] if d.get("other_insurance") else tx["dup_ins_no"], tx["dup_air_yes"] if d.get("airline_paid") else tx["dup_air_no"]]
    return " · ".join(parts) + f" ({tx['declared']})" + (f" — {d['detail']}" if d.get("detail") else "")


def flag_detail(f, lc):
    """Check details read as sentences in Korean: "legibility unreadable, confidence 0.3" -> "읽을 수 없음 · 판독 신뢰도 30%"."""
    d = f.get("detail") or ""
    if lc != "ko":
        return d
    m = re.match(r"legibility (\w+), confidence ([\d.]+)", d)
    if m:
        state = {"unreadable": "읽을 수 없음", "poor": "흐림", "good": "양호"}.get(m.group(1), m.group(1))
        return f"{state} · 판독 신뢰도 {round(float(m.group(2)) * 100)}%"
    if d.startswith("date/time not readable"):
        return "날짜·시각을 읽을 수 없음"
    m = re.match(r"(.+) vs (.+)", d)
    if m and f["code"] in ("name_mismatch", "flight_mismatch"):
        return f"서류: {m.group(1)} · 청구: {m.group(2)}"
    return d


def build(claim, traveler, event, documents, messages, locale="en", policy=None):
    from .i18n import label, norm
    lc = norm(locale)
    tx, flags_text = L[lc], FLAGS[lc]
    st = lambda s: tx["statuses"].get(s, s)
    # the claim status reads as the decision type: a part payment is never just "approved"
    dec_type = (claim.get("decision") or {}).get("type")
    status_label = tx["dec_" + dec_type] if claim.get("status") in ("approved", "denied") and dec_type else st(claim["status"])
    state = checklist.compute(documents, claim.get("secure_submitted"))
    cov = claim.get("coverage") or {}
    rows = []
    for d in documents:
        ex = d.get("extracted") or {}
        kind = ex.get("doc_type", "?")
        if ex.get("legibility") == "unreadable":
            summary = tx["unreadable"]
        elif kind == "receipt":
            r = ex.get("receipt") or {}
            summary = f"{r.get('merchant')} · {r.get('datetime')} · {r.get('currency')} {r.get('total')} (≈ USD {receipt_usd(d)})"
        elif kind == "airline_letter":
            l = ex.get("letter") or {}
            summary = f"{l.get('flight')} {l.get('date')} {st(l.get('status'))} · {l.get('reason_text')}"
        elif kind in ("eticket", "boarding_pass"):
            t = ex.get("ticket") or {}
            summary = f"{t.get('flight')} {t.get('date')} {t.get('departure_time')} · {t.get('passenger')}"
        else:
            summary = kind
        flags = "<br>".join(f"⚠ {_e(flags_text.get(f['code'], f['code']))}: {_e(flag_detail(f, lc))}" for f in d.get("flags", []))
        rows.append(f"<tr><td>{_e(DOC_TYPES[lc].get(kind, kind))}</td><td>{_e(CHANNELS[lc].get(d.get('source_channel'), d.get('source_channel')))}</td>"
                    f"<td>{_e(summary)}</td><td>{flags or '✓'}</td></tr>")
    claim_flags = "".join(f"<li>{_e(flags_text.get(f['code'], f['code']))}: {_e(f['detail'])}</li>"
                          for f in claim.get("flags", []) if not f.get("doc"))
    pending = lambda k: f" — ⚠ {tx['retake'].format(n=len(state[k]['pending']))}" if state[k]["pending"] else ""
    check = lambda k: (f" — ⚠ {_e(tx['check'].format(n=len(state[k]['check']), why=', '.join(flags_text.get(c, c) for c in state[k]['why'])))}"
                       if state[k]["check"] and not state[k]["done"] else "")
    kinds = {d["id"]: (d.get("extracted") or {}).get("doc_type") for d in documents}
    # the original ticket item met by the airline letter: say so, the reviewer should not hunt for a ticket
    basis = lambda k: (f" — {tx['via_letter']}" if k == "original_eticket" and state[k]["done"]
                       and all(kinds.get(x) == "airline_letter" for x in state[k]["docs"]) else "")
    mark = lambda k: "✓" if state[k]["done"] else "⚠" if state[k]["check"] else "✗"
    items = "".join(f"<li>{mark(k)} {_e(label(lc, k))}{basis(k)}{pending(k)}{check(k)}</li>" for k in checklist.REQUIRED)
    cited = "".join(f"<li><b>[{c}]</b> {clause_html(clauses(lc).get(c, ''))}</li>" for c in cov.get("cited_clauses", []))
    decided_on = "".join(f"<li><b>[{c}]</b> {clause_html(clauses(lc).get(c, ''))}</li>" for c in (claim.get("decision") or {}).get("clauses") or [])
    channels = sorted({CHANNELS[lc].get(m["channel"], m["channel"]) for m in messages if m.get("channel")})
    pol, form = policy or {}, claim.get("claim_form") or {}
    # who is covered and how: what the core system needs
    cover = (f"<p><b>{tx['policy']}:</b> {_e(pol.get('id'))} · <b>{tx['bought']}:</b> {_e((pol.get('purchased_at') or '')[:10])} · "
             f"<b>{tx['period']}:</b> {_e(pol.get('period_start'))} ~ {_e(pol.get('period_end'))}"
             + (f"<br><b>{tx['payto']}:</b> {_e(form.get('bank'))} •••{_e(form.get('account_last4'))} ({_e(form.get('holder'))})" if form.get("account_last4") else "")
             + (f"<br><b>{tx['dup']}:</b> {_e(dup_line(form['declared'], tx))}" if (form.get("declared") or {}).get("asked") else "")
             + "</p>") if pol else ""
    dec = claim.get("decision") or {}
    est = estimate(documents, pol.get("plan") or claim.get("plan"), dec.get("adjust"))  # the worksheet matches the decision
    usd = lambda x: "-" if x is None else f"${x:,.2f}"
    from .i18n import t as _t
    adj_cell = lambda r: (f"<s>{usd(r['rule_usd'])}</s> <b>{usd(r['counted_usd'])}</b><br><small>{_e(_t(lc, 'adj_' + r['reason']))}</small>"
                          if "rule_usd" in r else usd(r["counted_usd"]))
    small = lambda x: f"<br><small>{_e(x)}</small>" if x else ""
    ws_rows = "".join(f"<tr><td>{_e(r['date'])}{small(r.get('paid') and tx['night'].format(paid=r['paid']))}</td><td>{_e(r['merchant'])}</td>"
                      f"<td>{_e(r['currency'])} {_e(r['total'])}{small(r.get('per_usd') and r['currency'] != 'USD' and tx['rate'].format(r=r['per_usd'], cur=r['currency']))}</td><td>{usd(r['usd'])}</td>"
                      f"<td>{_e(', '.join(flags_text.get(c, c) for c in r['excluded'])) or adj_cell(r)}</td></tr>" for r in est["rows"])
    ws_days = "".join(f"<tr><td colspan=4>{_e(d)} {tx['daytotal']}</td><td>{usd(x['usd'])}{' → <b>' + usd(x['capped']) + '</b>' if x['capped'] < x['usd'] else ''}</td></tr>"
                      for d, x in (est.get("days") or {}).items())
    worksheet = (f"<h3>{tx['worksheet']}</h3><table border=\"1\" cellpadding=\"8\" style=\"border-collapse:collapse;border-color:#d5d9e2\">"
                 f"<tr><th>{tx['date']}</th><th>{tx['merchant']}</th><th>{tx['amount']}</th><th>USD</th><th>{tx['counted']}</th></tr>{ws_rows}{ws_days}</table>"
                 + (f"<p><b>{tx['decided_amount'] if dec else tx['estimate']}: {usd(est['estimate_usd'])}</b> ({tx['limits']} ${est['daily']}/{tx['day']}, ${est['trip']}/{tx['trip']} · {tx['left']} {usd(est['remaining_usd'])}; {tx['fx']})</p>"
                    if est.get("covered") else f"<p>{tx['nocover']}</p>")) if est["rows"] else ""
    on = f" {tx['on']} " if tx["on"] else " "
    body = f"""<!doctype html><html lang="{lc}"><head><meta charset="utf-8"><title>{tx['title']} {_e(claim['id'])}</title></head>
<body style="{STYLE}">
<h2>{tx['title']} {_e(claim['id'])}</h2>
<p><b>{tx['insured']}:</b> {_e(traveler['name'])} · <b>{tx['plan']}:</b> {_e(claim.get('plan'))} · <b>{tx['flight']}:</b> {_e(claim.get('flight'))}{on}{_e(claim.get('flight_date'))}<br>
<b>{tx['event']}:</b> {_e(st((event or {}).get('type')))} — {_e((event or {}).get('reason'))} · <b>{tx['status']}:</b> {_e(status_label)}<br>
<b>{tx['view']}:</b> {_e(st(cov.get('status')))} ({tx['guidance']})</p>
{decision_block(claim.get("decision"), tx, lc)}
{cover}
<h3>{tx['checklist']}</h3><ul>{items}</ul>
<h3>{tx['documents']}</h3>
<table border="1" cellpadding="8" style="border-collapse:collapse;border-color:#d5d9e2"><tr><th>{tx['type']}</th><th>{tx['via']}</th><th>{tx['extracted']}</th><th>{tx['checks']}</th></tr>{''.join(rows)}</table>
{worksheet}
{f"<h3>{tx['claim_checks']}</h3><ul>{claim_flags}</ul>" if claim_flags else ''}
{f"<h3>{tx['decision_clauses']}</h3><ul>{decided_on}</ul>" if decided_on else ''}
<h3>{tx['clauses']}</h3><ul>{cited or '<li>-</li>'}</ul>
<p style="color:#4f5868">{tx['channels'].format(c=_e(', '.join(channels)), n=len(messages))}</p>
</body></html>"""
    missing = ", ".join(checklist.describe_missing(state, lc)) or tx["none"]
    text = (f"{tx['title']} {claim['id']} · {traveler['name']} · {claim.get('flight')} ({claim.get('flight_date')}) · "
            f"{tx['status']} {status_label} · {tx['missing']}: {missing}")
    return text, body
