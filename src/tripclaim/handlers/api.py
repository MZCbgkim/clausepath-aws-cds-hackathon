"""HTTP API (API Gateway HTTP API, payload v2): reviewer dashboard, ops actions, secure claims page.

/api/* is protected by a Cognito JWT authorizer in AWS; /, /config.js and /secure/* are public
(the secure page is reached through a per-claim random token).
"""
import base64
import html
import json
import os
import re
import uuid
from datetime import datetime, timezone
from importlib import resources
from urllib.parse import parse_qs

from .. import checklist, i18n, rag
from ..estimate import estimate
from ..service import SIM_NUMBER_PREFIX, DecisionConflict, SecondPersonRequired, WordingBlocked, first_name
from ..app import service

WEB = resources.files("tripclaim.web")
SERVICE = None  # the local server injects the simulator's service here


PRIVATE_LINK_HEADERS = {"referrer-policy": "no-referrer", "x-robots-tag": "noindex, nofollow"}


def _resp(status, body, ctype="application/json", headers=None):
    if ctype == "application/json":
        body = json.dumps(body, ensure_ascii=False, default=str)
    if not ctype.endswith("json") and "charset" not in ctype:
        ctype += "; charset=utf-8"
    return {"statusCode": status, "headers": {"content-type": ctype, "cache-control": "no-store", **(headers or {})},
            "body": body}


def _body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    return raw


ROLE_NEEDED = {"disrupt": {"admins"}, "remind": {"admins"}, "demo": {"admins"},
               "approve": {"reviewers"}, "deny": {"reviewers"},  # admins run operations and audit; they do not decide claims
               "confirm": {"reviewers"}, "return": {"reviewers"}, "correct": {"reviewers"}, "appeal_uphold": {"reviewers"},
               "appeal_by_phone": {"admins", "reviewers", "agents"}, "request_info": {"admins", "reviewers"}, "note": {"admins", "reviewers", "agents"},
               # contact centre: tell the traveler something, resend the chat link, ask for the bank account again
               "message": {"admins", "reviewers", "agents"}, "resend_link": {"admins", "reviewers", "agents"},
               "bank_correction": {"admins", "reviewers", "agents"},
               "assign": {"admins", "reviewers", "agents"}, "handoff": {"admins", "reviewers", "agents"},
               "bank_correction_cancel": {"admins", "reviewers", "agents"}, "contact_update": {"admins", "reviewers", "agents"},
               "call_log": {"admins", "reviewers", "agents"}, "handoff_done": {"admins", "reviewers", "agents"},
               "audit": {"admins"},  # reading the audit log and KPIs
               "view_docs": {"admins", "reviewers"}, "siu": {"reviewers"}, "siu_clear": {"admins"}, "siu_hold_payment": {"reviewers"}, "siu_release_payment": {"reviewers", "admins"}}  # original document photos


CLAIM_ACTIONS = {"approve", "deny", "correct", "confirm", "return", "appeal_uphold", "appeal_by_phone", "call_log", "handoff_done", "request_info", "note", "message", "resend_link", "bank_correction",
                 "assign", "handoff", "bank_correction_cancel", "contact_update", "siu", "siu_clear", "siu_hold_payment", "siu_release_payment"}  # POST …/actions


# Local server only (no Cognito authorizer): pick who you are to demo roles and the four-eyes rule.
# In AWS every /api route has the JWT authorizer, so this header is never consulted there.
DEMO_USERS = {"admin@demo": {"admins"}, "reviewer-1@demo": {"reviewers"}, "reviewer-2@demo": {"reviewers"},
              "reviewer-3@demo": {"reviewers"}, "agent@demo": {"agents"}}  # a third reviewer handles objections


def _demo_user(event):
    u = (event.get("headers") or {}).get("x-demo-user")
    return u if u in DEMO_USERS else None


def _roles(event):
    """Staff roles from the Cognito groups in the ID token. No authorizer (local server, tests): every role,
    or the roles of the demo user picked in the dashboard."""
    jwt = ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt")
    if not jwt:
        # Only the local in-memory server runs without the authorizer; anywhere else no token means no rights.
        if (SERVICE or service()).s.store != "memory":
            return set()
        return DEMO_USERS.get(_demo_user(event), {"admins", "reviewers", "agents"})
    groups = jwt.get("claims", {}).get("cognito:groups") or ""
    if isinstance(groups, str):  # HTTP API passes list claims as "[admins reviewers]"
        groups = re.split(r"[\s,]+", groups.strip("[]"))
    return {g for g in groups if g in ("admins", "reviewers", "agents")}  # signed in without a group: no rights until an admin adds one


def _allowed(event, action):
    return bool(_roles(event) & ROLE_NEEDED[action])


def _intro(svc, claim, traveler):
    """Facts for the welcome card: what happened and what the traveler bought. No coverage judgement here."""
    from ..validate import PLAN_LIMITS_USD
    pol = svc._policy(traveler["id"])
    event = svc.repo.get_event(claim["event_id"]) if claim.get("event_id") else None
    plan = pol.get("plan") or claim.get("plan")
    return {"name": first_name(traveler), "flight": claim.get("flight"), "date": claim.get("flight_date"),
            "event": (event or {}).get("type"), "reason": (event or {}).get("reason"), "plan": plan,
            "limits": PLAN_LIMITS_USD.get(plan), "period": [pol.get("period_start"), pol.get("period_end")]}


def _mask_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    return f"•••-{digits[-4:]}" if len(digits) >= 4 else ""


def _reviewer(event):
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    return claims.get("email") or claims.get("username") or _demo_user(event) or "local-reviewer"


SECURE_TEXT = {
    "en": {"invalid": "This link is not valid.", "thanks": "Received — thank you",
           "done": "Your claim form, ID and bank details are in. Go back to the chat and tap \"Submit to the insurer\" when all documents are there.",
           "official": "Travel insurance claims · official page", "call": "Call", "title": "Secure claims page", "bank_only": "Enter your bank account again. Your signed form and ID stay as they are.",
           "size": ["A+", "A−"], "bigger": "Larger text", "smaller": "Normal text",
           "intro": "Claim <b>{claim}</b> · flight {flight}. What you enter here goes only to the insurer's payment system, never by message or email.",
           "sig": "Your full name (this is your signature)", "id": "Photo ID", "idbtn": "Take or choose a photo of your ID", "demo": "Demo: the photo is not uploaded.",
           "pay": "Where should we pay you?", "payout": "How you are paid: the amount is set in US dollars. Receipts in yen are converted to dollars at the Seoul Money Brokerage base rate on each expense date (demo: a fixed rate). A Korean account receives won at the rate on the payment day; an account abroad receives the US dollars by international transfer, usually 2–5 business days after the payment date. The insurer deducts no transfer fee; an intermediary bank or your own bank may deduct one (often $10–30).", "country": "Bank country", "us": "United States", "other": "Other country (IBAN)", "kr": "Korea",
           "holder": "Account holder", "routing": "Routing number (9 digits)", "account": "Account number", "type": "Account type",
           "checking": "Checking", "savings": "Savings", "iban": "IBAN", "swift": "SWIFT / BIC", "bank": "Bank", "choose": "Choose your bank",
           "confirm": "I confirm the information in this claim is true.", "submit": "Submit securely",
           "dup": "Other compensation for these costs", "dup_ins": "Are you claiming these costs from another insurance too (including credit-card travel cover)?",
           "dup_air": "Did the airline give you vouchers, a hotel, or money for this cancellation or delay?", "yes": "Yes", "no": "No",
           "dup_which": "Which insurer / what did you get?",
           "check": "Pay to {bank} {acct} ({holder})?", "checkyes": "Yes, send", "checkno": "Change", "back": "Back to the chat",
           "saved": "Payment account: {bank} ending {last4}", "fix": "Entered the wrong account? Call {phone}, or tap \"Ask us to call you\" in the chat (no international call needed). After a quick identity check we send you a new secure link to enter it again; nothing is paid to an account you have corrected.", "dial": "Call", "bad": "Please check the account number.", "where": "Where it is processed: this claim's documents and chat are {where} (AWS). See the privacy notice, transfers abroad."},
    "ko": {"invalid": "유효하지 않은 링크예요.", "thanks": "접수됐어요",
           "done": "청구서, 신분증, 입금 계좌를 받았어요. 대화창으로 돌아가 서류가 다 모이면 '보험사에 제출하기'를 눌러 주세요.",
           "official": "여행자보험 청구 · 공식 페이지", "call": "전화", "title": "보안 청구 페이지", "bank_only": "입금 계좌만 다시 입력해 주세요. 서명한 청구서와 신분증은 그대로 유지돼요.",
           "size": ["가+", "가−"], "bigger": "글자 크게", "smaller": "글자 보통",
           "intro": "청구 번호 <b>{claim}</b> · {flight}편. 여기 입력한 내용은 보험사 지급 시스템으로만 전달되고, 메시지나 이메일로는 보내지 않아요.",
           "sig": "이름 (입력하시면 서명으로 처리돼요)", "id": "신분증 사진", "idbtn": "신분증 사진 찍기 / 고르기", "demo": "데모에서는 사진이 업로드되지 않아요.",
           "pay": "보험금 받을 계좌", "payout": "받는 방법: 보험금은 미국 달러로 정해져요. 엔화 영수증은 지출일의 서울외국환중개 매매기준율로 달러로 바꿔 계산해요(데모는 고정 환율). 국내 계좌는 지급일 환율로 원화로, 해외 계좌는 미국 달러 해외송금으로 받으시며 지급일부터 보통 영업일 2–5일 걸려요. 보험사는 송금 수수료를 떼지 않지만 중개 은행이나 받는 은행이 수수료를 뗄 수 있어요.", "country": "은행 국가", "us": "미국", "other": "기타 국가 (IBAN)", "kr": "한국",
           "holder": "예금주", "routing": "Routing number (9자리)", "account": "계좌번호 (숫자만)", "type": "계좌 종류",
           "checking": "Checking", "savings": "Savings", "iban": "IBAN", "swift": "SWIFT / BIC", "bank": "은행", "choose": "은행을 고르세요",
           "confirm": "청구 내용이 사실임을 확인합니다.", "submit": "안전하게 제출",
           "dup": "다른 곳에서 받은 보상", "dup_ins": "같은 비용을 다른 보험(카드 부가 여행보험 포함)에도 청구했거나 청구할 예정인가요?",
           "dup_air": "이번 결항·지연으로 항공사에서 식사·호텔 바우처나 현금 보상을 받았나요?", "yes": "예", "no": "아니요",
           "dup_which": "어느 보험사인지 / 무엇을 받았는지 적어 주세요",
           "check": "{bank} {acct} ({holder}) 계좌로 받으시겠어요?", "checkyes": "네, 보내기", "checkno": "고치기", "back": "대화창으로 돌아가기",
           "saved": "받으실 계좌: {bank} 끝자리 {last4}", "fix": "계좌를 잘못 넣으셨나요? {phone}로 전화 주시거나 대화창의 '전화 요청하기'를 눌러 주세요. 본인 확인 뒤 계좌를 다시 넣을 수 있는 새 보안 링크를 보내 드려요. 고치기 전 계좌로는 지급하지 않아요.", "dial": "전화 걸기", "bad": "계좌번호를 다시 확인해 주세요.",
           "where": "처리 위치: 이 청구의 서류와 대화는 {where}. 자세한 내용은 개인정보 처리방침의 '국외 이전'에 있어요."},
}
KR_BANKS = ["KB국민은행", "신한은행", "우리은행", "하나은행", "NH농협은행", "IBK기업은행", "카카오뱅크", "토스뱅크", "케이뱅크", "SC제일은행", "부산은행", "대구은행", "우체국"]


def _contact_centre_view(view):
    """The contact centre sees that a claim is held for SIU, not why: what is not on screen cannot slip out on a call.
    The referral reason, its trail in the action history, and the document findings that point at it are removed
    here, on the server; a held payment's reason too."""
    c = view["claim"]
    # another traveler's name and claim number never reach the contact centre, held or not
    c["flags"] = [{k: v for k, v in f.items() if k != "detail"} if f.get("code") == "other_claim_document" else f for f in c.get("flags") or []]
    for d in view.get("documents", []):
        d["flags"] = [{k: v for k, v in f.items() if k != "detail"} if f.get("code") == "other_claim_document" else f for f in d.get("flags") or []]
    if c.get("siu"):
        c["flags"] = []
        view["actions"] = [{**a, "note": None} if a.get("type") == "note" and (a.get("at") or "") >= (c["siu"].get("at") or "~") else a
                           for a in view.get("actions", [])]  # internal notes written during the hold
        c["siu"] = {k: v for k, v in c["siu"].items() if k in ("at", "by")}
        view["actions"] = [{**a, "reason": None, "note": None} if str(a.get("type", "")).startswith("siu") else a for a in view.get("actions", [])]
        for d in view.get("documents", []):
            d["flags"], d["view"] = [], {**d.get("view", {}), "problems": []}
        view["checklist"] = {k: {**x, "check": [], "why": [], "pending": []} for k, x in (view.get("checklist") or {}).items()}
        view["unresolved"] = []
    link = c.get("siu_linked") or {}
    if link.get("hold_payment"):
        c["siu_linked"] = {**link, "hold_payment": {k: v for k, v in link["hold_payment"].items() if k != "reason"}}
    return view


def _back(event):
    """Where the secure page sends the traveler afterwards: their chat (only a chat path is accepted)."""
    back = _query(event).get("back") or ""
    return back if re.fullmatch(r"/c/[\w-]+", back) else ""


REGION_NAMES = {"us-east-1": ("미국(버지니아 북부)", "the United States (N. Virginia)"), "us-west-2": ("미국(오리건)", "the United States (Oregon)"),
                "ap-northeast-2": ("한국(서울)", "Korea (Seoul)"), "ap-northeast-1": ("일본(도쿄)", "Japan (Tokyo)")}
PROFILE_AREAS = {"us": ("미국 안", "in the US"), "apac": ("아시아·태평양 안", "in Asia-Pacific"),
                 "eu": ("유럽 안", "in the EU"), "global": ("해외 여러 지역", "in several countries")}


def processing_place(locale="en"):
    """Said to the traveler before they hand over ID and bank details: where the data is stored, and where the
    AI reads documents (a cross-region profile can route within its area)."""
    i = 0 if locale == "ko" else 1
    region = os.environ.get("AWS_REGION", "us-east-1")
    place = REGION_NAMES.get(region, (region, region))[i]
    area = PROFILE_AREAS.get(os.environ.get("MODEL_ID", "").split(".")[0])
    # one plain sentence: where it is kept, and where the AI reads it
    if i == 0:
        return f"{place}에 있는 서버에 보관하고, AI 서류 판독은 {area[0]}에서 해요" if area else f"{place}에 있는 서버에서 처리·보관해요"
    return f"kept on servers in {place}; the AI reads documents {area[1]}" if area else f"processed and kept on servers in {place}"


def secure_page(token, claim, done=False, locale="en", brand=None, back="", error=""):
    tx = SECURE_TEXT.get(locale, SECURE_TEXT["en"])
    e = html.escape
    brand = brand or {"name": "ClausePath", "phone": ""}
    head = (f'<div class="brand"><span class="lock">🔒</span><div><b>{e(brand["name"])}</b><span>{tx["official"]}</span></div>'
            + f'<button type="button" class="tsize" id="tsize" aria-label="{tx["bigger"]}">{tx["size"][0]}</button>'
            + (f'<a href="tel:{e(brand.get("tel") or brand["phone"])}">{tx["call"]}<i class="num"> {e(brand["phone"])}</i></a>' if brand["phone"] else "") + "</div>")
    # "가+" as in the chat, remembered across both pages (same origin)
    size_js = f"<script>const SIZE = {json.dumps([*tx['size'], tx['bigger'], tx['smaller']], ensure_ascii=False)};</script>" + """<script>const ts = document.getElementById("tsize"), apply = () => { const big = localStorage.getItem("bigText") === "1";
document.documentElement.classList.toggle("big-text", big); ts.textContent = big ? SIZE[1] : SIZE[0];
ts.setAttribute("aria-pressed", big); ts.setAttribute("aria-label", big ? SIZE[3] : SIZE[2]); };
ts.onclick = () => { localStorage.setItem("bigText", localStorage.getItem("bigText") === "1" ? "0" : "1"); apply(); }; apply();</script>"""
    if not claim:
        return _resp(404, f"<h3>{tx['invalid']}</h3>", "text/html")
    back_btn = f'<a class="btn" href="{e(back)}">{tx["back"]}</a>' if back else ""
    if done:
        form = claim.get("claim_form") or {}
        # what was saved, and how to fix it: the worry right after typing an account number
        saved = (f"<p class='saved'>{e(tx['saved'].format(bank=form.get('bank') or '', last4=form.get('account_last4') or ''))}</p>"
                 if form.get("account_last4") else "")
        fix = (f"<p class='fix'>{e(tx['fix'].format(phone=brand.get('phone') or ''))}<br>"
               f"<a class='dial' href='tel:{e(brand.get('tel') or brand['phone'])}'>{tx['dial']} {e(brand['phone'])}</a></p>") if brand.get("phone") else ""
        inner = f"<div class='ok'>✓</div><h2>{tx['thanks']}</h2><p>{tx['done']}</p>{saved}{fix}{back_btn}"
    else:
        intro = tx["intro"].format(claim=e(claim["id"]), flight=e(str(claim.get("flight"))))
        bank_only = (claim.get("claim_form") or {}).get("bank_reset")  # staff asked for the account again: nothing else
        if bank_only:
            intro = tx["bank_only"]
        banks = "".join(f"<option>{b}</option>" for b in KR_BANKS)
        default = "kr" if locale == "ko" else "us"
        where = f"<p class='muted where'>{e(tx['where'].format(where=processing_place(locale)))}</p>"
        inner = f"""<h2>{tx['title']}</h2><p class="intro">{intro}</p>{where}
{f'<p class="err">{e(error)}</p>' if error else ''}
<form method="post" id="sf">
{"" if bank_only else f"""<label>{tx['sig']}<input name="signed_name" required autocomplete="name"></label>
<div class="lbl">{tx['id']}</div>
<label class="idbtn"><input type="file" accept="image/*" id="idf"><span id="idn">📷 {tx['idbtn']}</span></label><small>{tx['demo']}</small>"""}
<h3>{tx['pay']}</h3><p class="muted payout">{tx['payout']}</p>
<label>{tx['country']}<select name="country" id="cty"><option value="kr">{tx['kr']}</option><option value="us">{tx['us']}</option><option value="other">{tx['other']}</option></select></label>
<label>{tx['holder']}<input name="holder" required autocomplete="name"></label>
<div data-c="kr"><label>{tx['bank']}<select name="bank_name"><option value="">{tx['choose']}</option>{banks}</select></label>
<label>{tx['account']}<input name="account" inputmode="numeric" pattern="[0-9-]{{8,20}}" autocomplete="off"></label></div>
<div data-c="us"><label>{tx['routing']}<input name="routing" inputmode="numeric" pattern="[0-9]{{9}}" autocomplete="off"></label>
<label>{tx['account']}<input name="us_account" inputmode="numeric" pattern="[0-9]{{4,17}}" autocomplete="off"></label>
<label>{tx['type']}<select name="acct_type"><option>{tx['checking']}</option><option>{tx['savings']}</option></select></label></div>
<div data-c="other"><label>{tx['iban']}<input name="iban" autocomplete="off" pattern="[A-Za-z]{{2}}[0-9A-Za-z ]{{10,32}}"></label>
<label>{tx['swift']}<input name="swift" autocomplete="off"></label></div>
{"" if bank_only else f"""<h3>{tx['dup']}</h3>
<div class="lbl">{tx['dup_ins']}</div><div class="yn"><label><input type="radio" name="other_insurance" value="yes" required> {tx['yes']}</label>
<label><input type="radio" name="other_insurance" value="no"> {tx['no']}</label></div>
<div class="lbl">{tx['dup_air']}</div><div class="yn"><label><input type="radio" name="airline_paid" value="yes" required> {tx['yes']}</label>
<label><input type="radio" name="airline_paid" value="no"> {tx['no']}</label></div>
<label id="dupd" hidden>{tx['dup_which']}<input name="dup_detail" maxlength="120" autocomplete="off"></label>"""}
<label class="cb"><input type="checkbox" required> {tx['confirm']}</label>
<button>{tx['submit']}</button></form>
<dialog id="ck"><p id="ckt"></p><div class="ckb"><button type="button" id="ckn">{tx['checkno']}</button><button type="button" id="cky">{tx['checkyes']}</button></div></dialog>
<script>
const cty = document.getElementById("cty"); cty.value = "{default}";
const show = () => document.querySelectorAll("[data-c]").forEach(d => {{ const on = d.dataset.c === cty.value; d.hidden = !on;
  d.querySelectorAll("input,select").forEach(i => i.required = on && i.name !== "swift"); }});
cty.onchange = show; show();
document.querySelectorAll("input[name=other_insurance],input[name=airline_paid]").forEach(r => r.onchange = () => {{  // say what, if anything
  const any = [...document.querySelectorAll("input[value=yes]:checked")].length; const d = document.getElementById("dupd");
  d.hidden = !any; d.querySelector("input").required = !!any; }});
document.getElementById("idf") && (document.getElementById("idf").onchange = e => {{ const f = e.target.files[0]; if (f) document.getElementById("idn").textContent = "✓ " + f.name; }});
document.getElementById("sf").onsubmit = e => {{  // read the account back before it leaves the page
  const f = new FormData(e.target), c = f.get("country"), last = s => (s || "").replace(/\\D/g, "").slice(-4);
  const bank = c === "kr" ? f.get("bank_name") : c === "us" ? "Routing " + f.get("routing") : (f.get("swift") || "IBAN");
  const acct = "•••" + (c === "kr" ? last(f.get("account")) : c === "us" ? last(f.get("us_account")) : (f.get("iban") || "").slice(-4));
  // asked inside the page, not with the browser's confirm box (it stays inside the chat's frame and the phone)
  e.preventDefault();
  const d = document.getElementById("ck");
  document.getElementById("ckt").textContent = {json.dumps(tx['check'])}.replace("{{bank}}", bank).replace("{{acct}}", acct).replace("{{holder}}", f.get("holder"));
  document.getElementById("cky").onclick = () => {{ d.close(); e.target.submit(); }};
  document.getElementById("ckn").onclick = () => d.close();
  d.showModal();
}};
</script>"""
    page = f"""<!doctype html><html lang="{locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow">
<title>{e(brand['name'])} — {tx['title']}</title><style>*{{box-sizing:border-box}}
body{{font:16px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;max-width:520px;margin:0 auto;padding:0 1rem 2rem;color:#1b2230;background:#f6f8fc}}
.brand{{display:flex;align-items:center;gap:.6rem;padding:1rem 0;border-bottom:1px solid #e3e6ee}}.brand b{{display:block;color:#1f4fd8;font-size:1.05rem}}
#ck{{border:0;border-radius:18px 18px 0 0;margin:auto auto 0;width:100%;max-width:520px;padding:1.1rem}}#ck::backdrop{{background:rgba(15,20,35,.45)}}
#ck p{{margin:.1rem 0 1rem;font-size:1.05rem}}.ckb{{display:flex;gap:.5rem}}.ckb button{{flex:1;height:48px;border-radius:12px;font:inherit;font-weight:600;border:1px solid #e3e6ee;background:#fff;color:#1f2937}}
.ckb #cky{{background:#1f4fd8;border-color:#1f4fd8;color:#fff}}
.brand span{{color:#5b6474;font-size:.85rem}}.brand a{{padding:.35rem .8rem;border-radius:16px;background:#eef2ff;color:#1f4fd8;font-size:.9rem;text-decoration:none;white-space:nowrap}}
.brand{{flex-wrap:wrap}} .brand>div{{min-width:0;flex:1}} .brand span{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .brand a,.tsize{{flex-shrink:0;white-space:nowrap}}
.tsize{{display:inline;width:auto;height:34px;margin:0 0 0 auto;padding:0 .7rem;border-radius:17px;background:#f1f4fb;color:#1b2230;font:600 .95rem/34px system-ui,sans-serif}}
.big-text{{zoom:1.3}} .big-text .brand span,.big-text .num{{display:none}} .num{{font-style:normal}} .tsize[aria-pressed="true"]{{background:#1f4fd8;color:#fff}}
.lock{{font-size:1.4rem}} h2{{margin:1.1rem 0 .3rem}} h3{{margin:1.4rem 0 .2rem;font-size:1.05rem}} .intro{{color:#4f5868;font-size:.95rem}}
label{{display:block;margin:.8rem 0;font-weight:600;font-size:.95rem}} .lbl{{font-weight:600;font-size:.95rem;margin-top:.8rem}}
input[name],select{{display:block;width:100%;margin-top:.3rem;font:inherit;font-weight:400;padding:.75rem;border:1px solid #c8cdd8;border-radius:10px;background:#fff}}
.idbtn{{display:flex;align-items:center;justify-content:center;height:56px;margin:.4rem 0 .2rem;border:2px dashed #9fb1e8;border-radius:12px;background:#fff;color:#1f4fd8;cursor:pointer}}
.idbtn input{{position:absolute;opacity:0;width:1px;height:1px}} small{{color:#6b7280}}
.yn{{display:flex;gap:1.2rem;margin:.3rem 0 .6rem}} .yn label{{display:flex;align-items:center;gap:.35rem;margin:0;font-weight:400}} .yn input{{width:20px;height:20px}}
.cb{{display:flex;gap:.5rem;align-items:flex-start;font-weight:400}} .cb input{{width:20px;height:20px;margin-top:.15rem}}
button,.btn{{display:block;width:100%;margin-top:1rem;height:52px;border:0;border-radius:12px;background:#1f4fd8;color:#fff;font:600 1.05rem/52px system-ui,sans-serif;text-align:center;text-decoration:none;cursor:pointer}}
.err{{color:#b42318;background:#fde8e7;padding:.6rem .8rem;border-radius:10px}} .saved{{font-weight:600;font-size:1.05em}} .fix .dial{{display:inline-block;margin-top:.5rem;padding:.45rem .9rem;border-radius:10px;background:#1f4fd8;color:#fff;font-weight:600;text-decoration:none}} .fix{{background:#fff8e6;border-radius:10px;padding:.7rem .85rem;line-height:1.55}} .ok{{width:56px;height:56px;margin:1.6rem 0 0;border-radius:50%;background:#e3f5ee;color:#0f8a5f;display:grid;place-items:center;font-size:1.6rem}}
[hidden]{{display:none}}</style></head>
<body>{head}{inner}{size_js}</body></html>"""
    return _resp(200, page, "text/html", PRIVATE_LINK_HEADERS)


def web_chat(svc, event, method, token, sub):
    """The traveler's chat page, reached from the link in our SMS. The link is the credential."""
    claim_id = svc.repo.claim_for_link(token, "chat")
    if not claim_id:
        return _resp(404, "<h3>This link has expired. Reply to our text message for a new one.</h3>", "text/html",
                     PRIVATE_LINK_HEADERS)
    claim = svc.repo.get_claim(claim_id)
    traveler = svc.repo.get_traveler(claim["traveler_id"])
    locale = traveler.get("locale", "en")
    if sub == "" and method == "GET":
        fd = i18n.pretty_date(claim.get("flight_date"), locale)  # the same date style as everything else on the page
        subtitle = (f"{claim.get('flight')} · {fd}, {(claim.get('flight_date') or '')[:4]}" if locale != "ko"
                    else f"{claim.get('flight')}편 · {fd}")
        cfg = json.dumps({"locale": locale, "subtitle": subtitle, "docs": rag.version(), "intro": _intro(svc, claim, traveler), "where": processing_place(locale),
                          "insurer": svc.s.insurer(locale)},
                         ensure_ascii=False)
        page = WEB.joinpath("chat.html").read_text().replace("/*CONFIG*/{}", cfg)
        return _resp(200, page, "text/html", PRIVATE_LINK_HEADERS)
    if sub == "/messages" and method == "GET":
        msgs, evidence = [], {}
        kept = {}  # s3 key -> how the photo was judged; ID photos are not kept and never shown back
        for d in svc.repo.documents(claim_id):
            codes = {f["code"] for f in d.get("flags", [])}
            kept[d.get("s3_key")] = "retake" if "retake" in codes else "check" if codes & checklist.CHECK_FLAGS else "ok"
        for m in svc.repo.messages(claim_id):
            if m.get("to") == "reviewer" or m.get("kind") == "undelivered":
                continue
            out = {"direction": m["direction"], "channel": m.get("channel"), "text": m.get("text") or "",
                   "media": m.get("media") or [], "at": m.get("at"), "id": m.get("id")}
            if m.get("media_keys"):
                out["photos"] = [{"i": i, "state": kept[k]} for i, k in enumerate(m["media_keys"]) if k in kept]
            ui = m.get("ui") or {}
            if ui.get("sources"):  # source card under this reply; [n] in the text points at sources[n-1]
                out["ev"] = ui["sources"]
                for src in ui["sources"]:  # each passage once, however many replies cite it
                    evidence.setdefault(src["chunk"], rag.evidence(src["chunk"]))
            elif ui.get("fold"):
                out["fold"] = True  # the agent folded the open card
            if ui.get("steps"):
                out["steps"] = ui["steps"]  # what the agent checked for this reply
            msgs.append(out)
        state = checklist.compute(svc.repo.documents(claim_id), claim.get("secure_submitted"))
        items = [{"key": k, "label": i18n.label(locale, k), "done": state[k]["done"], "retake": len(state[k]["pending"]), "check": len(state[k]["check"]),
                  # why it needs a check; a document from another claim is only "not usable here" for the traveler
                  "why": sorted({"not_own" if c == "other_claim_document" else c for c in state[k]["why"]}
                                - ({"not_own"} if "name_mismatch" in state[k]["why"] else set())),  # the name says it already
                  "secure": k in ("claim_form", "photo_id")} for k in checklist.REQUIRED]
        if claim.get("siu"):  # held for review: no retake or "not your document" prompts, nothing that hints at it
            items = [{**x, "retake": 0, "check": 0, "why": []} for x in items]
        done = sum(1 for x in items if x["done"])
        est = estimate(svc.repo.documents(claim_id), svc._policy(traveler["id"]).get("plan") or claim.get("plan"))
        live = _live(claim.get("activity"))
        for src in (live or {}).get("sources") or []:  # cited while the reply is being written
            evidence.setdefault(src["chunk"], rag.evidence(src["chunk"]))
        return _resp(200, {"messages": msgs, "evidence": evidence, "done": done, "total": len(items), "items": items,
                           "estimate": {**{k: est.get(k) for k in ("covered", "estimate_usd", "estimate_krw", "remaining_usd", "daily", "trip", "receipts_usd", "krw_per_usd")},
                                        "rows": [{k: r.get(k) for k in ("merchant", "currency", "total", "usd", "counted_usd", "excluded", "per_usd")}
                                                 for r in est.get("rows", [])]},
                           "payout": {k: (claim.get("claim_form") or {}).get(k) for k in ("bank", "account_last4")} if (claim.get("claim_form") or {}).get("account_last4") else None,
                           "status": claim["status"], "due": svc.due(claim), "callback": {"requested": bool((claim.get("callback") or {}).get("requested")),  # the promised time, shown on the traveler's clock
                                                     "due": (claim.get("callback") or {}).get("due"),
                                                     "change_requested": bool((claim.get("callback") or {}).get("change_request"))} if claim.get("callback") else None,
                           "decision": {k: (claim.get("decision") or {}).get(k) for k in ("type", "amount_usd", "estimate_usd", "lines", "appeal_by")}
                                        | {"appeal": claim.get("appeal")} if claim.get("decision") else None,
                           "activity": live},  # steps while the agent is working
                     headers=PRIVATE_LINK_HEADERS)
    pm = re.fullmatch(r"/photo/([\w-]+)/(\d+)", sub)
    if pm and method == "GET":  # a photo the traveler sent, shown back as a thumbnail; only ones we kept
        m = next((x for x in svc.repo.messages(claim_id) if x.get("id") == pm.group(1) and x.get("media_keys")), None)
        key = m["media_keys"][int(pm.group(2))] if m and int(pm.group(2)) < len(m["media_keys"]) else None
        doc = next((d for d in svc.repo.documents(claim_id) if key and d.get("s3_key") == key), None)
        if not doc:
            return _resp(404, {"error": "not found"}, headers=PRIVATE_LINK_HEADERS)
        return {"statusCode": 200, "isBase64Encoded": True, "body": base64.b64encode(svc.docs.get(key)).decode(),
                "headers": {"content-type": doc.get("mime") or "image/jpeg", **PRIVATE_LINK_HEADERS, "cache-control": "private, max-age=86400"}}
    if sub == "/secure" and method == "GET":  # fresh secure-page link on tap; stored messages only show a masked one
        location = f"{svc.secure_link(claim_id)}?back=/c/{token}"  # the secure page offers the way back to this chat
        return {"statusCode": 302, "headers": {"location": location, **PRIVATE_LINK_HEADERS}, "body": ""}
    if sub == "/decision" and method == "GET":  # the decision letter, to print or save as PDF
        if not claim.get("decision"):
            return _resp(404, "<p>No decision yet.</p>", "text/html", PRIVATE_LINK_HEADERS)
        from ..packet import decision_letter
        from ..policy import clauses as policy_clauses
        d = claim["decision"]
        deadline = i18n.letter_date(d["appeal_by"], locale) if d.get("appeal_by") else ""
        ins = svc.s.insurer(locale)
        ap = claim.get("appeal") or {}  # photos sent after the objection count as its evidence, as on the chat card
        evidence = sum(len(m.get("media") or []) for m in svc.repo.messages(claim_id)
                       if ap and m.get("direction") == "in" and (m.get("at") or "") > (ap.get("at") or "~"))
        return _resp(200, decision_letter(claim, traveler, ins["name"], locale, ins["phone"], deadline,
                                          lambda c: policy_clauses(locale).get(c, ""), evidence), "text/html", PRIVATE_LINK_HEADERS)
    if sub == "/appeal" and method == "POST":  # "I disagree with the decision", from the decision card
        try:
            claim = svc.appeal(claim_id, (json.loads(_body(event) or "{}")).get("text"))
        except (ValueError, DecisionConflict) as e:
            return _resp(400, {"error": str(e)}, headers=PRIVATE_LINK_HEADERS)
        return _resp(200, {"appeal": claim["appeal"]}, headers=PRIVATE_LINK_HEADERS)
    if sub == "/appeal_withdraw" and method == "POST":  # "I no longer object"
        try:
            claim = svc.withdraw_appeal(claim_id)
        except ValueError as e:
            return _resp(400, {"error": str(e)}, headers=PRIVATE_LINK_HEADERS)
        return _resp(200, {"appeal": claim.get("appeal")}, headers=PRIVATE_LINK_HEADERS)
    if sub == "/callback" and method == "POST":  # "call me back": abroad, a call to Korea costs; it joins the call-back queue
        b = json.loads(_body(event) or "{}")
        claim = (svc.cancel_callback(claim_id) if b.get("cancel") else svc.change_callback(claim_id, b.get("pref"), b.get("offset_min"))
                 if b.get("change") else svc.request_callback(claim_id, b.get("text"), b.get("pref"), b.get("offset_min")))
        return _resp(200, {"callback": claim.get("callback")}, headers=PRIVATE_LINK_HEADERS)
    if sub == "/lang" and method == "POST":  # the traveler picked a language: the page, texts and the agent follow it
        lc = (json.loads(_body(event) or "{}")).get("locale")
        if lc not in i18n.LOCALES:
            return _resp(400, {"error": "locale"}, headers=PRIVATE_LINK_HEADERS)
        svc.repo.update_traveler(traveler["id"], locale=lc, locale_source="explicit")
        return _resp(200, {"locale": lc}, headers=PRIVATE_LINK_HEADERS)
    if sub == "/submit" and method == "POST":  # the traveler's "submit to the insurer" button
        return _resp(200, svc.submit_claim(claim_id, actor="traveler"), headers=PRIVATE_LINK_HEADERS)
    if sub == "/send" and method == "POST":
        body = json.loads(_body(event) or "{}")
        media = []
        images = (body.get("images") or ([body["image"]] if body.get("image") else []))[:MAX_IMAGES]
        datas = [base64.b64decode(img["data"]) for img in images]
        if sum(map(len, datas)) > 4_500_000:  # Lambda request limit is 6 MB (base64 included)
            return _resp(413, {"error": "images too large"})
        for img, data in zip(images, datas):  # several receipts in one message: one agent turn, one reply
            key = f"claims/{claim_id}/web-{uuid.uuid4().hex[:8]}.jpg"
            svc.docs.put(key, data, img.get("mime", "image/jpeg"))
            media.append({"s3_key": key, "mime": img.get("mime", "image/jpeg"), "filename": img.get("filename", "photo.jpg")[:80]})
        text = (body.get("text") or "")[:2000]
        if not text and not media:
            return _resp(400, {"error": "empty"})
        payload = {"claim_id": claim_id, "traveler_id": traveler["id"], "text": text, "media": media,
                   "provider_id": f"web-{claim_id}-{str(body.get('client_id') or uuid.uuid4().hex)[:64]}"}  # client ids dedupe retries within one chat only
        process_web_async(svc, payload)
        return _resp(202, {"ok": True}, headers=PRIVATE_LINK_HEADERS)
    return _resp(405, {"error": "method"})


def process_web_async(svc, payload):
    """The agent can take ~30 s, longer than API Gateway waits: hand the turn to the inbound Lambda."""
    fn = os.environ.get("INBOUND_FN")
    if fn:
        import boto3
        boto3.client("lambda").invoke(FunctionName=fn, InvocationType="Event",
                                      Payload=json.dumps({"web_inbound": payload}).encode())
    else:  # local server / simulator
        import threading
        threading.Thread(target=handle_web_inbound, args=(svc, payload), daemon=True).start()


def handle_web_inbound(svc, payload):
    """A web chat message, or a reply typed on the simulated phone (channel sms/rcs, sender = the phone number)."""
    from ..inbound import Inbound
    channel = payload.get("channel", "web")
    return svc.handle_inbound(Inbound(channel=channel, sender=payload["traveler_id"] if channel == "web" else payload["sender"],
                                      text=payload["text"], media=payload.get("media") or [], provider_id=payload["provider_id"],
                                      claim_id=payload.get("claim_id") or ""))


SIM_CHANNELS = ("sms", "rcs")


def sim_phone_mail(svc, traveler):
    """Email to this traveler, newest first. Unlike SMS/RCS this is not simulated on a demo deployment: each message
    went out through Amazon SES, and its SES message id is shown so the delivery can be checked."""
    claims = [c for c in svc.repo.list_claims() if c.get("traveler_id") == traveler["id"]]
    live = svc.s.channels == "live"
    out = [{"claim_id": c["id"], "subject": m.get("subject") or "", "text": m.get("text") or "", "at": m.get("at"),
            "message_id": m.get("provider_id") if live else None, "via": "Amazon SES" if live else "local (not sent)"}
           for c in claims for m in svc.repo.messages(c["id"])
           if m.get("channel") == "email" and m.get("direction") == "out" and m.get("to") != "reviewer"
           and m.get("kind") != "undelivered"]
    out.sort(key=lambda x: x["at"] or "", reverse=True)
    addr = traveler.get("email") or ""
    masked = (addr[:1] + "•••" + addr[addr.index("@"):]) if "@" in addr else ""  # the demo address is a real inbox
    return {"email": masked, "to": traveler.get("name") or masked, "mail": out}  # the phone shows the name, not the address


def sim_phone_on(svc):
    return svc.s.sim_phone or svc.s.channels == "fake"


def sim_phone_thread(svc, traveler):
    """What reached this traveler's phone over SMS/RCS, and what they texted back, across their claims (oldest first)."""
    claims = [c for c in svc.repo.list_claims() if c.get("traveler_id") == traveler["id"]]
    msgs = [(c["id"], m) for c in claims for m in svc.repo.messages(c["id"])
            if m.get("channel") in SIM_CHANNELS and m.get("kind") != "undelivered" and m.get("to") != "reviewer"]
    msgs.sort(key=lambda x: x[1].get("at") or "")
    # stored texts keep links masked (/c/••••ab12); the phone asks /link for a fresh one when a link is tapped
    out = [{"claim_id": cid, "direction": m["direction"], "channel": m["channel"], "text": m.get("text") or "", "at": m.get("at"),
            "simulated": str(m.get("provider_id") or "").startswith("sim-") or svc.s.channels == "fake"} for cid, m in msgs]
    busy = bool(out) and out[-1]["direction"] == "in" and \
        (datetime.now(timezone.utc) - datetime.fromisoformat(out[-1]["at"])).total_seconds() < 120
    return {"traveler": {"id": traveler["id"], "name": traveler.get("name"), "phone": traveler.get("phone"),
                         "locale": traveler.get("locale")}, "messages": out, "busy": busy}


MAX_IMAGES = 4


def _live(activity):
    """Steps of the turn in progress; ignore leftovers from a turn that failed."""
    if not activity:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(activity["at"])
    return activity if age.total_seconds() < 180 else None


def _query(event):
    return event.get("queryStringParameters") or {}


def handler(event, context):
    svc = SERVICE or service()
    method = event["requestContext"]["http"]["method"]
    path = event.get("rawPath", "/")

    m = re.fullmatch(r"/docs/atlas-(en|ko)\.json", path)
    if m:  # clause graph for the source cards (public: built from the public sample documents)
        cache = "public, max-age=31536000, immutable" if _query(event).get("v") == rag.version() else "public, max-age=300"
        return _resp(200, rag.atlas(m.group(1)), headers={"cache-control": cache})
    m = re.fullmatch(r"/docs/([\w-]+\.(jpg|pdf))", path)
    if m:  # policy and FAQ PDFs and their page images (public documents)
        f = rag.DOCS.joinpath(m.group(1))
        if not f.is_file():
            return _resp(404, {"error": "not found"})
        ctype = "image/jpeg" if m.group(2) == "jpg" else "application/pdf"
        return {"statusCode": 200, "isBase64Encoded": True, "body": base64.b64encode(f.read_bytes()).decode(),
                "headers": {"content-type": ctype,  # versioned URLs (?v=): a page is fetched once per device
                            "cache-control": "public, max-age=31536000, immutable" if _query(event).get("v") == rag.version()
                            else "public, max-age=300"}}
    from ..web import pages
    if path in pages.PAGES:
        return _resp(200, pages.render(path), "text/html")
    if path in ("/", "/index.html"):
        return _resp(200, WEB.joinpath("index.html").read_text(), "text/html")
    if path == "/phone":  # the simulated phone's page; its data comes from /api/sim/phone (admins, demo only)
        return _resp(200, WEB.joinpath("phone.html").read_text(), "text/html")
    if path == "/config.js":
        cfg = {"auth": bool(os.environ.get("COGNITO_CLIENT_ID")), "clientId": os.environ.get("COGNITO_CLIENT_ID", ""),
               "domain": os.environ.get("COGNITO_DOMAIN", "")}
        return _resp(200, f"window.TRIPCLAIM = {json.dumps(cfg)};", "application/javascript")

    m = re.fullmatch(r"/c/([\w-]+)(/messages|/send|/secure|/submit|/lang|/appeal|/appeal_withdraw|/callback|/decision|/photo/[\w-]+/\d+)?", path)
    if m:
        return web_chat(svc, event, method, m.group(1), m.group(2) or "")

    m = re.fullmatch(r"/secure/([\w-]+)", path)
    if m:
        token = m.group(1)
        claim_id = svc.repo.claim_for_token(token)
        claim = svc.repo.get_claim(claim_id) if claim_id else None
        traveler = svc.repo.get_traveler(claim["traveler_id"]) if claim else None
        locale = (traveler or {}).get("locale", "en")
        brand, back = svc.s.insurer(locale), _back(event)
        if method == "POST" and claim:
            form = {k: v[0] for k, v in parse_qs(_body(event)).items()}
            if not svc.secure_submit(token, form):
                return secure_page(token, claim, locale=locale, brand=brand, back=back, error=SECURE_TEXT.get(locale, SECURE_TEXT["en"])["bad"])
            return secure_page(token, svc.repo.get_claim(claim_id), done=True, locale=locale, brand=brand, back=back)
        return secure_page(token, claim, locale=locale, brand=brand, back=back)

    if path == "/api/claims" and method == "GET":
        rows = []
        contact_centre = not ({"reviewers", "admins"} & set(_roles(event)))
        for c in svc.repo.list_claims():
            t = svc.repo.get_traveler(c["traveler_id"]) or {}
            rows.append({"id": c["id"], "traveler": t.get("name"), "locale": t.get("locale", "en"), "phone": _mask_phone(t.get("phone")),
                         "flight": c.get("flight"), "date": c.get("flight_date"),
                         "status": c["status"], "updated_at": c["updated_at"], "coverage": (c.get("coverage") or {}).get("status"),
                         "flags": sorted({f["code"] for f in c.get("flags", []) if f["code"] != "missing_airline_letter"}),
                         "open": svc.unresolved(c["id"]),  # the same list the approval prompt shows
                         "pending": bool(c.get("pending_decision")), "siu": "hold" if c.get("siu") else "linked" if c.get("siu_linked") else None,  # waiting for a second reviewer
                         "cb_change": bool((c.get("callback") or {}).get("change_request")),  # the traveler cannot make the staff-set time
                         # open objection, and whether its reply-by date has passed
                         "appeal": (("overdue" if (c["appeal"].get("reply_by") or "9") < svc.clock().date().isoformat() else "open")
                                    if c.get("appeal") and not c["appeal"].get("resolved") else None),
                         "assignee": c.get("assignee"), "handoff": c.get("handoff"), "due": svc.due(c),
                         "callback": c.get("callback"),
                         "decision": (c.get("decision") or {}).get("type"),  # "partial" reads as part payment, not "approved"  # a promised call back: time and who promised it
                         "received": len(svc.repo.documents(c["id"])),  # photos in, whether or not they count
                         "checklist": c.get("checklist", {})})
            if contact_centre and c.get("siu"):  # a held claim's findings stay with the reviewers
                rows[-1].update(flags=[], open=[])
        return _resp(200, rows)
    m = re.fullmatch(r"/api/claims/([\w-]+)", path)
    if m and method == "GET":
        if _query(event).get("open"):  # a person opened this claim (not the 5-second refresh): who looked at whose data
            svc.repo.audit(_reviewer(event), "view_claim", m.group(1))
        view = svc.claim_view(m.group(1))
        if not ({"reviewers", "admins"} & set(_roles(event))):
            _contact_centre_view(view)
        return _resp(200, view)
    m = re.fullmatch(r"/api/claims/([\w-]+)/docs/([\w-]+)", path)
    if m and method == "GET":  # the original photo, for reviewers; every look is in the audit trail
        if not _allowed(event, "view_docs"):
            return _resp(403, {"error": "reviewers only"})
        data, mime = svc.document_image(m.group(1), m.group(2))
        if data is None:
            return _resp(404, {"error": "no image"})
        svc.repo.audit(_reviewer(event), "view_document", m.group(1), after={"doc": m.group(2)})
        return {"statusCode": 200, "isBase64Encoded": True, "body": base64.b64encode(data).decode(),
                "headers": {"content-type": mime, "cache-control": "private, no-store"}}
    if path == "/api/kpi" and method == "GET":
        if not _allowed(event, "audit"):
            return _resp(403, {"error": "admins only"})
        q = _query(event)
        if q.get("export"):  # who took the numbers out of the system, and which window
            svc.repo.audit(_reviewer(event), "export_kpi", "kpi", after={"days": q.get("days") or 7})
        return _resp(200, svc.kpi(days=int(q.get("days") or 7) if str(q.get("days") or "7").isdigit() else 7))
    if path == "/api/audit" and method == "GET":
        if not _allowed(event, "audit"):
            return _resp(403, {"error": "admins only"})
        q = _query(event)
        day = q.get("date") or datetime.now(timezone.utc).date().isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return _resp(400, {"error": "date"})
        if q.get("tz") == "kst":  # a Korean business day runs 15:00 UTC the day before to 15:00 UTC
            from datetime import date, timedelta
            prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
            shift = lambda at: (datetime.fromisoformat(at) + timedelta(hours=9)).date().isoformat()
            entries = [e for e in svc.repo.audits(prev) + svc.repo.audits(day) if shift(e["at"]) == day]
        else:
            entries = svc.repo.audits(day)
        # looking at the audit trail is itself audited (and exporting it, separately)
        svc.repo.audit(_reviewer(event), "export_audit" if q.get("export") else "view_audit", day, after={"tz": q.get("tz") or "utc"})
        return _resp(200, {"date": day, "entries": entries})
    m = re.fullmatch(r"/api/claims/([\w-]+)/packet", path)
    if m and method == "GET":
        if not _allowed(event, "view_docs"):  # the reviewer packet carries document findings: reviewers and admins only
            return _resp(403, {"error": "reviewers only"})
        svc.repo.audit(_reviewer(event), "view_packet", m.group(1))
        return _resp(200, svc.repo.get_claim(m.group(1)).get("packet_html") or "<p>Not submitted yet.</p>", "text/html")
    if path == "/api/staff" and method == "GET":  # colleagues to hand a claim to
        if not _roles(event):
            return _resp(403, {"error": "no role"})
        return _resp(200, {"teams": sorted(svc.TEAMS), "staff": sorted(set(svc.staff_directory()) | (set(DEMO_USERS) if not ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") else set()))})
    if path == "/api/me" and method == "GET":
        local = not ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt")
        return _resp(200, {"user": _reviewer(event), "roles": sorted(_roles(event)), "demo_users": sorted(DEMO_USERS) if local else None,
                           "can": sorted(a for a in ROLE_NEEDED if _allowed(event, a)), "sim_phone": sim_phone_on(svc)})
    m = re.fullmatch(r"/api/claims/([\w-]+)/estimate", path)
    if m and method == "POST":  # the worksheet with the reviewer's per-receipt amounts, before deciding
        if not _allowed(event, "approve"):
            return _resp(403, {"error": "reviewers only"})
        c = svc.repo.get_claim(m.group(1))
        plan = svc._policy(c["traveler_id"]).get("plan") or c.get("plan")
        return _resp(200, estimate(svc.repo.documents(m.group(1)), plan, json.loads(_body(event) or "{}").get("adjust")))
    m = re.fullmatch(r"/api/claims/([\w-]+)/actions", path)
    if m and method == "POST":
        body = json.loads(_body(event) or "{}")
        if body.get("action") not in CLAIM_ACTIONS or not _allowed(event, body["action"]):
            return _resp(403, {"error": "your role cannot do this"})
        try:
            if body["action"] == "appeal_uphold":  # objection reviewed, decision kept
                return _resp(200, svc.resolve_appeal(m.group(1), _reviewer(event), body.get("note", "")))
            if body["action"] == "appeal_by_phone":  # the traveler objected on the phone; the agent records it for them
                svc.identity("resend_link", body.get("checks"), body.get("reason", ""))
                return _resp(200, svc.appeal(m.group(1), body.get("note"), source="phone", staff=_reviewer(event)))
            cl, me_, roles = svc.repo.get_claim(m.group(1)), _reviewer(event), _roles(event)
            # held for the special investigation unit: no decision and nothing to the traveler until the hold is cleared
            siu = cl.get("siu") or {}
            if siu and body["action"] in ("approve", "deny", "correct", "message", "request_info", "resend_link", "bank_correction",
                                          "contact_update"):  # a new phone or email on a suspected claim waits too
                # fraud confirmed by SIU: a denial may go ahead (through the second reviewer); nothing else
                if not (siu.get("outcome") in ("fraud_confirmed", "referred_to_police") and body["action"] == "deny"):
                    return _resp(409, {"error": "siu_hold", "by": siu.get("by")})
            if (cl.get("siu_linked") or {}).get("hold_payment") and (
                    body["action"] in ("approve", "correct", "confirm") or (body["action"] == "message" and body.get("template"))):
                return _resp(409, {"error": "payment_held"})  # no payment decided, corrected or announced while held
            if body["action"] in ("siu", "siu_clear"):
                return _resp(200, svc.siu_refer(m.group(1), me_, body.get("reason", ""), clear=body["action"] == "siu_clear",
                                                outcome=body.get("outcome"), admin="admins" in roles, kind=body.get("kind")))
            if body["action"] == "siu_hold_payment":
                return _resp(200, svc.siu_hold_payment(m.group(1), me_, body.get("reason", "")))
            if body["action"] == "siu_release_payment":
                return _resp(200, svc.siu_release_payment(m.group(1), me_, body.get("reason", "")))
            if body["action"] == "assign":  # a team's queue is taken only by someone in that team
                owner = cl.get("assignee") or ""
                if owner.startswith("team:") and owner[5:] not in roles:
                    return _resp(403, {"error": "not_in_team", "team": owner})
                # someone else's claim changes hands only with a reason (in the audit trail)
                if owner and not owner.startswith("team:") and owner != me_ and (body.get("to") or me_) != owner and not (body.get("reason") or "").strip():
                    return _resp(409, {"error": "takeover_needs_reason", "owner": owner})
            if body["action"] == "handoff":  # handing on someone else's claim is a takeover too: same rule (admins included), never to yourself
                owner = cl.get("assignee") or ""
                if (body.get("to") or "") == me_:
                    return _resp(400, {"error": "handoff_to_self"})
                if owner and not owner.startswith("team:") and owner != me_ and not (body.get("reason") or "").strip():
                    return _resp(409, {"error": "takeover_needs_reason", "owner": owner})
            if body["action"] == "handoff_done":  # only the person it was handed to (or who took it) closes it, or an admin
                h = cl.get("handoff") or {}
                if me_ not in (h.get("to"), h.get("taken_by"), cl.get("assignee")) and "admins" not in roles:
                    return _resp(403, {"error": "not_your_handoff"})
            if body["action"] in svc.STAFF_ONLY:
                return _resp(200, svc.staff_action(m.group(1), _reviewer(event), body["action"], body.get("to"), body.get("note", ""),
                                                   body.get("due"), body.get("reason", ""), body.get("phone"), body.get("email"),
                                                   body.get("checks"), body.get("callback_at"), body.get("callback_done"), body.get("callback_pref")))
            if body["action"] in ("confirm", "return"):  # second reviewer on a pending decision
                return _resp(200, svc.confirm_decision(m.group(1), _reviewer(event), body["action"] == "confirm", body.get("note", ""),
                                                       body.get("pid"), body.get("override", "")))
            if body["action"] in ("approve", "deny", "correct"):  # every decision goes through decide(): amount, clauses, four eyes
                act = ("deny" if body.get("deny") else "approve") if body["action"] == "correct" else body["action"]
                return _resp(200, svc.decide(m.group(1), _reviewer(event), act, body.get("amount_usd"),
                                             body.get("clauses") or [], body.get("note", ""), body.get("reason", ""),
                                             adjust=body.get("adjust") or None, correction=body["action"] == "correct"))
            return _resp(200, svc.reviewer_action(m.group(1), _reviewer(event), body["action"], body.get("note", ""), body.get("reason", ""),
                                                  checks=body.get("checks"), template=body.get("template")))
        except WordingBlocked as e:
            return _resp(422, {"error": "banned_wording", "phrase": str(e)})
        except DecisionConflict as e:
            return _resp(409, {"error": str(e)})
        except SecondPersonRequired as e:
            return _resp(409, {"error": "second_person_required", "detail": str(e)})
        except PermissionError as e:
            return _resp(409, {"error": "reason_required", "detail": str(e), "unresolved": svc.unresolved(m.group(1))})
        except ValueError as e:
            return _resp(400, {"error": str(e)})  # e.g. clauses_and_note_required, not_submitted
    staff_only = {"/api/events": "disrupt", "/api/demo/personas": "demo", "/api/reminders": "remind"}
    if path in staff_only and method == "POST" and not _allowed(event, staff_only[path]):
        return _resp(403, {"error": "admins only"})
    if path == "/api/events/preview" and method == "POST":  # count, channels, SMS parts and cost before anything is sent
        if not _allowed(event, "disrupt"):
            return _resp(403, {"error": "admins only"})
        return _resp(200, svc.preview_disruption(json.loads(_body(event))))
    if path == "/api/reminders/preview" and method == "POST":
        if not _allowed(event, "remind"):
            return _resp(403, {"error": "admins only"})
        return _resp(200, svc.preview_reminders())
    if path == "/api/events" and method == "POST":  # bulk sends are audited with who pressed the button and how many went out
        ev = json.loads(_body(event))
        count = svc.preview_disruption(ev)["count"]
        if count > svc.s.bulk_second_admin_over:  # too many at once for one person: a second admin sends it
            req = svc.repo.put_bulk("alert", ev, _reviewer(event), count)
            svc.repo.audit(_reviewer(event), "bulk_requested", f"{ev.get('flight')}#{ev.get('date')}", after={"count": count, "id": req["id"]})
            return _resp(202, {"pending": req})
        alerted = svc.on_disruption(ev)
        svc.repo.audit(_reviewer(event), "bulk_alert", f"{ev.get('flight')}#{ev.get('date')}", after={"sent": len(alerted)})
        return _resp(200, {"alerted": alerted})
    if path == "/api/bulk" and method == "GET":
        if not _allowed(event, "disrupt"):
            return _resp(403, {"error": "admins only"})
        return _resp(200, {"pending": svc.repo.bulk_requests()})
    m = re.fullmatch(r"/api/bulk/(\w+)/(confirm|cancel)", path)
    if m and method == "POST":
        if not _allowed(event, "disrupt"):
            return _resp(403, {"error": "admins only"})
        req = next((r for r in svc.repo.bulk_requests() if r["id"] == m.group(1)), None)
        if not req:
            return _resp(404, {"error": "not pending"})
        me_ = _reviewer(event)
        if m.group(2) == "confirm" and req["by"] == me_:
            return _resp(409, {"error": "second_person_required"})
        svc.repo.close_bulk(req["id"], "sent" if m.group(2) == "confirm" else "cancelled", me_)
        if m.group(2) == "cancel":
            svc.repo.audit(me_, "bulk_cancelled", req["id"])
            return _resp(200, {"cancelled": req["id"]})
        alerted = svc.on_disruption(req["body"])
        svc.repo.audit(me_, "bulk_alert", f"{req['body'].get('flight')}#{req['body'].get('date')}",
                       after={"sent": len(alerted), "requested_by": req["by"], "id": req["id"]})
        return _resp(200, {"alerted": alerted})
    m = re.fullmatch(r"/api/sim/phone(?:/([\w-]+))?(/link|/mail)?", path)
    if m:  # the simulated phone (demo only): what reached a traveler by SMS/RCS, and their replies back
        if not sim_phone_on(svc):
            return _resp(404, {"error": "not found"})
        if not _allowed(event, "demo"):
            return _resp(403, {"error": "admins only"})
        if not m.group(1):  # who has a phone number
            seen, out = set(), []
            for c in svc.repo.list_claims():
                t = c.get("traveler_id")
                if t in seen:
                    continue
                seen.add(t)
                tr = svc.repo.get_traveler(t)
                if tr and str(tr.get("phone") or "").startswith(SIM_NUMBER_PREFIX):  # fictional numbers only, never a real one
                    out.append({"id": tr["id"], "name": tr.get("name"), "phone": tr["phone"], "locale": tr.get("locale")})
            return _resp(200, {"phones": out})
        traveler = svc.repo.get_traveler(m.group(1))
        if not traveler or not str(traveler.get("phone") or "").startswith(SIM_NUMBER_PREFIX):
            return _resp(404, {"error": "no simulated phone"})
        if m.group(2) == "/mail" and method == "GET":  # the traveler's mailbox: what went to them by email (SES)
            return _resp(200, sim_phone_mail(svc, traveler))
        if m.group(2) == "/link" and method == "POST":  # a link tapped on the phone: a fresh one, as the real text carried
            body = json.loads(_body(event) or "{}")
            claim = svc.repo.get_claim(body.get("claim_id") or "")
            if not claim or claim.get("traveler_id") != traveler["id"]:
                return _resp(404, {"error": "no claim"})
            svc.repo.audit(_reviewer(event), "sim_phone_link", claim["id"], after={"kind": body.get("kind")})
            kind = body.get("kind")
            url = svc.secure_link(claim["id"]) if kind == "secure" else svc.chat_link(claim["id"]) + ("/decision" if kind == "decision" else "")
            return _resp(200, {"url": url})
        if method == "GET":
            return _resp(200, sim_phone_thread(svc, traveler))
        if method == "POST":
            text = (json.loads(_body(event) or "{}").get("text") or "").strip()[:1000]
            if not text:
                return _resp(400, {"error": "empty"})
            last_out = [x for x in sim_phone_thread(svc, traveler)["messages"] if x["direction"] == "out"]
            channel = last_out[-1]["channel"] if last_out else "sms"  # reply on the thread it came in on
            svc.repo.audit(_reviewer(event), "sim_phone_reply", traveler["id"], after={"channel": channel})
            process_web_async(svc, {"channel": channel, "sender": traveler["phone"], "text": text,
                                    "provider_id": f"sim-in-{uuid.uuid4().hex[:12]}"})
            return _resp(202, {"ok": True, "channel": channel})
    if path == "/api/demo/reset" and method == "POST":  # demo deployments only: start a recording from an empty desk
        if not sim_phone_on(svc):
            return _resp(404, {"error": "not found"})
        if not _allowed(event, "demo"):
            return _resp(403, {"error": "admins only"})
        return _resp(200, {"removed": svc.reset_demo_data(_reviewer(event))})
    if path == "/api/demo/personas" and method == "POST":
        svc.repo.audit(_reviewer(event), "demo_personas", "demo")
        body = json.loads(_body(event) or "{}") or {}
        day = body.get("date")  # the flight date in the disruption form, so both match
        depart = body.get("depart")  # the replacement flight's time (a recording made late in the day)
        try:
            day = datetime.strptime(day, "%Y-%m-%d").date() if day else None
            depart = datetime.strptime(depart, "%H:%M").strftime("%H:%M") if depart else None
        except ValueError:
            return _resp(400, {"error": "date"})
        return _resp(200, {"personas": svc.create_demo_personas(today=day, depart=depart)})
    if path == "/api/reminders" and method == "POST":
        reminded = svc.run_reminders(force=True)
        svc.repo.audit(_reviewer(event), "bulk_reminder", "reminders", after={"sent": len(reminded)})
        return _resp(200, {"reminded": reminded})
    return _resp(404, {"error": "not found"})
