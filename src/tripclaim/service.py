"""Claim orchestration: disruption alerts, inbound messages and documents, checks, replies, submission.

Every channel handler (Lambda, AgentCore, simulator) goes through this class.
"""
import hashlib
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

from . import checklist, rag, understanding
from . import i18n
from .policy import clauses as policy_clauses
from .i18n import render_template, t
from .estimate import KRW_PER_USD, estimate
from .packet import FLAG_TEXT, build as build_packet
from .router import Router
from .store import now_iso
from .validate import COVERED_CAUSES, PLAN_LIMITS_USD, _names, validate

BLOCKING = {"flight_mismatch", "name_mismatch", "name_unverified", "cause_not_covered", "delay_below_threshold", "other_claim_document",
            "other_insurance_declared", "airline_paid_declared"}  # declared other compensation: settle the split before paying
CLOSED = {"submitted", "approved", "denied", "info_requested", "closed"}
SIM_NUMBER_PREFIX = "+120255501"  # the demo personas' fictional numbers (+1 202 555-01xx); the phone simulator shows only these
SIM_PREFERENCE = {"premier": "rcs", "extended": "rcs", "jordan": "rcs"}  # demo personas on the simulated phone; others SMS
REGIONS_BY_AIRPORT = {"HND": ["Tokyo", "Kanagawa", "Chiba"], "NRT": ["Chiba", "Tokyo"], "KIX": ["Osaka", "Wakayama", "Hyogo"]}


# What the traveler may hear about a flag. Cross-claim detail (another claim, another traveler) is for staff only.
TRAVELER_FLAG_TEXT = {"other_claim_document": "Can't be used for this claim; please send a document in your own name"}


def _traveler_problem(f):
    if f["code"] in TRAVELER_FLAG_TEXT:
        return TRAVELER_FLAG_TEXT[f["code"]]
    return f"{FLAG_TEXT.get(f['code'], f['code'])} ({f['detail']})"


GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")


def sms_parts(text):
    """SMS segments a carrier bills: 160/153 GSM-7 characters, or 70/67 once any character (Hangul) needs UCS-2."""
    single, multi = (160, 153) if set(text) <= GSM7 else (70, 67)
    return 1 if len(text) <= single else -(-len(text) // multi)


def banned_wording():
    """The compliance-owned list in prompts/banned_wording.txt, as one regex."""
    from importlib import resources
    lines = resources.files("tripclaim.prompts").joinpath("banned_wording.txt").read_text().splitlines()
    return re.compile("|".join(f"(?:{x.strip()})" for x in lines if x.strip() and not x.startswith("#")), re.IGNORECASE)


class WordingRule:
    """The compliance regex, applied the way people actually write:
    - Hangul words run together or spaced apart ("보상받으실수있어요", "입금 해드릴게요") are both checked,
    - a customer's question quoted back ("'보상돼요?'라고 물으셨죠") is not a promise,
    - "we cannot guarantee …" / "not guaranteed …" negates the rest of its clause only: after a comma, "but",
      "however", "and", a dash, "other than" … the text is checked again ("We cannot guarantee it, but you will be
      paid" and "I can't guarantee timing and you will be paid" are promises)."""
    # one short question quoted back ("'보상돼요?'라고 물으셨죠"): no sentence end inside, at most 40 characters,
    # and not when the next words confirm it ("…라고 물으셨는데, 네 맞습니다" states the answer)
    QUOTED_Q = re.compile(r"['\"‘“「][^'\"’”」.!?。！？]{0,40}\?['\"’”」](?![^.!?\n]{0,24}(?:네|맞(?:아요|습니다|죠)|그렇(?:습니다|죠|아요)|\byes\b|\bcorrect\b|\bright\b|\bthat's it\b))", re.IGNORECASE)
    NEGATED = re.compile(r"""\b(?:cannot|can't|can’t|do not|don't|don’t|no|not)\s+(?:be\s+)?guarantee[ds]?\b(?:(?![\s(\["'“](?:but|however|yet|though|although|still|so|and|anyway|other than|except|unless)\b)[^.!?\n,;:—–(\[-])*""", re.IGNORECASE)

    def __init__(self, rx):
        self.rx = rx
        # for text written without spaces: every space in the rules becomes optional
        # for Korean written without spaces: the Korean rules again, every space in them optional
        ko = [p for p in re.split(r"\|(?=\(\?:)", rx.pattern) if re.search(r"[가-힣]", p)]
        self.joined = re.compile("|".join(re.sub(r"\(\?<[!=][^)]*\)", "", p).replace(" ", " ?") for p in ko), rx.flags)

    def _clean(self, text):
        return self.NEGATED.sub(" ", self.QUOTED_Q.sub(" ", text or ""))

    def search(self, text):
        t = self._clean(text)
        return self.rx.search(t) or self.joined.search(re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", t))

    def phrases(self, text):
        """Every matching phrase, not just the first (a fact may come first and a promise after it)."""
        t = self._clean(text)
        found = [m.group(0) for m in self.rx.finditer(t)]
        return found or [m.group(0) for m in self.joined.finditer(re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", t))]


class WordingBlocked(Exception):
    """A message to the traveler promises an outcome ("보상돼요", "is covered")."""


class DecisionConflict(Exception):
    """The decision state moved (already decided, already pending, changed while being confirmed)."""


class SecondPersonRequired(Exception):
    """Four-eyes rule: the confirming reviewer must be someone else."""


def business_days_after(iso, n):
    """The date n business days (Mon–Fri) after `iso`; public holidays are left to the insurer's own calendar."""
    from datetime import date, timedelta
    d = date.fromisoformat(iso[:10])
    while n:
        d += timedelta(days=1)
        n -= d.weekday() < 5
    return d.isoformat()


def first_name(traveler):
    """How we greet: given name in English ("Jordan"), full name in Korean ("김지수" -> "김지수님")."""
    return traveler["name"] if loc(traveler) == "ko" else traveler["name"].split()[0]


def loc(traveler):
    return i18n.norm((traveler or {}).get("locale"))


MAX_SOURCES = 3


class Service:
    def _turn_for(self, claim_id):
        """{"claim_id", "channel", "subject", "acked", "ui"} while a message for this claim is being handled.
        Keyed by claim, not thread: the agent runs tools on worker threads, and turns for other claims run concurrently."""
        with self._turns_lock:
            return self._turns.get(claim_id)

    def __init__(self, settings, repo, channels, llm, agent, docs, clock=None, guardrail=None):
        self.s = settings
        self.guardrail = guardrail  # Bedrock guardrail: the second check on words to a traveler (None = regex only)
        self.repo = repo
        self.channels = channels
        self.llm = llm
        self.agent = agent
        self.docs = docs
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.router = Router(repo, channels, self.clock)
        sent = self.router.send

        def send(traveler, claim_id, purpose, text, *a, **kw):  # the last line of defence: anything sent is scanned once more
            self._after_send(claim_id, text)
            return sent(traveler, claim_id, purpose, text, *a, **kw)
        self.router.send = send
        self._turns, self._turns_lock = {}, threading.Lock()
        self._embed = None

    # ---------- setup ----------

    def register_traveler(self, traveler, policy, segments):
        self.repo.put_traveler(traveler)
        self.repo.put_policy(traveler["id"], policy)
        for seg in segments:
            self.repo.put_segment(traveler["id"], seg)
        return traveler["id"]

    def _policy(self, traveler_id):
        policies = self.repo.policies(traveler_id)
        return policies[0] if policies else {}

    def _open_claim_for(self, traveler, event):
        """The traveler's open claim for this flight, if there is one (an alert re-sent for the same disruption, or
        demo travelers created before the alert): the alert links to it instead of opening a second claim."""
        claim = self.repo.get_claim(traveler["active_claim_id"]) if traveler.get("active_claim_id") else None
        if claim and claim["status"] not in CLOSED and (claim.get("flight"), claim.get("flight_date")) == (event["flight"], event["date"]):
            return claim
        return None

    def _new_claim(self, traveler, event):
        pol = self._policy(traveler["id"])
        seg = next((s for s in self.repo.segments(traveler["id"])
                    if event and s["flight"] == event["flight"] and s["date"] == event["date"]), None)
        seg = seg or (self.repo.segments(traveler["id"]) or [{}])[0]
        return self.repo.create_claim(
            traveler["id"], event["id"] if event else None, plan=pol.get("plan"), policy_id=pol.get("id"),
            flight=seg.get("flight"), flight_date=seg.get("date"), route=seg.get("route"),
            scheduled_departure=seg.get("scheduled_departure"),
            replacement_departure=(event or {}).get("replacement_departure"),
            allowed_regions=(event or {}).get("allowed_regions") or REGIONS_BY_AIRPORT.get(seg.get("origin", ""), []),
            status="alerted" if event else "open", secure_submitted=False)

    # ---------- disruption events ----------

    def precheck(self, policy, event):
        """Rule-only view before any conversation: is it worth alerting this traveler?"""
        if policy.get("plan") not in PLAN_LIMITS_USD:
            return "likely_not_covered", "PLAN-1"
        if event.get("reason_category") not in COVERED_CAUSES:
            return "likely_not_covered", "TD-2"
        if event.get("type") == "delayed" and (event.get("delay_hours") or 0) < 4:
            return "likely_not_covered", "DEF-5"
        if event.get("announced_at") and policy.get("purchased_at") and policy["purchased_at"] >= event["announced_at"]:
            return "review", "EX-1"
        return "likely_covered", "TD-1"

    # ---------- bulk sends: preview before anything goes out ----------

    def _alert_text(self, traveler, event, link):
        lc = loc(traveler)
        params = [first_name(traveler), event["flight"], i18n.pretty_date(event["date"], lc),
                  t(lc, f"event_{event['type']}") if event["type"] in ("cancelled", "delayed") else event["type"]]
        brand = self.s.insurer(lc)
        return t(lc, "sms_alert", date=params[2], flight=event["flight"], event=params[3], link=link,
                 insurer=brand["name"], phone=brand["phone"]), params

    def _reminder_text(self, traveler, claim, link):
        lc = loc(traveler)
        return t(lc, "sms_reminder", flight=claim.get("flight"), link=link,
                 insurer=self.s.insurer(lc)["name"], phone=self.s.insurer(lc)["phone"])

    def _sample_link(self):
        return f"{self.s.public_base_url}/c/{'x' * 22}"  # same length as a real chat link

    def _bulk_summary(self, rows):
        """rows: [(traveler, text)] -> who gets what on which channel, SMS parts and a cost estimate."""
        by_channel, parts, cost, samples = {}, 0, 0.0, {}
        for traveler, text in rows:
            cands = self.router.candidates(traveler, "alert")
            channel = cands[0][0] if cands else "none"
            by_channel[channel] = by_channel.get(channel, 0) + 1
            if channel in ("sms", "rcs"):
                n = sms_parts(text)
                parts += n
                cost += n * self.s.sms_price_usd
            elif channel == "whatsapp":
                cost += self.s.wa_price_usd
            samples.setdefault(loc(traveler), text)
        return {"count": len(rows), "by_channel": by_channel, "sms_parts": parts, "cost_usd": round(cost, 2),
                "samples": samples, "needs_second_admin": len(rows) > self.s.bulk_second_admin_over}

    def preview_disruption(self, event):
        """What on_disruption would send, without sending or storing anything."""
        rows, skipped = [], []
        for tid in self.repo.travelers_on_flight(event["flight"], event["date"]):
            traveler = self.repo.get_traveler(tid)
            view, clause = self.precheck(self._policy(tid), event)
            if view == "likely_not_covered":
                skipped.append(clause)
                continue
            rows.append((traveler, self._alert_text(traveler, event, self._sample_link())[0]))
        return {**self._bulk_summary(rows), "skipped_not_covered": len(skipped)}

    def preview_reminders(self):
        rows = []
        for claim in self._reminder_due(force=True):
            traveler = self.repo.get_traveler(claim["traveler_id"])
            rows.append((traveler, self._reminder_text(traveler, claim, self._sample_link())))
        return self._bulk_summary(rows)

    def on_disruption(self, event):
        event = self.repo.put_event(event)
        self.repo.link_event_to_flight(event)
        alerted = []
        for tid in self.repo.travelers_on_flight(event["flight"], event["date"]):
            traveler = self.repo.get_traveler(tid)
            pol = self._policy(tid)
            view, clause = self.precheck(pol, event)
            if view == "likely_not_covered":
                self.repo.audit("system", "alert_skipped", tid, after={"event": event["id"], "clause": clause})
                continue
            claim = self._open_claim_for(traveler, event) or self._new_claim(traveler, event)
            self.repo.update_claim(claim["id"], precheck={"status": view, "clause": clause})
            # WhatsApp gets the approved template; SMS/RCS get a one-line text with the chat link.
            text, params = self._alert_text(traveler, event, self.chat_link(claim["id"]))
            self.router.send(traveler, claim["id"], "alert", text, template=("flight_disruption_alert", params))
            alerted.append(claim["id"])
        return alerted

    # ---------- inbound ----------

    def _claim_for(self, traveler):
        claim = self.repo.get_claim(traveler["active_claim_id"]) if traveler.get("active_claim_id") else None
        if claim and claim["status"] not in ("closed",):
            return claim
        events = [e for seg in self.repo.segments(traveler["id"])
                  for e in self.repo.events_for_flight(seg["flight"], seg["date"])]
        return self._new_claim(traveler, events[-1] if events else None)

    def handle_inbound(self, inb):
        traveler = self.repo.get_traveler(inb.sender) if inb.channel == "web" else self.repo.find_traveler(inb.channel, inb.sender)
        if not traveler:
            self.repo.audit("system", "unknown_sender", f"{inb.channel}:{inb.sender}")
            return None
        claim = self.repo.get_claim(inb.claim_id) if inb.claim_id else self._claim_for(traveler)
        if inb.provider_id and not self.repo.claim_provider_id(f"in-{inb.provider_id}", claim["id"]):
            return None  # redelivered event
        last = dict(traveler.get("last_inbound") or {})
        last[inb.channel] = self.clock().isoformat(timespec="seconds")
        updates = {"last_inbound": last, "unreachable": {
            k: v for k, v in (traveler.get("unreachable") or {}).items() if k != inb.channel}}
        switch = i18n.explicit_switch(inb.text)
        if switch:  # "English" / "한국어": explicit choice wins and sticks
            updates.update(locale=switch, locale_source="explicit")
        elif traveler.get("locale_source") != "explicit" and inb.channel != "email":
            detected = i18n.detect(inb.text)  # not from email: forwarded airline mail says nothing about the traveler
            if detected and detected != traveler.get("locale"):
                updates.update(locale=detected, locale_source="detected")
        traveler = self.repo.update_traveler(traveler["id"], **updates)
        self.repo.add_message(claim["id"], {"direction": "in", "channel": inb.channel, "kind": "text",
                                            "text": inb.text or ("[attachment]" if inb.media else ""),
                                            "provider_id": inb.provider_id, "subject": inb.subject,
                                            "media": [m.get("filename") for m in inb.media],
                                            # web uploads: where each photo is, so the chat can show what was sent
                                            "media_keys": [m.get("s3_key") for m in inb.media] if inb.channel == "web" else None})
        gated = self._submit_gate(claim, traveler, inb)
        if gated:
            return gated
        if self.s.llm_daily_cap and not self.repo.take_quota(f"llm#{self.clock().date().isoformat()}", self.s.llm_daily_cap):
            reply = t(loc(traveler), "demo_limit")  # no model call: the message is saved, the daily demo budget is spent
            msg = self.router.send(self.repo.get_traveler(traveler["id"]), claim["id"], "reply", reply, reply_on=inb.channel,
                                   email_subject=("Re: " + inb.subject) if inb.subject else t(loc(traveler), "subject_claim"))
            return {"claim_id": claim["id"], "reply": reply, "channel": msg["channel"] if msg else None}
        turn = {"claim_id": claim["id"], "channel": inb.channel, "subject": inb.subject, "acked": False, "ui": None, "steps": []}
        with self._turns_lock:
            self._turns[claim["id"]] = turn
        try:
            if inb.media:
                self.step(claim["id"], "read", n=len(inb.media))
            new_docs = self._ingest(claim, inb)
            claim, docs, state = self.recheck(claim["id"])
            context = {**self.turn_context(claim, traveler, state, new_docs), "channel": inb.channel}
            for d in context["new_documents"]:
                self.step(claim["id"], "doc", summary=d["summary"], problem=(d["problems"] or [None])[0])
            text = inb.text or "(sent a document)"
            try:
                reply = self.agent.run(self, claim["id"], text, context)
            except Exception as e:  # the model is unavailable: say so instead of leaving the traveler waiting
                self.repo.audit("system", "agent_error", claim["id"], after=type(e).__name__)
                print(f"AGENT_ERROR {claim['id']} {type(e).__name__}")  # log metric -> alarm when the model keeps failing
                brand = self.s.insurer(loc(traveler))
                reply = t(loc(traveler), "agent_unavailable", phone=brand["phone"])
            ui = dict(turn["ui"] or {})
            if turn["steps"]:
                ui["steps"] = turn["steps"]  # the reply keeps the trail of what was checked
        finally:
            with self._turns_lock:
                self._turns.pop(claim["id"], None)
            if turn["steps"] and sys.exc_info()[0]:  # failed before the reply: no endless "writing the reply"
                self.repo.update_claim(claim["id"], activity=None)
        reply = self.guard_reply(claim["id"], reply, loc(traveler))
        claim = self.repo.get_claim(claim["id"])
        purpose = "reply"
        msg = self.router.send(self.repo.get_traveler(traveler["id"]), claim["id"], purpose, reply, reply_on=inb.channel,
                               email_subject=("Re: " + inb.subject) if inb.subject else t(loc(traveler), "subject_claim"),
                               extra={"ui": ui} if ui else None)
        if turn["steps"]:
            self.repo.update_claim(claim["id"], activity=None)  # after the reply is stored: no gap in the chat
        return {"claim_id": claim["id"], "reply": reply, "channel": msg["channel"] if msg else None}

    YES_WORDS = {"yes", "네", "예", "yes.", "네.", "예."}  # a whole word: one stray "y" files nothing
    NO_WORDS = {"no", "cancel", "아니요", "아니오", "취소", "no.", "아니요."}

    def _submit_gate(self, claim, traveler, inb):
        """By text, one word must not file a claim: "SUBMIT" gets a summary back, and only "YES" within 30 minutes sends it."""
        if inb.media or inb.channel == "web" or claim["status"] in CLOSED:
            return None
        word, lc = (inb.text or "").strip().lower(), loc(traveler)
        pending = claim.get("submit_confirm")
        if word in i18n.SUBMIT_WORDS:
            docs = self.repo.documents(claim["id"])
            state = checklist.compute(docs, claim.get("secure_submitted"))
            if checklist.missing(state):
                return None  # not ready: the agent explains what is missing
            est = estimate(docs, self._policy(traveler["id"]).get("plan") or claim.get("plan"))
            form = claim.get("claim_form") or {}
            self.repo.update_claim(claim["id"], submit_confirm=self.clock().isoformat(timespec="seconds"))
            text = t(lc, "submit_confirm", usd=f"${(est.get('estimate_usd') or 0):,.2f}", n=len(docs),
                     acct=f"{form.get('bank') or ''} •••{form.get('account_last4') or ''}".strip())
        elif word in self.NO_WORDS and pending:
            self.repo.update_claim(claim["id"], submit_confirm=None)
            text = t(lc, "submit_cancelled")
        elif word in self.YES_WORDS and pending and self.clock() - datetime.fromisoformat(pending) < timedelta(minutes=30):
            self.repo.update_claim(claim["id"], submit_confirm=None)
            res = self.submit_claim(claim["id"], actor="traveler")
            text = t(lc, "submitted", claim=res["claim_id"])
        else:
            return None
        msg = self.router.send(self.repo.get_traveler(traveler["id"]), claim["id"], "reply", text, reply_on=inb.channel)
        return {"claim_id": claim["id"], "reply": text, "channel": msg["channel"] if msg else None}

    # Allow-list for decisions (round 17 re-check): the decision reaches the traveler only through templates the
    # server fills from the claim itself (decision notice, letter, "why less" / "pay date" replies). Free text from the
    # agent or staff never states a decision, whatever the claim's state: no fact exception to argue with.
    # First pass, here: words that state a decision; second pass: the guardrail's DecisionStatement topic.
    DECISION = re.compile(
        r"승인(?:했|됐|되었|났|입니다|이에요|이세요| ?완료| ?처리)|결정(?:이|은)? ?(?:났|됐|되었|내렸)|지급하기로|지급 ?대상(?:으로|이)? ?(?:결정|확정)|"
        r"심사(?:를|에서)? ?(?:통과|승인)|부지급|지급하지 않기로|보상하지 않기로|(?:지급|보상|청구)(?:을|를|이|가)? ?거절|"
        r"\bapprov(?:ed|al)\b|\baccepted (?:your|the) claim\b|\bagreed to pay\b|\b(?:we are|we're|they are|they're|the insurer is) paying\b|"
        r"\b(?:payout|payment|amount to pay) (?:is|was|will be)\b|지급액(?:은|이)|\b(?:denied|declined|rejected)\b|\bnot (?:be )?(?:paid|payable|approved)\b|"
        r"(?:결정|심사 결과)\s*:|(?:일부|전액) ?지급(?! 여부)(?:으로|이|을|입니다|이에요|했|됐|결정|$|[ .,)])|\bdecision\s*:|\bpaid (?:in part|in full)\b|\bnot payable\b|"
        r"承認|支払(?:い)?(?:が|を)?(?:決定|承認)|不支給", re.IGNORECASE)
    # "if / whether / once approved": a condition, not a statement (English only; Korean conditions use 되면/하면,
    # which the words above do not match)
    EN_CONDITION = re.compile(r"\b(?:if|whether|once|unless)\b[^.!?,;]{0,40}$", re.IGNORECASE)
    SENTENCES = re.compile(r"(?<=[!?。！？])\s*|(?<=\.)(?!\d)\s*|\n+")  # "$100.00" is not a sentence end
    # the nouns the topic reacts to by themselves ("결정 내용", "결과", "decision"): left out of the re-ask too
    OUTCOME_NOUNS = re.compile(r"결정(?: ?내용| ?통지서)?|결과|심사|\bdecision(?:s)?\b|\boutcome\b|\bresult\b", re.IGNORECASE)
    # sentences that point to where the outcome is written, say when it will be known, or are conditional
    POINTER = re.compile(r"(?:결정 ?카드|통지서|decision (?:card|letter)|\bletter\b)|(?:되|하|나)면\b|(?:되|하|나)면 |\b(?:if|whether|once)\b|"
                         r"여부|(?:되|하|나|받)는지|보험사가 (?:심사해서 )?(?:정해|결정해|판단해)|\b(?:the insurer|a reviewer|reviewers?) decides?\b|\bdepends\b|"
                         r"알려 ?드(?:려요|릴게요|립니다)|\byou(?:'ll| will)? (?:get|receive) a (?:text|message)\b", re.IGNORECASE)

    def _decision_stated(self, sent):
        for m in self.DECISION.finditer(sent or ""):
            if not self.EN_CONDITION.search(sent[:m.start()]):
                return m.group(0)
        return None

    def promise_in(self, claim_id, text, use_guardrail=True, template=False):
        """What in this text promises an outcome or states a decision, or None. template=True: text the server filled
        from the claim (a decision may be stated; promises still may not). template="note": a reviewer's own words
        beside a decision (decision words are refused; the guardrail's decision topic is not applied, since a note
        explains the decision it sits beside)."""
        # a customer's question quoted back is not a promise; taken out before splitting (its "?" would split it)
        for sent in (x for x in self.SENTENCES.split(WordingRule.QUOTED_Q.sub(" ", text or "")) if x and x.strip()):
            hit = self.BANNED.search(sent)
            if hit:
                return hit.group(0)
            if template is not True:  # False (free text) or "note" (a person's words beside the decision)
                stated = self._decision_stated(WordingRule.QUOTED_Q.sub(" ", sent))
                if stated:
                    return "decision:" + stated
        if use_guardrail and self.guardrail and (text or "").strip():
            # a customer's question quoted back is not a promise, for either check
            topics = self.guardrail.flags(WordingRule.QUOTED_Q.sub(" ", text))
            if topics is None:  # unreachable: the regex alone judged it; kept so compliance can re-check later
                self.repo.audit("system", "guardrail_skipped", claim_id, after={"text": (text or "")[:200]})
            else:
                topics = [x for x in topics if not (template and x == "DecisionStatement")]
                if "DecisionStatement" in topics and template is False:
                    # the topic also reacts to sentences that only say where the outcome is written or when it will
                    # be known ("결정 내용은 위 결정 카드와 통지서에 있어요", "If it's approved, you get a text"):
                    # ask again without those; still flagged = a statement of the outcome
                    # only the pointing words go ("결정 카드대로 청구가 받아들여졌어요" keeps "청구가 받아들여졌어요");
                    # if nothing is left to ask about, the flag stays
                    rest = self.OUTCOME_NOUNS.sub(" ", self.POINTER.sub(" ", WordingRule.QUOTED_Q.sub(" ", text)))
                    again = self.guardrail.flags(rest) if re.search(r"\w{2}", rest) else None
                    if again is not None and "DecisionStatement" not in again:
                        topics = [x for x in topics if x != "DecisionStatement"]
                if topics:
                    return "guardrail:" + ",".join(topics)
        return None

    def guard_reply(self, claim_id, reply, lc):
        """The agent's words pass the same compliance list as staff words, before they are sent: a sentence that
        promises an outcome is taken out, a careful line takes its place, and compliance sees what was removed."""
        if not self.promise_in(claim_id, reply):
            return reply
        kept, removed = [], []  # sentence by sentence (the guardrail is asked again only for a reply it flagged)
        for line in (reply or "").split("\n"):
            parts = re.split(r"(?<=[.!?。])\s+", line)
            hits = [self.promise_in(claim_id, p) if p.strip() else None for p in parts]
            ok = [p for p, h in zip(parts, hits) if not h]
            removed += [(p, h) for p, h in zip(parts, hits) if h]
            if ok or not line.strip():
                kept.append(" ".join(ok))
        safe = "\n".join(kept).strip()
        safe = (safe + "\n\n" if safe else "") + t(lc, "wording_safe")
        self.repo.audit("agent", "agent_wording_blocked", claim_id, before=[p for p, _ in removed][:3],
                        after={"phrases": [h for _, h in removed][:3]})
        return safe

    def check_wording(self, text, actor, claim_id, where, template=False):
        """Staff words to a traveler: blocked before sending, and the attempt kept for compliance training."""
        hit = self.promise_in(claim_id, text, template=template)
        if hit:
            self.repo.audit(actor, "wording_blocked", claim_id, after={"phrase": hit, "where": where, "text": (text or "")[:200]})
            raise WordingBlocked(hit)

    def _ingest(self, claim, inb):
        new = []
        for i, media in enumerate(inb.media):
            key = f"claims/{claim['id']}/{uuid.uuid4().hex[:8]}-{media.get('filename', 'doc')}"
            data = media.get("bytes")
            if data is None and media.get("s3_key"):  # uploaded from the web chat
                key, data = media["s3_key"], self.docs.get(media["s3_key"])
            if data is None:
                data = self.channels[inb.channel].fetch_media(media["media_id"], self.s.bucket, key)
            mime = media.get("mime", "image/jpeg")
            if mime == "application/pdf":
                ex = understanding.extract_document(self.llm, pdf_bytes=data)
                self._note_fallback(claim["id"])
            else:
                ex = understanding.extract_document(self.llm, image_bytes=data, image_format=mime.split("/")[-1].replace("jpg", "jpeg"))
                self._note_fallback(claim["id"])
            if ex.get("doc_type") == "identity_document":
                new.append(self._doc_view({"id": "id-photo", "extracted": ex, "flags": []},
                                          problem="ID documents go only through the secure claims page; we did not keep it"))
                continue
            if not media.get("s3_key"):
                self.docs.put(key, data, mime)
            doc = self.repo.add_document(claim["id"], {"source_channel": inb.channel, "s3_key": key, "mime": mime,
                                                       "filename": media.get("filename"), "extracted": ex, "flags": [],
                                                       "sha256": hashlib.sha256(data).hexdigest()})
            new.append(doc["id"])
        if inb.channel == "email" and inb.text and len(inb.text) > 80:
            ex = understanding.extract_document(self.llm, text=f"Subject: {inb.subject}\n\n{inb.text}")
            self._note_fallback(claim["id"])
            if ex.get("doc_type") in ("airline_letter", "eticket", "boarding_pass", "receipt"):
                key = f"claims/{claim['id']}/{uuid.uuid4().hex[:8]}-email.txt"
                self.docs.put(key, inb.text.encode(), "text/plain")
                doc = self.repo.add_document(claim["id"], {"source_channel": "email", "s3_key": key, "mime": "text/plain",
                                                           "filename": "forwarded-email.txt", "extracted": ex, "flags": []})
                new.append(doc["id"])
        return new

    # ---------- checks ----------

    def validation_context(self, claim, traveler, docs):
        sched = claim.get("scheduled_departure")
        replacement = claim.get("replacement_departure")
        if not replacement:
            for d in docs:
                ex = d.get("extracted") or {}
                letter, ticket = ex.get("letter") or {}, ex.get("ticket") or {}
                if letter.get("rebooked_departure"):
                    replacement = letter["rebooked_departure"].replace(" ", "T") + sched[-6:]
                elif ex.get("doc_type") == "boarding_pass" and ticket.get("date") and ticket.get("departure_time"):
                    replacement = f"{ticket['date']}T{ticket['departure_time']}{sched[-6:]}"
        if replacement:
            try:
                datetime.fromisoformat(replacement)
            except ValueError:  # malformed date from a document: fall back to the default window
                replacement = None
        if not replacement and sched:
            replacement = (datetime.fromisoformat(sched) + timedelta(hours=48)).isoformat()
        return {"traveler": traveler.get("passport_name") or traveler["name"], "flight": claim.get("flight"),
                "scheduled_departure": sched,
                "replacement_departure": replacement, "plan": claim.get("plan"),
                "allowed_regions": claim.get("allowed_regions") or []}

    def recheck(self, claim_id):
        claim = self.repo.get_claim(claim_id)
        traveler = self.repo.get_traveler(claim["traveler_id"])
        docs = self.repo.documents(claim_id)
        flags = validate(self.validation_context(claim, traveler, docs), docs) + self.cross_claim_flags(claim, docs)
        dec = (claim.get("claim_form") or {}).get("declared") or {}
        flags += [{"code": c, "doc": None, "detail": dec.get("detail") or ""} for c, hit in
                  (("other_insurance_declared", dec.get("other_insurance")), ("airline_paid_declared", dec.get("airline_paid"))) if hit]
        for d in docs:
            mine = [f for f in flags if f["doc"] == d["id"]]
            if mine != d.get("flags"):
                self.repo.update_document(claim_id, d, flags=mine)
                d["flags"] = mine
        state = checklist.compute(docs, claim.get("secure_submitted"))
        status = claim["status"]
        if status not in CLOSED:
            if any(f["code"] in BLOCKING for f in flags):
                status = "review"
            elif not checklist.missing(state):
                status = "ready"
            elif docs or status == "open":
                status = "collecting"
        claim = self.repo.update_claim(claim_id, flags=flags, status=status,
                                       checklist={k: v["done"] for k, v in state.items()})
        return claim, docs, state

    def cross_claim_flags(self, claim, docs):
        """Documents that belong to another claim on the same flight: the same photo (fingerprint), or a ticket/letter
        in the name of another traveler on that flight (a companion's papers). Each needs a look before payment."""
        others = [c for c in self.repo.list_claims() if c["id"] != claim["id"]
                  and c.get("flight") == claim.get("flight") and c.get("flight_date") == claim.get("flight_date")]
        if not others:
            return []
        me = frozenset(_names((self.repo.get_traveler(claim["traveler_id"]) or {}).get("passport_name")))
        prints, names = {}, {}
        for c in others:
            t = self.repo.get_traveler(c["traveler_id"]) or {}
            theirs = frozenset(_names(t.get("passport_name")))
            if theirs and theirs != me:  # same name as this traveler: their own papers, not someone else's
                names[theirs] = (c["id"], t.get("name"))
            for d in self.repo.documents(c["id"]):
                if d.get("sha256") and (d["sha256"] not in prints or d["seq"] < prints[d["sha256"]][2]):
                    prints[d["sha256"]] = (c["id"], t.get("name"), d["seq"])  # keep the first upload of each photo
        flags = []
        for d in docs:
            ex = d.get("extracted") or {}
            person = ((ex.get("letter") or {}).get("passenger") or (ex.get("ticket") or {}).get("passenger") or "")
            first = prints.get(d.get("sha256")) if d.get("sha256") else None
            # the same photo counts against a claim only if another claim had it first: the original owner keeps theirs
            hit = (first if first and first[2] < d["seq"] else None) or (names.get(frozenset(_names(person))) if person else None)
            if hit:
                flags.append({"code": "other_claim_document", "doc": d["id"], "detail": f"{hit[0]} ({hit[1]})"})
        return flags

    def _doc_view(self, doc, problem=None):
        ex = doc.get("extracted") or {}
        kind = ex.get("doc_type")
        # Language-neutral summary (the document type is shown or said separately): "XX123 · 2026-10-05 · cancelled · Typhoon"
        join = lambda *parts: " · ".join(str(p) for p in parts if p not in (None, ""))
        if ex.get("legibility") == "unreadable":
            summary = "unreadable"
        elif kind == "receipt":
            r = ex.get("receipt") or {}
            amount = f"¥{r['total']:,}" if r.get("currency") == "JPY" and isinstance(r.get("total"), (int, float)) \
                else join(r.get("currency"), r.get("total"))
            summary = join(r.get("merchant"), amount, r.get("datetime"))
        elif kind == "airline_letter":
            l = ex.get("letter") or {}
            summary = join(l.get("flight"), l.get("date"), l.get("status"), l.get("reason_text"))
        elif kind in ("eticket", "boarding_pass"):
            t = ex.get("ticket") or {}
            summary = join(t.get("flight"), t.get("date"), t.get("departure_time"))
        else:
            summary = kind or "document"
        summary = f"{kind}: {summary}" if kind else summary
        problems = [problem] if problem else [_traveler_problem(f) for f in doc.get("flags", [])]
        return {"summary": summary, "problems": problems}

    def turn_context(self, claim, traveler, state, new_doc_ids):
        docs = {d["id"]: d for d in self.repo.documents(claim["id"])}
        new_docs = [self._doc_view(docs[x]) if isinstance(x, str) else x for x in new_doc_ids]
        event = self.repo.get_event(claim["event_id"]) if claim.get("event_id") else None
        lc = loc(traveler)
        now = self.clock()
        dep = (event or {}).get("replacement_departure")
        try:  # once the replacement flight has left, the traveler is no longer "about to board"
            departed = bool(dep) and datetime.fromisoformat(dep) <= now
        except ValueError:
            departed = None
        return {
            "today": now.date().isoformat(),
            "now_korea_time": (now + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
            "replacement_flight_departed": departed,
            # held for the special investigation unit: neutral answers only (see the agent prompt)
            "claim_on_hold": bool(claim.get("siu")),
            "reply_language": i18n.LANGUAGE_NAME[lc],
            "locale": lc,
            "traveler": first_name(traveler),
            "claim": {"id": claim["id"], "status": claim["status"], "flight": claim.get("flight"),
                      "date": claim.get("flight_date"), "plan": claim.get("plan")},
            "event": {k: event.get(k) for k in ("type", "reason", "delay_hours", "replacement_flight")} if event else None,
            "coverage": {k: (claim.get("coverage") or {}).get(k) for k in ("status", "cited_clauses")},
            "checklist": {"complete": not checklist.missing(state), "missing": checklist.describe_missing(state, lc),
                          "received": [i18n.label(lc, k) for k in checklist.REQUIRED if state[k]["done"]]},
            "new_documents": new_docs,
            "claim_level_problems": [_traveler_problem(f)
                                     for f in claim.get("flags", []) if not f.get("doc") and f["code"] != "missing_airline_letter"],
        }

    # ---------- agent tools ----------

    def step(self, claim_id, kind, **detail):
        """What the agent is doing right now, for the web chat to show live (search, sources found, policy checked…)."""
        turn = self._turn_for(claim_id)
        if not turn or turn["channel"] != "web":
            return
        turn["steps"].append({"kind": kind, **{k: v for k, v in detail.items() if v not in (None, "", [])}})
        sources = (turn["ui"] or {}).get("sources")  # the source card shows while the reply is still being written
        self.repo.update_claim(claim_id, activity={"steps": turn["steps"], "at": now_iso(), **({"sources": sources} if sources else {})})

    def search_documents(self, claim_id, query):
        """RAG over the policy and FAQ PDFs: top policy clauses and FAQ answers, traveler's language first."""
        if self._embed is None and self.s.llm_backend == "bedrock":
            self._embed = rag.bedrock_embedder(self.s.region)
        lc = loc(self.repo.get_traveler(self.repo.get_claim(claim_id)["traveler_id"]))
        self.step(claim_id, "search", query=query[:80])
        found = {"policy": rag.search(query, lc, 3, self._embed, kinds={"policy"}),
                 "faq": rag.search(query, lc, 3, self._embed, kinds={"faq"})}
        clauses = list(dict.fromkeys(c["clause"] for c in found["policy"] if c["clause"]))
        self.step(claim_id, "found", items=[{"id": x, "name": rag.clause_name(x, lc)} for x in clauses], faq=len(found["faq"]))
        return found

    def evidence_panel(self, claim_id, action, chunk_id=None, caption=None):
        """The agent attaches sources (highlighted passages) to its web reply, numbered [1], [2] in the order shown,
        or folds the open source card. Rides on the reply message; the traveler can still toggle the card."""
        if action == "show" and chunk_id not in rag.by_id():
            return {"ok": False, "error": f"unknown chunk {chunk_id}"}
        turn = self._turn_for(claim_id)
        if not turn:
            return {"ok": True}
        ui = turn["ui"] = turn["ui"] or {"sources": [], "fold": False}
        if action != "show":
            ui["fold"] = True
            return {"ok": True}
        for src in ui["sources"]:
            if src["chunk"] == chunk_id:
                return {"ok": True, "ref": src["n"]}
        if len(ui["sources"]) >= MAX_SOURCES:
            return {"ok": False, "error": f"at most {MAX_SOURCES} sources per reply"}
        if caption and self.promise_in(claim_id, caption):  # the caption is the agent's words too, shown before the reply
            self.repo.audit("agent", "agent_wording_blocked", claim_id, before=[caption[:200]], after={"where": "caption"})
            caption = None
        ui["sources"].append({"n": len(ui["sources"]) + 1, "chunk": chunk_id, "caption": caption})
        self.step(claim_id, "cite", n=ui["sources"][-1]["n"], clause=rag.by_id()[chunk_id].get("clause"), name=rag.title(rag.by_id()[chunk_id]))
        return {"ok": True, "ref": ui["sources"][-1]["n"]}

    def progress(self, claim_id, key="progress_policy"):
        """One short interim message per turn before a slow step (policy check), on the channel in use."""
        turn = self._turn_for(claim_id)
        if not turn or turn["acked"] or turn["channel"] in ("email", "web"):  # the web chat shows live steps instead
            return
        turn["acked"] = True
        traveler = self.repo.get_traveler(self.repo.get_claim(claim_id)["traveler_id"])
        self.router.send(traveler, claim_id, "progress", t(loc(traveler), key), reply_on=turn["channel"])

    def coverage_case(self, claim, question):
        traveler = self.repo.get_traveler(claim["traveler_id"])
        pol = self._policy(traveler["id"])
        event = self.repo.get_event(claim["event_id"]) if claim.get("event_id") else None
        state = checklist.compute(self.repo.documents(claim["id"]), claim.get("secure_submitted"))
        return {
            "today": self.clock().date().isoformat(),
            "traveler": {"plan": pol.get("plan"), "policy_bought_at": pol.get("purchased_at"),
                         "period_start": pol.get("period_start"), "period_end": pol.get("period_end")},
            "trip": {"flight": f"{claim.get('flight')} {claim.get('route') or ''}".strip(),
                     "scheduled_departure": claim.get("scheduled_departure")},
            "event": {k: event.get(k) for k in ("type", "delay_hours", "reason", "announced_at", "replacement_flight")} if event else None,
            "documents_received": [k for k in checklist.REQUIRED if state[k]["done"]],
            "message": question,
            "reply_language": i18n.LANGUAGE_NAME[loc(traveler)],
            "review_language": i18n.LANGUAGE_NAME[i18n.norm(self.s.review_locale)],
        }

    def assess_coverage(self, claim_id, question):
        claim = self.repo.get_claim(claim_id)
        case = self.coverage_case(claim, question)
        t_ = case["traveler"]
        self.step(claim_id, "check", plan=t_["plan"], bought=(t_["policy_bought_at"] or "")[:10],
                  period=f"{t_['period_start']} ~ {t_['period_end']}", event=(case["event"] or {}).get("reason"))
        result = understanding.assess_coverage(self.llm, case)
        self._note_fallback(claim_id)
        lc = loc(self.repo.get_traveler(claim["traveler_id"]))
        self.step(claim_id, "verdict", status=result.get("status"),
                  clauses=[{"id": x, "name": rag.clause_name(x, lc)} for x in result.get("cited_clauses") or []])
        self.repo.update_claim(claim_id, coverage={k: result.get(k) for k in
                                                   ("status", "cited_clauses", "covered_expenses", "not_covered_expenses")})
        return result

    def _note_fallback(self, claim_id):
        """Where this claim's data was processed, when it left the regional profile: kept with the claim's audit trail."""
        fb = getattr(self.llm, "last_fallback", None)
        if fb:
            self.llm.last_fallback = None
            self.repo.audit("system", "bedrock_fallback", claim_id, after={**fb, "processed_via": fb["to"].split(".", 1)[0]})

    def _after_send(self, claim_id, text):
        """Wording that got past every check before sending: logged for the ops alarm and kept for compliance."""
        hit = self.promise_in(claim_id, text, use_guardrail=False, template=True)  # sent texts include our decision templates
        if hit:
            print(f"WORDING_AFTER_SEND {claim_id} {hit!r}")
            self.repo.audit("system", "wording_after_send", claim_id, after={"phrase": hit, "text": (text or "")[:200]})

    def _mirror_web(self, traveler, claim_id, text):
        """Status news goes out by text/email (the web chat never starts a conversation); if the traveler uses the
        web chat, a copy also lands there so the page they are looking at says what just happened."""
        if (traveler.get("last_inbound") or {}).get("web"):
            self._after_send(claim_id, text)
            self.repo.add_message(claim_id, {"direction": "out", "channel": "web", "kind": "text", "text": text, "status": "sent",
                                             "purpose": "mirror", "chosen_reason": "copy in the web chat the traveler is using"})

    def secure_link(self, claim_id):
        """A fresh 7-day link each time; only its hash is stored, never the link itself."""
        self.step(claim_id, "secure")
        return f"{self.s.public_base_url}/secure/{self.repo.new_secure_token(claim_id)}"

    def chat_link(self, claim_id):
        """The traveler's way in: no login, the 30-day link identifies this one claim."""
        return f"{self.s.public_base_url}/c/{self.repo.new_link_token(claim_id, 'chat', days=30)}"

    def secure_submit(self, token, form):
        claim_id = self.repo.claim_for_token(token)
        if not claim_id:
            return None
        # Bank details are passed to the insurer's payment system, not stored here: we keep the bank and the last 4 digits
        # so staff can read them back to the traveler on the phone.
        country = form.get("country") or "kr"
        number = {"kr": form.get("account"), "us": form.get("us_account"), "other": form.get("iban")}.get(country) or form.get("bank") or ""
        digits = re.sub(r"[^0-9A-Za-z]", "", number)
        if len(digits) < 6 or (country == "us" and not re.fullmatch(r"\d{9}", form.get("routing") or "")):
            return None
        bank = {"kr": form.get("bank_name"), "us": "US routing " + (form.get("routing") or "")[-4:], "other": form.get("swift") or "IBAN"}.get(country)
        before = self.repo.get_claim(claim_id)
        old = before.get("claim_form") or {}
        correction = bool(old.get("bank_reset"))  # only the account was asked for again; signature and ID stand
        # other compensation the traveler declared ([TD-4](d) airline-paid, other insurance): a reviewer must look
        declared = old.get("declared") if correction else {
            "other_insurance": form.get("other_insurance") == "yes", "airline_paid": form.get("airline_paid") == "yes",
            "detail": (form.get("dup_detail") or "").strip()[:120], "asked": "other_insurance" in form}
        self.repo.update_claim(claim_id, secure_submitted=True, claim_form={
            "signed_name": form.get("signed_name") or old.get("signed_name"), "bank_details_provided": True, "holder": form.get("holder"),
            "bank": bank, "account_last4": digits[-4:], "country": country, "declared": declared,
            **({"previous": old.get("previous"), "corrected_at": now_iso()} if correction else {})})
        self.repo.audit("traveler", "bank_corrected" if correction else "secure_form", claim_id)
        if correction and before["status"] in CLOSED:  # the packet went out with the old account: tell the claims inbox
            prev = old.get("previous") or {}
            self.channels["email"].send_email(self.s.reviewer_email, f"[ClausePath] {claim_id} — payout account changed",
                                              f"Payout account changed by the traveler on the secure page: "
                                              f"{prev.get('bank')} •••{prev.get('account_last4')} -> {bank} •••{digits[-4:]}.")
            traveler = self.repo.get_traveler(before["traveler_id"])
            text = t(loc(traveler), "bank_corrected", bank=bank, last4=digits[-4:])
            self._mirror_web(traveler, claim_id, text)
            lc = loc(traveler)
            self.router.send(traveler, claim_id, "status", text, template=("claim_status_update", [
                first_name(traveler), claim_id, t(lc, "status_reviewing"), text]))
            return self.repo.get_claim(claim_id)
        claim, docs, state = self.recheck(claim_id)
        traveler = self.repo.get_traveler(claim["traveler_id"])
        lc = loc(traveler)
        missing = checklist.describe_missing(state, lc)
        text = t(lc, "secure_done_missing", missing=", ".join(missing)) if missing else t(lc, "secure_done_complete")
        self.router.send(traveler, claim_id, "status", text, template=("claim_status_update", [
            first_name(traveler), claim_id, t(lc, "status_missing" if missing else "status_ready"), t(lc, "no_note")]))
        self._mirror_web(traveler, claim_id, text if missing else t(lc, "secure_done_complete_web"))
        return claim

    def submit_claim(self, claim_id, actor="agent"):
        claim, docs, state = self.recheck(claim_id)
        missing = checklist.describe_missing(state)
        if missing:
            return {"submitted": False, "missing": missing}
        if claim["status"] in CLOSED:
            return {"submitted": True, "claim_id": claim_id, "status": claim["status"]}
        traveler = self.repo.get_traveler(claim["traveler_id"])
        event = self.repo.get_event(claim["event_id"]) if claim.get("event_id") else None
        submitted = {**claim, "status": "submitted"}
        pol = self._policy(traveler["id"])
        text, html = build_packet(submitted, traveler, event, docs, self.repo.messages(claim_id), self.s.review_locale, pol)
        copy_text, copy_html = build_packet(submitted, traveler, event, docs, self.repo.messages(claim_id), loc(traveler), pol)
        attachments = [(d.get("filename") or f"{d['id']}.jpg", self.docs.get(d["s3_key"]), d["mime"])
                       for d in docs if d.get("s3_key")]
        subject = f"[ClausePath] {claim_id} — {traveler['name']}"
        pid = self.channels["email"].send_email(self.s.reviewer_email, subject, text, html=html, attachments=attachments)
        self.repo.add_message(claim_id, {"direction": "out", "channel": "email", "kind": "packet", "text": text,
                                         "subject": subject, "attachments": [a[0] for a in attachments],
                                         "provider_id": pid, "purpose": "packet", "to": "reviewer",
                                         "chosen_reason": "claim packet to the insurer's claims inbox"})
        review = any(f["code"] in BLOCKING for f in claim.get("flags", []))
        self.repo.update_claim(claim_id, status="submitted", submitted_at=now_iso(), needs_review=review, packet_html=html)
        self.repo.audit(actor, "submit", claim_id)
        params, lc = [first_name(traveler), claim_id], loc(traveler)
        self.router.send(traveler, claim_id, "receipt",
                         render_template("claim_received", params, lc) + t(lc, "receipt_copy") + "\n\n" + copy_text,
                         template=("claim_received", params), email_subject=t(lc, "subject_received", claim=claim_id),
                         email_html=copy_html)
        est, form = estimate(docs, self._policy(traveler["id"]).get("plan") or claim.get("plan")), claim.get("claim_form") or {}
        claim = self.repo.get_claim(claim_id)
        due = self.due(claim)
        when = t(lc, "when_" + due["kind"], date=i18n.due_date(due["date"], lc)) if due else t(lc, "when_none")
        krw = f"(약 {round(est.get('estimate_krw') or 0, -4) / 10000:,.0f}만 원)" if lc == "ko" and est.get("estimate_krw") else ""
        acct = (f", 입금 계좌 {form['bank']} •••{form['account_last4']}" if lc == "ko" else f", paid to {form['bank']} •••{form['account_last4']}") \
            if form.get("account_last4") else ""
        self._mirror_web(traveler, claim_id, render_template("claim_received", params, lc)
                         + t(lc, "receipt_next", usd=f"{est.get('estimate_usd', 0):,.2f}", krw=krw, acct=acct, when=when))
        return {"submitted": True, "claim_id": claim_id, "needs_review": review}

    def due(self, claim):
        """Expected date after submission ([CL-4]): payment for a complete claim, or a report-back when something needs
        a check. None before submission or when the insurer turned dates off."""
        if not self.s.due_dates or not claim.get("submitted_at") or claim["status"] not in ("submitted", "approved"):
            return None
        if claim.get("siu") or (claim.get("siu_linked") or {}).get("hold_payment"):
            return None  # held for review: no date is promised
        check = bool(claim.get("needs_review")) and claim["status"] != "approved"
        days = self.s.review_business_days if check else self.s.pay_business_days
        return {"kind": "check" if check else "pay", "date": business_days_after(claim["submitted_at"], days)}

    # ---------- demo ----------

    def create_demo_personas(self, today=None, depart=None):
        """Same typhoon cancellation for seven travelers with different policies; returns their chat links.
        No messages are sent: the presenter opens each chat from the dashboard. With the simulated phone (SIM_PHONE)
        each gets a fictional number (+1 202 555-01xx) and SMS consent, so the disruption alert reaches the phone page."""
        from .seed import PERSONAS, event, persona_records
        today = today or self.clock().astimezone(timezone(timedelta(hours=9))).date()
        ev = self.repo.put_event(event(today, depart or "17:00"))
        self.repo.link_event_to_flight(ev)
        out = []
        for i, p in enumerate(PERSONAS):
            traveler, policy, segment = persona_records(p, today)
            if self.s.sim_phone:  # preferences differ, so the router's choice (and its logged reason) differs too
                traveler.update(phone=f"{SIM_NUMBER_PREFIX}{i:02d}", consent={"sms": True}, preferred_channel=SIM_PREFERENCE.get(p["key"], "sms"))
            if self.s.demo_email and p["key"] in ("jordan", "plus"):  # the English and the Korean demo traveler
                traveler.update(email=self.s.demo_email, consent={**traveler["consent"], "email": True})
            self.register_traveler(traveler, policy, [segment])
            claim = self._new_claim(self.repo.get_traveler(traveler["id"]), ev)
            out.append({"name": p["name"], "plan": p["plan"], "language": p["lang"], "purchased_at": policy["purchased_at"],
                        "period": f"{policy['period_start']} ~ {policy['period_end']}", "expect": p["expect"],
                        "claim_id": claim["id"], "chat": self.chat_link(claim["id"])})
        return out

    DEMO_TRAVELERS = ("trv-persona-", "trv-demo")  # created by Demo personas and by seed.py

    def reset_demo_data(self, actor):
        """Remove every demo traveler and their claims (and the flight's events once nobody is booked on it), so a
        recording starts from an empty desk. Only demo travelers are touched; the audit log stays and records this."""
        from .seed import PERSONAS
        travelers = {c["traveler_id"] for c in self.repo.list_claims()} | {f"trv-persona-{p['key']}" for p in PERSONAS} | {"trv-demo"}
        removed = {"travelers": 0, "claims": 0, "documents": 0, "events": 0}
        flights = set()
        for tid in sorted(travelers):
            if not tid.startswith(self.DEMO_TRAVELERS) or not self.repo.get_traveler(tid):
                continue
            n, keys, fl = self.repo.purge_traveler(tid)
            removed["travelers"] += 1
            removed["claims"] += n
            flights |= set(fl)
            for k in keys:
                try:
                    self.docs.delete(k)
                    removed["documents"] += 1
                except Exception:  # an object already gone or expired: nothing to do
                    pass
        for f in flights:
            removed["events"] += self.repo.purge_flight_events(f)
        self.repo.audit(actor, "demo_reset", "demo", after=removed)
        return removed

    # ---------- reviewer / ops ----------

    def unresolved(self, claim_id):
        """What a reviewer must have looked at before approving: blocking flags, documents needing a check, retakes."""
        claim = self.repo.get_claim(claim_id)
        state = checklist.compute(self.repo.documents(claim_id), claim.get("secure_submitted"))
        # a name/flight mismatch on a document already shows as that item's "check"; count it once
        out = sorted({f["code"] for f in claim.get("flags", []) if f["code"] in BLOCKING and f["code"] not in checklist.CHECK_FLAGS})
        out += [f"check:{k}" for k in checklist.REQUIRED if state[k]["check"] and not state[k]["done"]]
        out += [f"retake:{k}" for k in checklist.REQUIRED if state[k]["pending"]]
        if (claim.get("claim_form") or {}).get("bank_reset"):
            out.append("bank_pending")  # do not pay to the old account
        return out

    # How a caller's identity was checked: ticked items the server counts (not free text), and what each action needs.
    ID_CHECKS = ("dob", "policy", "booking", "callback")
    ID_NEEDED = {"resend_link": 1, "bank_correction": 2, "contact_update": 2}

    def identity(self, action, checks=None, other=""):
        """-> text for the audit trail, or PermissionError when fewer checks than the action needs."""
        picked = sorted(set(checks or []) & set(self.ID_CHECKS))
        other = (other or "").strip()
        n = len(picked) + (1 if len(other) >= 4 else 0)  # "기타" counts once, and only with real words
        if n < self.ID_NEEDED.get(action, 0):
            raise PermissionError("identity_checks_needed")
        return ", ".join(picked + ([other] if other else []))

    # Contact-centre actions: the traveler is told something without a decision being made.
    # resend_link / bank_correction need `reason` = how the caller's identity was checked (audit trail only).
    SUPPORT = {"message", "resend_link", "bank_correction"}

    def decide(self, claim_id, reviewer, action, amount_usd=None, clauses=(), note="", reason="", confirmed_by=None, adjust=None,
               correction=False):
        """The reviewer's decision: approve the estimate (full), approve less (partial) or deny.
        Partial and deny need the policy clauses they rest on and a note the traveler can understand, with how to object."""
        claim = self.repo.get_claim(claim_id)
        if not confirmed_by:  # one decision at a time; a final one changes only through a correction with its own approval
            if claim.get("pending_decision"):
                raise DecisionConflict("pending_exists")
            if claim.get("decision") and not correction:
                raise DecisionConflict("already_decided")
            if correction and not claim.get("decision"):
                raise DecisionConflict("nothing_to_correct")
            if correction and not (reason or "").strip():
                raise ValueError("correction_needs_reason")
            self._appeal_reviewer_ok(claim, reviewer)
        traveler = self.repo.get_traveler(claim["traveler_id"])
        docs, plan = self.repo.documents(claim_id), self._policy(traveler["id"]).get("plan") or claim.get("plan")
        est = estimate(docs, plan)
        estimate_usd = est.get("estimate_usd") or 0
        lines = []
        if adjust and action != "deny":  # receipt by receipt: the amount follows from what the reviewer counted
            from .estimate import ADJUST_REASONS
            adjust = {k: {"usd": round(float(v.get("usd") or 0), 2), "reason": v.get("reason") if v.get("reason") in ADJUST_REASONS else "other"}
                      for k, v in adjust.items()}
            adj = estimate(docs, plan, adjust)
            amount_usd = adj.get("estimate_usd") or 0
            lines = [{"doc": r["doc"], "merchant": r.get("merchant"), "from": r["rule_usd"], "to": r["counted_usd"], "reason": r["reason"]}
                     for r in adj["rows"] if "rule_usd" in r]
        if action == "deny":
            kind, amount = "deny", 0.0
        else:
            amount = round(float(estimate_usd if amount_usd in (None, "") else amount_usd), 2)
            if amount < 0 or (est.get("covered") and amount > est["trip"]):
                raise ValueError("amount outside the plan limits")
            kind = "deny" if amount == 0 and estimate_usd > 0 else "partial" if amount < estimate_usd else "full"  # $0 is a denial
        if not confirmed_by and (note or "").strip():  # what the reviewer writes goes to the traveler
            # the reviewer's own words beside the decision: they may explain it, not state another outcome or amount
            self.check_wording(note, reviewer, claim_id, "decision", template="note")
        if kind != "full" and (not clauses or not (note or "").strip()):
            raise ValueError("clauses_and_note_required")  # a partial payment or denial must say why, in words the traveler gets
        # paying past open checks, or denying while documents still need checking (another name, unreadable: usually
        # a request for documents, not a denial), needs a reason in the audit trail
        open_checks = [c for c in self.unresolved(claim_id) if kind != "deny" or c.split(":")[0] in ("check", "retake")]
        if open_checks and not (reason or "").strip():
            raise PermissionError("deciding with open checks needs a reason")
        if not claim.get("submitted_at") and not confirmed_by and not (reason or "").strip():
            raise ValueError("not_submitted")  # decide on what the traveler has not submitted only with a stated reason
        over = kind != "deny" and amount > estimate_usd + 0.005  # paying more than the receipts and limits support
        if over and not (reason or "").strip():
            raise ValueError("over_estimate_needs_reason")
        decision = {"type": kind, "amount_usd": amount, "estimate_usd": estimate_usd, "clauses": list(clauses),
                    "appeal_by": (self.clock() + timedelta(days=30)).date().isoformat() if kind != "full" else None,
                    "lines": lines, "adjust": adjust or None, "by": reviewer, "at": now_iso(),
                    "note": (note or "").strip() or None}  # the reviewer's explanation, also printed in the letter
        # Four eyes: a denial, an approval over the limit, or one past open checks waits for a second reviewer.
        why = [w for w, hit in (("deny", kind == "deny" and self.s.dual_for_deny),
                                ("partial", kind == "partial" and self.s.dual_for_partial),
                                ("amount", amount >= self.s.dual_approval_over_usd),
                                ("over_estimate", over),
                                ("not_covered", kind != "deny" and amount > 0 and not est.get("covered")),
                                ("correction", correction),
                                ("open_checks", bool(open_checks))) if hit]
        if why and not confirmed_by:
            pending = {**decision, "note": note, "reason": reason, "why": why, "pid": uuid.uuid4().hex[:8], "correction": correction}
            # a denial while documents still need checking, without CL-1 (claim documents) among its grounds: those are
            # usually a request for documents, not a ground to deny; the second reviewer is told before confirming
            doc_checks = [c for c in self.unresolved(claim_id) if c.split(":")[0] in ("check", "retake")]
            if kind == "deny" and doc_checks and "CL-1" not in clauses:
                pending["basis_check"] = {"open": doc_checks, "clauses": list(clauses)}
            self.repo.update_claim(claim_id, pending_decision=pending)
            self.repo.add_action(claim_id, {"reviewer": reviewer, "type": "decision_requested", "note": None,
                                            "reason": reason or None, "decision": pending})
            self.repo.audit(reviewer, "decision_requested", claim_id, after={"type": kind, "amount_usd": amount, "why": why},
                            reason=reason or None, basis_check=pending.get("basis_check"))
            return self.repo.get_claim(claim_id)
        if confirmed_by:
            decision["confirmed_by"] = confirmed_by
        if correction:  # keep what it replaced
            prev = claim.get("decision") or {}
            decision["corrects"] = {k: prev.get(k) for k in ("type", "amount_usd", "at", "by", "confirmed_by")}
            # everyone behind any earlier version stays out of an objection review
            decision["earlier_people"] = sorted({p for p in (*(prev.get("earlier_people") or []), prev.get("by"), prev.get("confirmed_by")) if p})
            if (claim.get("appeal") or {}) and not claim["appeal"].get("resolved"):
                self.repo.update_claim(claim_id, appeal={**claim["appeal"], "resolved": {"outcome": "corrected", "by": confirmed_by or reviewer,
                                                                                          "at": now_iso()}})
        self.repo.update_claim(claim_id, decision=decision, pending_decision=None)
        if claim.get("submitted_at"):  # the packet becomes the record of the decision (versioned)
            decided = {**self.repo.get_claim(claim_id), "status": "denied" if kind == "deny" else "approved"}
            _, html = build_packet(decided, traveler, self.repo.get_event(claim["event_id"]) if claim.get("event_id") else None,
                                   docs, self.repo.messages(claim_id), self.s.review_locale, self._policy(traveler["id"]))
            self.repo.update_claim(claim_id, packet_html=html, packet_version=(claim.get("packet_version") or 1) + 1)
        lc = loc(traveler)
        # The notice in blocks a tired traveler can scan: result / why less / when / if you disagree
        money = f"${amount:,.2f}" + (f"(약 {round(amount * KRW_PER_USD, -4) / 10000:,.0f}만 원)" if lc == "ko" else "")
        blocks = [("result", t(lc, f"decision_{kind}", usd=money))]
        if kind != "full":
            why = [t(lc, "decision_line", merchant=ln["merchant"] or "", frm=f"{ln['from']:,.2f}", to=f"{ln['to']:,.2f}",
                     why=t(lc, "adj_" + ln["reason"])) for ln in lines]
            why += [note.strip()] if (note or "").strip() else []
            why += [t(lc, "decision_basis", clauses=", ".join(f"{c} {rag.clause_name(c, lc)}" for c in clauses))] if clauses else []
            blocks.append(("why_" + kind, " ".join(why)))
        due = self.due({**self.repo.get_claim(claim_id), "status": "approved" if kind != "deny" else "denied"})
        if due and kind != "deny":
            blocks.append(("when", t(lc, "when_pay", date=i18n.due_date(due["date"], lc))))
        if kind != "full":
            deadline = i18n.due_date((self.clock() + timedelta(days=30)).date().isoformat(), lc, year=True)
            blocks.append(("appeal", t(lc, "appeal", phone=self.s.insurer(lc)["phone"], deadline=deadline)))
        # the letter travels with the text/email, so it can be found later (company, card insurer) without the chat
        blocks.append(("letter", t(lc, "letter_line", link=self.chat_link(claim_id) + "/decision")))
        text = "\n\n".join(f"[{t(lc, 'dec_h_' + k)}] {v}" for k, v in blocks)
        if correction:
            text = t(lc, "decision_corrected") + "\n\n" + text
        return self.reviewer_action(claim_id, confirmed_by or reviewer, "deny" if kind == "deny" else "approve",
                                    text, reason, decision=decision)

    def _appeal_reviewer_ok(self, claim, reviewer):
        """While the traveler's objection is open, the people behind the first decision do not review it."""
        a, d = claim.get("appeal") or {}, claim.get("decision") or {}
        if a and not a.get("resolved") and reviewer in (d.get("by"), d.get("confirmed_by"), *(d.get("earlier_people") or [])):
            raise SecondPersonRequired("appeal_needs_new_reviewer")

    def resolve_appeal(self, claim_id, reviewer, note):
        """A different reviewer looked again and keeps the decision: say why, and where to go next."""
        claim = self.repo.get_claim(claim_id)
        a = claim.get("appeal") or {}
        if not a or a.get("resolved"):
            raise ValueError("no_open_appeal")
        self._appeal_reviewer_ok(claim, reviewer)
        note = (note or "").strip()
        if len(note) < 10:
            raise ValueError("appeal_answer_needs_reason")
        for label in ("확인한 내용:", "What I checked:"):  # the template's own words do not count as a reason
            if label in note and len(note.split(label, 1)[1].strip()) < 10:
                raise ValueError("appeal_answer_needs_reason")
        self.check_wording(note, reviewer, claim_id, "appeal_reply", template="note")  # a person's words: no outcome or amount of their own
        if note[-1] not in ".!?。":
            note += "."
        self.repo.update_claim(claim_id, appeal={**a, "resolved": {"outcome": "upheld", "by": reviewer, "at": now_iso(), "note": note}})
        traveler = self.repo.get_traveler(claim["traveler_id"])
        lc = loc(traveler)
        text = t(lc, "appeal_upheld", note=note, phone=self.s.insurer(lc)["phone"])
        params = [first_name(traveler), claim_id, t(lc, "status_" + claim["status"]) if f"status_{claim['status']}" in i18n.STRINGS[lc]
                  else t(lc, "status_reviewing"), text]
        self.router.send(traveler, claim_id, "decision", text, template=("claim_status_update", params),
                         email_subject=t(lc, "subject_update", claim=claim_id))
        self._mirror_web(traveler, claim_id, text)
        self.repo.add_action(claim_id, {"reviewer": reviewer, "type": "appeal_upheld", "note": note, "reason": None})
        self.repo.audit(reviewer, "appeal_upheld", claim_id, after={"reply_by": a.get("reply_by")})
        return self.repo.get_claim(claim_id)

    def appeal(self, claim_id, text, source="traveler", staff=None):
        """The traveler disagrees with the decision: recorded, acknowledged with a reply-by date, and shown to staff."""
        claim = self.repo.get_claim(claim_id)
        dec = claim.get("decision") or {}
        if not dec or dec.get("type") == "full":
            raise ValueError("nothing_to_appeal")
        if claim.get("appeal"):
            raise DecisionConflict("already_appealed")
        if dec.get("appeal_by") and self.clock().date().isoformat() > dec["appeal_by"]:
            raise ValueError("appeal_window_closed")
        text = (text or "").strip()[:1000]
        if len(text) < 5:
            raise ValueError("appeal_needs_text")
        reply_by = business_days_after(self.clock().isoformat(), self.s.review_business_days)
        self.repo.update_claim(claim_id, appeal={"text": text, "at": now_iso(), "reply_by": reply_by, "source": source})
        traveler = self.repo.get_traveler(claim["traveler_id"])
        lc = loc(traveler)
        self.repo.add_message(claim_id, {"direction": "in", "channel": "web" if source == "traveler" else "phone", "kind": "appeal", "text": text})
        self._mirror_web(traveler, claim_id, t(lc, "appeal_received", date=i18n.due_date(reply_by, lc)))
        self.repo.add_action(claim_id, {"reviewer": staff or "traveler", "type": "appeal" if source == "traveler" else "appeal_by_phone",
                                        "note": text, "reason": None})
        self.repo.audit(staff or "traveler", "appeal" if source == "traveler" else "appeal_by_phone", claim_id, after={"reply_by": reply_by})
        return self.repo.get_claim(claim_id)

    def withdraw_appeal(self, claim_id):
        """The traveler takes the objection back: closed as "withdrawn", staff see it, nothing else changes."""
        claim = self.repo.get_claim(claim_id)
        a = claim.get("appeal") or {}
        if not a or a.get("resolved"):
            raise ValueError("no_open_appeal")
        self.repo.update_claim(claim_id, appeal={**a, "resolved": {"outcome": "withdrawn", "by": "traveler", "at": now_iso()}})
        traveler = self.repo.get_traveler(claim["traveler_id"])
        self._mirror_web(traveler, claim_id, t(loc(traveler), "appeal_withdrawn"))
        self.repo.add_action(claim_id, {"reviewer": "traveler", "type": "appeal_withdrawn", "note": None, "reason": None})
        self.repo.audit("traveler", "appeal_withdrawn", claim_id)
        return self.repo.get_claim(claim_id)

    SIU_OUTCOMES = ("no_fraud", "fraud_confirmed", "referred_to_police")

    def siu_refer(self, claim_id, reviewer, reason, clear=False, outcome=None, admin=False, kind=None):
        """A document from another claim may be fraud, not a mistake: hold the claim for the special investigation unit
        instead of asking the traveler (which only tells them it was noticed). The linked claims are marked too.
        Nothing is sent to the traveler; while held, decisions and messages wait. clear=True ends the hold."""
        if not (reason or "").strip():
            raise ValueError("reason_required")
        claim = self.repo.get_claim(claim_id)
        if clear:
            # SIU's conclusion is recorded by an admin (standing in for the SIU team), never by the referrer.
            # "No fraud" ends the hold; "fraud confirmed" / "referred to the police" keep it (nothing reaches the
            # traveler) and only allow a denial, which itself needs a second reviewer.
            if outcome not in self.SIU_OUTCOMES:
                raise ValueError("siu_outcome_required")
            if not admin:
                raise PermissionError("siu_conclusion_admins_only")
            if (claim.get("siu") or {}).get("by") == reviewer:
                raise SecondPersonRequired("the person who referred the claim cannot clear it")
            self.repo.audit(reviewer, "siu_cleared", claim_id, after={"note": reason, "outcome": outcome})
            if outcome != "no_fraud":
                self.repo.update_claim(claim_id, siu={**claim["siu"], "outcome": outcome, "concluded_by": reviewer, "concluded_at": now_iso()})
                return self.repo.get_claim(claim_id)
            for other in (claim.get("siu") or {}).get("linked") or []:  # the linked claims leave the review with it,
                o = self.repo.get_claim(other)                          # but a held payment is released on its own
                link = (o or {}).get("siu_linked") or {}
                if link.get("from") == claim_id:
                    self.repo.update_claim(other, siu_linked={**link, "review_closed": outcome} if link.get("hold_payment") else None)
            self.repo.update_claim(claim_id, siu=None)
            return self.repo.get_claim(claim_id)
        linked = sorted({f["detail"].split(" ")[0] for f in self._other_claim_flags(claim_id)})
        hold = {"by": reviewer, "at": now_iso(), "reason": reason.strip(), "linked": linked, "kind": kind or "document_reuse"}
        self.repo.update_claim(claim_id, siu=hold, pending_decision=None)
        for other in linked:  # the claim the documents came from is part of the same review
            if self.repo.get_claim(other):
                self.repo.update_claim(other, siu_linked={"from": claim_id, "at": hold["at"]})
        self.repo.add_action(claim_id, {"reviewer": reviewer, "type": "siu_referred", "note": None, "reason": reason})
        self.repo.audit(reviewer, "siu_referred", claim_id, after={"linked": linked}, reason=reason)
        return self.repo.get_claim(claim_id)

    def siu_hold_payment(self, claim_id, reviewer, reason):
        """A claim linked to an SIU review (its documents were reused elsewhere) keeps going unless a reviewer also
        holds its payment, with a reason: collusion is possible, but the original owner may be the victim."""
        if not (reason or "").strip():
            raise ValueError("reason_required")
        claim = self.repo.get_claim(claim_id)
        link = claim.get("siu_linked")
        if not link:
            raise ValueError("not_linked")
        self.repo.update_claim(claim_id, siu_linked={**link, "hold_payment": {"by": reviewer, "at": now_iso(), "reason": reason.strip()}})
        self.repo.audit(reviewer, "siu_payment_held", claim_id, reason=reason, after={"from": link.get("from")})
        return self.repo.get_claim(claim_id)

    def siu_release_payment(self, claim_id, reviewer, reason):
        """A linked claim's held payment goes ahead again: a separate act, with a reason, by someone else."""
        if not (reason or "").strip():
            raise ValueError("reason_required")
        claim = self.repo.get_claim(claim_id)
        link = claim.get("siu_linked") or {}
        if not link.get("hold_payment"):
            raise ValueError("not_held")
        if link["hold_payment"].get("by") == reviewer:
            raise SecondPersonRequired("the person who held the payment cannot release it")
        self.repo.update_claim(claim_id, siu_linked=None if link.get("review_closed") else {k: v for k, v in link.items() if k != "hold_payment"})
        self.repo.audit(reviewer, "siu_payment_released", claim_id, reason=reason)
        return self.repo.get_claim(claim_id)

    def _other_claim_flags(self, claim_id):
        claim = self.repo.get_claim(claim_id)
        docs = self.repo.documents(claim_id)
        return self.cross_claim_flags(claim, docs)

    def confirm_decision(self, claim_id, second, accept, note="", pid=None, override=""):
        """The second reviewer confirms (the decision goes out under both names) or returns it with a reason.
        pid: the pending decision the second reviewer looked at; if it changed meanwhile, nothing happens."""
        claim = self.repo.get_claim(claim_id)
        p = claim.get("pending_decision")
        if not p:
            raise ValueError("nothing_pending")
        if pid and p.get("pid") != pid:
            raise DecisionConflict("pending_changed")
        if p["by"] == second:
            raise SecondPersonRequired("the same person cannot confirm their own decision")
        if p.get("correction"):
            self._appeal_reviewer_ok(claim, second)
        if not accept:
            if not (note or "").strip():
                raise ValueError("reason_required")
            self.repo.update_claim(claim_id, pending_decision=None)
            self.repo.add_action(claim_id, {"reviewer": second, "type": "decision_returned", "note": note, "reason": None, "decision": p})
            self.repo.audit(second, "decision_returned", claim_id, after={"requested_by": p["by"], "note": note})
            return self.repo.get_claim(claim_id)
        if p.get("basis_check") and not (override or "").strip():  # confirming past the warning: say why, for the audit
            raise PermissionError("basis_override_needs_reason")
        self.repo.audit(second, "decision_confirmed", claim_id, after={"requested_by": p["by"], "type": p["type"], "amount_usd": p["amount_usd"]},
                        **({"override": override.strip()} if p.get("basis_check") else {}))
        return self.decide(claim_id, p["by"], "deny" if p["type"] == "deny" else "approve", p["amount_usd"], p["clauses"],
                           p["note"], p["reason"], confirmed_by=second, adjust=p.get("adjust"), correction=bool(p.get("correction")))

    def reviewer_action(self, claim_id, reviewer, action, note="", reason="", decision=None, checks=None, template=None):
        """note: goes to the traveler (approve / request_info / message) or stays internal (note).
        reason: why the reviewer approved despite open checks, or how the caller was identified; audit trail only,
        never sent to the traveler."""
        if action in self.SUPPORT:
            return self.support_action(claim_id, reviewer, action, note, reason, checks, template)
        if action in ("approve", "deny") and decision is None:  # no side door around amounts and four eyes
            return self.decide(claim_id, reviewer, action, note=note, reason=reason)
        open_checks = self.unresolved(claim_id) if action == "approve" else []
        if open_checks and not (reason or "").strip():
            raise PermissionError("approving with open checks needs a reason")
        claim = self.repo.get_claim(claim_id)
        traveler = self.repo.get_traveler(claim["traveler_id"])
        before = claim["status"]
        if action == "request_info":
            self.check_wording(note, reviewer, claim_id, "request_info")
        status = {"approve": "approved", "deny": "denied", "request_info": "info_requested", "note": before}[action]
        self.repo.update_claim(claim_id, status=status)
        self.repo.add_action(claim_id, {"reviewer": reviewer, "type": action, "note": note, "reason": reason or None,
                                        "open_checks": open_checks or None, "decision": decision})
        self.repo.audit(reviewer, f"reviewer_{action}", claim_id, before=before, after=status,
                        **({"reason": reason, "open_checks": open_checks} if open_checks else {}),
                        **({"decision": decision} if decision else {}))
        if action in ("approve", "deny", "request_info"):
            lc = loc(traveler)
            label = t(lc, "status_partial" if (decision or {}).get("type") == "partial"
                      else {"approve": "status_approved", "deny": "status_denied", "request_info": "status_info_requested"}[action])
            params = [first_name(traveler), claim_id, label, note or t(lc, "no_note")]  # template params cannot be empty
            text = render_template("claim_status_update", params, lc)
            self.router.send(traveler, claim_id, "decision" if action in ("approve", "deny") else "status", text,
                             template=("claim_status_update", params), email_subject=t(lc, "subject_update", claim=claim_id))
            # in the chat the decision reads as its blocks; the claim number goes to the end, out of the way
            self._mirror_web(traveler, claim_id, f"{note}\n\n{t(lc, 'ref_no', claim=claim_id)}" if decision else text)
        return self.repo.get_claim(claim_id)

    STAFF_TEMPLATES = ("why_less", "pay_date")

    def staff_template(self, claim_id, key):
        """The decision told by the contact centre, filled here from the claim (never from what the browser sent):
        the only way staff words may state a decision."""
        claim = self.repo.get_claim(claim_id)
        lc = loc(self.repo.get_traveler(claim["traveler_id"]))
        d, ap, due = claim.get("decision") or {}, claim.get("appeal") or {}, self.due(claim)
        if key == "why_less" and d and d.get("type") != "full" and not claim.get("pending_decision"):
            lines = "".join(t(lc, "tpl_line", merchant=x.get("merchant") or "", frm=f"{x['from']:.2f}", to=f"{x['to']:.2f}",
                              why=t(lc, "adj_" + x["reason"])) for x in d.get("lines") or [])
            return t(lc, "tpl_why_less", result=t(lc, "tpl_result_" + d["type"], amount=f"{d.get('amount_usd', 0):.2f}"), lines=lines,
                     clauses=", ".join(f"{c} {rag.clause_name(c, lc)}".strip() for c in d.get("clauses") or []) or "-")
        if key == "pay_date" and due and due["kind"] == "pay":
            date = i18n.due_date(due["date"], lc)
            if ap and not ap.get("resolved"):
                return t(lc, "tpl_pay_date_appeal", reply=i18n.due_date(ap.get("reply_by") or "", lc), date=date)
            return t(lc, "tpl_pay_date", date=date)
        raise ValueError("template_not_applicable")

    def support_action(self, claim_id, staff, action, note="", reason="", checks=None, template=None):
        if action in ("resend_link", "bank_correction"):
            reason = self.identity(action, checks, reason)
        if action == "message" and template:  # a decision template: the server writes it, whatever the browser sent
            if template not in self.STAFF_TEMPLATES:
                raise ValueError("unknown_template")
            note = self.staff_template(claim_id, template)
        if action == "message" and not (note or "").strip():
            raise ValueError("empty message")
        if action == "message":  # staff words follow the same rule as the agent's; a decision only through a template
            self.check_wording(note, staff, claim_id, "message", template=bool(template))
        claim = self.repo.get_claim(claim_id)
        traveler = self.repo.get_traveler(claim["traveler_id"])
        lc = loc(traveler)
        if action == "message":
            text = note.strip()
        elif action == "resend_link":
            text = t(lc, "resend_link", link=self.chat_link(claim_id))
        else:  # the old account is void until the traveler enters a new one on a fresh secure page
            changed = traveler.get("contact_changed_at")
            if changed and self.clock() - datetime.fromisoformat(changed) < timedelta(hours=24):
                # a new phone + a new bank account in one day is how payouts get hijacked: wait, or call the old number
                raise PermissionError("recent_contact_change")
            form = claim.get("claim_form") or {}
            self.repo.update_claim(claim_id, claim_form={**form, "bank_details_provided": False, "account_last4": None, "bank": None,
                                                         "bank_reset": True, "previous": {k: form.get(k) for k in ("bank", "account_last4")}})
            text = t(lc, "bank_correction", link=self.secure_link(claim_id))
        params = [first_name(traveler), claim_id, t(lc, "status_" + claim["status"]) if f"status_{claim['status']}" in i18n.STRINGS[lc]
                  else t(lc, "status_reviewing"), text]
        self.router.send(traveler, claim_id, "status", text, template=("claim_status_update", params),
                         email_subject=t(lc, "subject_update", claim=claim_id))
        self._mirror_web(traveler, claim_id, text)
        self.repo.add_action(claim_id, {"reviewer": staff, "type": action,
                                        "note": note if action == "message" else None, "reason": reason or None})
        self.repo.audit(staff, f"support_{action}", claim_id, **({"identity_check": reason} if reason else {}))
        return self.repo.get_claim(claim_id)

    # Desk work that tells the traveler nothing: who owns the claim, hand-offs, undoing a bank re-entry, contact changes.
    STAFF_ONLY = {"assign", "handoff", "bank_correction_cancel", "contact_update", "call_log", "handoff_done"}
    CALL_TOPICS = ("smishing", "pay_date", "why_less", "objection", "bank", "contact", "documents", "other")
    CALL_RESULTS = ("answered", "handed_off", "call_back")

    def staff_directory(self):
        """Colleagues a claim can be handed to: STAFF_DIRECTORY, else everyone who acted in the last week's audit trail."""
        if self.s.staff_directory:
            return sorted({x.strip() for x in self.s.staff_directory.split(",") if x.strip()})
        seen = set()
        for i in range(7):
            for a in self.repo.audits((self.clock() - timedelta(days=i)).date().isoformat()):
                if "@" in (a.get("actor") or ""):
                    seen.add(a["actor"])
        return sorted(seen)

    TEAMS = {"team:reviewers", "team:agents"}  # a hand-off to a team waits until someone in it takes the claim

    CALL_WINDOWS = {"any": (9, 18), "am": (9, 12), "pm": (13, 18)}

    @classmethod
    def callback_slot(cls, pref, offset_min, now=None):
        """First half hour inside the traveler's window in *their* time that is also contact-centre hours
        (Mon–Fri 09–18 KST). No overlap within a week (e.g. the Americas): the start of their window, flagged off-hours."""
        a, b = cls.CALL_WINDOWS.get(pref, cls.CALL_WINDOWS["any"])
        now = now or datetime.now(timezone.utc)
        t0 = now.replace(second=0, microsecond=0) + timedelta(minutes=30 - now.minute % 30)
        local = lambda x: x + timedelta(minutes=offset_min)
        kst = lambda x: x + timedelta(hours=9)
        slots = [t0 + timedelta(minutes=30 * i) for i in range(7 * 48)]
        mine = [x for x in slots if a <= local(x).hour < b]
        both = [x for x in mine if kst(x).weekday() < 5 and 9 <= kst(x).hour < 18]
        return (both or mine or [t0])[0], bool(both)

    def request_callback(self, claim_id, text=None, pref=None, offset_min=None):
        """The traveler asks us to call (abroad, calling Korea costs): it joins the staff call-back queue,
        at the first slot in their preferred window (their local time) that the contact centre is open."""
        claim = self.repo.get_claim(claim_id)
        if claim.get("callback"):
            return claim
        traveler = self.repo.get_traveler(claim["traveler_id"])
        pref = pref if pref in self.CALL_WINDOWS else "any"
        try:
            offset = max(-720, min(840, int(offset_min)))
        except (TypeError, ValueError):
            offset = 540  # no browser offset: Korea time
        due, in_hours = self.callback_slot(pref, offset)
        # what the call is probably about, from where the claim stands, so the agent can prepare (marked as a guess)
        d, ap = claim.get("decision") or {}, claim.get("appeal") or {}
        topic = ("objection" if ap and not ap.get("resolved") else "why_less" if d.get("type") in ("partial", "deny")
                 else "pay_date" if d or claim.get("status") == "submitted" else "documents")
        self.repo.update_claim(claim_id, callback={"due": due.isoformat(timespec="seconds").replace("+00:00", "Z"), "by": "traveler", "topic": topic, "topic_guess": True,
                                                   "note": ((text or "").strip()[:200] or None), "requested": True, "at": now_iso(),
                                                   "pref": pref, "tz_offset": offset, "in_hours": in_hours})
        lc = loc(traveler)
        digits = re.sub(r"\D", "", traveler.get("phone") or "")
        on = t(lc, "callback_on", last4=digits[-4:]) if digits else ""  # the number we will call, masked; none on file: just "we call you"
        lt = due + timedelta(minutes=offset)
        when = f"{i18n.pretty_date(lt.date().isoformat(), lc)} {lt:%H:%M}"
        self._mirror_web(traveler, claim_id, t(lc, "callback_slot", on=on, when=when))
        self.repo.add_action(claim_id, {"reviewer": "traveler", "type": "callback_requested", "note": (text or None), "reason": None})
        self.repo.audit("traveler", "callback_requested", claim_id, after={k: (self.repo.get_claim(claim_id).get("callback") or {}).get(k)
                                                                          for k in ("pref", "tz_offset", "due", "in_hours")})
        return self.repo.get_claim(claim_id)

    def change_callback(self, claim_id, pref=None, offset_min=None):
        """The traveler cannot make the time staff set: they say which window suits them (their own time); staff
        see the request on the card and set a new time. Nothing is moved automatically."""
        claim = self.repo.get_claim(claim_id)
        cb = claim.get("callback") or {}
        if not cb or cb.get("requested"):
            return claim  # their own request: cancel it and ask again instead
        try:
            offset = max(-720, min(840, int(offset_min)))
        except (TypeError, ValueError):
            offset = 540
        change = {"pref": pref if pref in self.CALL_WINDOWS else "any", "tz_offset": offset, "at": now_iso()}
        self.repo.update_claim(claim_id, callback={**cb, "change_request": change})
        traveler = self.repo.get_traveler(claim["traveler_id"])
        self._mirror_web(traveler, claim_id, t(loc(traveler), "callback_change_requested"))
        self.repo.add_action(claim_id, {"reviewer": "traveler", "type": "callback_change_requested", "note": None, "reason": None})
        self.repo.audit("traveler", "callback_change_requested", claim_id, after=change)
        return self.repo.get_claim(claim_id)

    def cancel_callback(self, claim_id):
        """The traveler takes their call request back; a call-back staff promised stays (only staff close that)."""
        claim = self.repo.get_claim(claim_id)
        cb = claim.get("callback") or {}
        if not cb.get("requested"):
            return claim
        self.repo.update_claim(claim_id, callback=None)
        traveler = self.repo.get_traveler(claim["traveler_id"])
        self._mirror_web(traveler, claim_id, t(loc(traveler), "callback_cancelled"))
        self.repo.add_action(claim_id, {"reviewer": "traveler", "type": "callback_cancelled", "note": None, "reason": None})
        self.repo.audit("traveler", "callback_cancelled", claim_id, before={k: cb.get(k) for k in ("pref", "tz_offset", "due")})
        return self.repo.get_claim(claim_id)

    def staff_action(self, claim_id, staff, action, to=None, note="", due=None, reason="", phone=None, email=None, checks=None,
                     callback_at=None, callback_done=None, callback_pref=None):
        claim = self.repo.get_claim(claim_id)
        if action == "assign":  # take it, or give it to a named colleague
            owner = to or staff
            h = claim.get("handoff") or {}
            # taken from a team queue: what was asked and by when stays on the claim until "done"
            keep = {**h, "taken_by": owner, "taken_at": now_iso()} if (h.get("to") or "").startswith("team:") else None
            self.repo.update_claim(claim_id, assignee=owner, handoff=keep)
            detail = {"assignee": owner, **({"from_queue": h.get("to")} if keep else {})}
        elif action == "handoff":  # pass it on with what to do and by when; it shows in the colleague's queue
            if not to or not (note or "").strip():
                raise ValueError("handoff_needs_to_and_note")
            known = self.staff_directory()
            if known and to not in known and to not in self.TEAMS:
                raise ValueError("unknown_colleague")  # a typo must not send the claim to nobody
            self.repo.update_claim(claim_id, assignee=to, handoff={"from": staff, "to": to, "note": note.strip(), "due": due, "at": now_iso()})
            detail = {"to": to, "due": due}
        elif action == "handoff_done":  # the colleague finished what was asked
            h = claim.get("handoff")
            if not h:
                raise ValueError("no_handoff")
            self.repo.update_claim(claim_id, handoff=None)
            detail = {"from": h.get("from"), "done_by": staff}
        elif action == "call_log":  # one line per phone call: topic, outcome, short note
            if to not in self.CALL_TOPICS:
                raise ValueError("call_topic")  # a default topic saved unread skews the statistics
            result = due if due in self.CALL_RESULTS else "answered"
            detail = {"topic": to, "result": result}
            if result == "call_back":  # a promise to call: it waits in a queue with a time and a name, not in someone's memory
                if not callback_at:
                    raise ValueError("callback_needs_time")
                try:
                    when = datetime.fromisoformat(callback_at.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError("callback_needs_time")
                when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
                if when < self.clock() - timedelta(minutes=5):
                    raise ValueError("callback_past")
                self.repo.update_claim(claim_id, callback={"due": callback_at, "by": staff, "topic": to, "note": (note or "").strip(),
                                                           "pref": callback_pref if callback_pref in ("any", "am", "pm") else "any",
                                                           "at": now_iso()})
                detail["callback_at"] = callback_at
            elif claim.get("callback") and callback_done:  # the agent says this call kept the promise; another call leaves it
                self.repo.update_claim(claim_id, callback=None)
                detail["callback_done"] = claim["callback"].get("due")
        elif action == "bank_correction_cancel":  # pressed by mistake: the old account stands again until a new one arrives
            form = claim.get("claim_form") or {}
            if not form.get("bank_reset"):
                raise ValueError("nothing_to_cancel")
            prev = form.get("previous") or {}
            self.repo.update_claim(claim_id, claim_form={**{k: v for k, v in form.items() if k not in ("bank_reset", "previous")},
                                                         "bank": prev.get("bank"), "account_last4": prev.get("account_last4"),
                                                         "bank_details_provided": True})
            detail = {"restored": prev}
        else:  # contact_update: a new phone or email, only after the caller's identity was checked
            reason = self.identity(action, checks, reason)
            if not (phone or email):
                raise ValueError("phone_or_email")
            if phone and not re.fullmatch(r"\+?[0-9][0-9 -]{6,18}", phone):
                raise ValueError("phone")
            if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise ValueError("email")
            updates = {**({"phone": re.sub(r"[ -]", "", phone)} if phone else {}), **({"email": email} if email else {}),
                       "unreachable": {}, "contact_changed_at": self.clock().isoformat(timespec="seconds")}
            old = self.repo.get_traveler(claim["traveler_id"])
            lc = loc(old)
            # tell the old phone/email first: if it was not them, they call us before anything else moves
            notice = t(lc, "contact_changed", phone=self.s.insurer(lc)["phone"])
            self.router.send(old, claim_id, "status", notice, template=("claim_status_update", [
                first_name(old), claim_id, t(lc, "status_reviewing"), notice]), email_subject=t(lc, "subject_update", claim=claim_id))
            self.repo.update_traveler(claim["traveler_id"], **updates)
            detail = {"phone": ("•••" + re.sub(r"\D", "", phone)[-4:]) if phone else None, "email": bool(email)}  # masked in the log
        self.repo.add_action(claim_id, {"reviewer": staff, "type": action, "note": note or None, "reason": reason or None, "detail": detail})
        # how the caller was identified (contact change) is not the same thing as why a claim changed hands
        self.repo.audit(staff, f"staff_{action}", claim_id, after=detail,
                        **({("identity_check" if action == "contact_update" else "takeover_reason"): reason} if reason else {}))
        return self.repo.get_claim(claim_id)

    def handle_status(self, st):
        claim_id, msg = self.repo.message_by_provider_id(st.provider_id)
        if not msg:
            return None
        self.repo.update_message(claim_id, msg["_sk"], status=st.status, status_detail=st.detail)
        if st.status == "failed":
            print(f"SMS_FAILED {msg['channel']} {claim_id}")  # delivery failures: an alarm when many fail at once
        if st.status != "failed" or msg.get("fallback_sent") or msg.get("to") == "reviewer":
            return None
        traveler = self.repo.get_traveler(self.repo.get_claim(claim_id)["traveler_id"])
        self.router.mark_unreachable(traveler, msg["channel"])
        self.repo.update_message(claim_id, msg["_sk"], fallback_sent=True)
        return self.router.send(traveler, claim_id, msg.get("purpose", "reply"), msg["text"], exclude=[msg["channel"]],
                                note=f"{msg['channel']} reported undelivered ({st.detail or st.status})")

    def _reminder_due(self, force=False):
        now = self.clock()
        for claim in self.repo.list_claims():
            if claim["status"] not in ("alerted", "collecting") or claim.get("reminders_sent", 0) >= self.s.max_reminders:
                continue
            last = claim.get("last_reminder_at")
            if not force and last and now - datetime.fromisoformat(last) < timedelta(hours=24):
                continue
            state = checklist.compute(self.repo.documents(claim["id"]), claim.get("secure_submitted"))
            if checklist.missing(state):
                yield claim

    def escalate_overdue_appeals(self):
        """An objection past its reply-by date goes to the reviewers' queue (once) and raises the ops alarm."""
        today, out = self.clock().date().isoformat(), []
        for c in self.repo.list_claims():
            a = c.get("appeal") or {}
            if not a or a.get("resolved") or (a.get("reply_by") or "9") >= today:
                continue
            if not a.get("escalated"):  # the alarm fires once, when it goes to the queue; the KPI shows what is still overdue
                print(f"APPEAL_OVERDUE {c['id']} reply_by={a.get('reply_by')}")
                self.repo.update_claim(c["id"], appeal={**a, "escalated": now_iso()}, assignee="team:reviewers",
                                       handoff={"from": "system", "to": "team:reviewers", "note": f"이의 답변 기한({a.get('reply_by')}) 초과",
                                                "due": None, "at": now_iso()})
                self.repo.audit("system", "appeal_escalated", c["id"], after={"reply_by": a.get("reply_by")})
            out.append(c["id"])
        return out

    def run_reminders(self, force=False):
        self.escalate_overdue_appeals()  # the scheduled run also watches objection deadlines
        sent = []
        now = self.clock()
        for claim in list(self._reminder_due(force)):
            _, _, state = self.recheck(claim["id"])
            missing = checklist.missing(state)
            if not missing:
                continue
            traveler = self.repo.get_traveler(claim["traveler_id"])
            lc = loc(traveler)
            short = ", ".join(i18n.label(lc, m, short=True) for m in missing[:2])
            params = [first_name(traveler), claim.get("flight"), short, self.s.claims_inbox]
            self.router.send(traveler, claim["id"], "reminder", self._reminder_text(traveler, claim, self.chat_link(claim["id"])),
                             template=("document_reminder", params))
            self.repo.update_claim(claim["id"], reminders_sent=claim.get("reminders_sent", 0) + 1,
                                   last_reminder_at=now.isoformat(timespec="seconds"))
            sent.append(claim["id"])
        return sent

    # ---------- operations KPIs ----------

    # Wording the agent must never use with travelers (promises, odds); any hit is listed for a person to read.
    BANNED = WordingRule(banned_wording())

    def kpi(self, days=7):
        """Throughput, speed, document quality, AI quality and messaging cost per disruption, from stored data only."""
        def hours(a, b):
            try:
                return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600
            except (TypeError, ValueError):
                return None
        avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
        events, banned, totals = {}, [], {"replies": 0, "cited": 0, "sms": 0, "sms_parts": 0, "docs": 0, "retake": 0,
                                         "decided": 0, "changed": 0, "support": 0, "appeals_open": 0, "appeals_overdue": 0,
                                         "appeals": 0, "corrections": 0, "pending": 0, "pending_h": [], "callbacks": 0}
        today = datetime.fromisoformat(now_iso()).date()  # same clock as the stored timestamps
        n_days = 30 if int(days or 7) > 7 else 7
        days = [(today - timedelta(days=i)).isoformat() for i in range(n_days - 1, -1, -1)]
        series = {k: {d: 0 for d in days} for k in ("created", "submitted", "decided")}  # last 7 or 30 days, for trend lines
        # wording stopped before it went out: staff attempts (by person) and agent sentences taken out
        blocked = {"staff": 0, "agent": 0, "by_person": {}, "items": [], "regex": 0, "guardrail": 0, "regex_only": 0}
        for d in days:
            for a in self.repo.audits(d):
                if a["action"] == "guardrail_skipped":  # guardrail down: the regex alone judged this text
                    blocked["regex_only"] += 1
                    continue
                if a["action"] == "wording_blocked":
                    blocked["staff"] += 1
                    blocked["by_person"][a["actor"]] = blocked["by_person"].get(a["actor"], 0) + 1
                elif a["action"] == "agent_wording_blocked":
                    blocked["agent"] += 1
                else:
                    continue
                after = a.get("after") or {}
                phrases = [after.get("phrase")] if after.get("phrase") else (after.get("phrases") or [])
                blocked["guardrail" if any(str(p).startswith("guardrail:") for p in phrases) else "regex"] += 1
                blocked["items"].append({"at": a["at"], "actor": a["actor"], "claim": a.get("target"), "where": after.get("where", "ai_reply"),
                                         "phrase": after.get("phrase") or ", ".join(after.get("phrases") or [])})
        blocked["items"] = blocked["items"][-50:]
        fallbacks = sum(1 for d in days for a in self.repo.audits(d) if a["action"] == "bedrock_fallback")
        for c in self.repo.list_claims():
            # backlog right now (open objections, waiting second reviews, call-backs): every claim, whatever its age
            a = c.get("appeal") or {}
            if a and not a.get("resolved"):
                totals["appeals_open"] += 1
                totals["appeals_overdue"] += (a.get("reply_by") or "9") < today.isoformat()
            if c.get("pending_decision"):
                totals["pending"] += 1
                totals["pending_h"].append(hours(c["pending_decision"].get("at"), now_iso()))  # same clock as "at"
            totals["callbacks"] += bool(c.get("callback"))
            # everything else covers the same window: claims opened in the chosen period
            if (c.get("created_at") or "")[:10] < days[0]:
                continue
            key = f"{c.get('flight')} {c.get('flight_date')}"
            e = events.setdefault(key, {"event": key, "claims": 0, "opened": 0, "submitted": 0, "approved": 0, "denied": 0, "full": 0, "partial": 0,
                                        "open_checks": 0, "to_submit_h": [], "to_decide_h": [], "by_status": {}})
            e["claims"] += 1
            for k, at in (("created", c.get("created_at")), ("submitted", c.get("submitted_at")), ("decided", (c.get("decision") or {}).get("at"))):
                if at and at[:10] in series[k]:
                    series[k][at[:10]] += 1
            totals["appeals"] += bool(a)
            totals["corrections"] += bool((c.get("decision") or {}).get("corrects"))
            e["by_status"][c["status"]] = e["by_status"].get(c["status"], 0) + 1
            msgs = self.repo.messages(c["id"])
            if any(m["direction"] == "in" for m in msgs):
                e["opened"] += 1
            if c.get("submitted_at"):
                e["submitted"] += 1
                e["to_submit_h"].append(hours(c.get("created_at"), c["submitted_at"]))
            dec = c.get("decision")
            if dec:
                e["approved" if dec["type"] != "deny" else "denied"] += 1
                if dec["type"] in ("full", "partial"):  # a part payment is not counted as "approved in full"
                    e[dec["type"]] += 1
                e["to_decide_h"].append(hours(c.get("submitted_at"), dec.get("at")))
                totals["decided"] += 1
                # paid less than the automatic estimate, or denied (not "the AI was overruled")
                totals["changed"] += dec["type"] != "full"
            if self.unresolved(c["id"]):
                e["open_checks"] += 1
            for m in msgs:
                if m["direction"] != "out":
                    continue
                if m.get("channel") in ("sms", "rcs"):
                    totals["sms"] += 1
                    totals["sms_parts"] += sms_parts(m.get("text") or "")
                if m.get("purpose") == "reply" and m.get("channel") == "web":
                    totals["replies"] += 1
                    totals["cited"] += bool((m.get("ui") or {}).get("sources"))
                hit = self.BANNED.search(m.get("text") or "") if m.get("purpose") in ("reply", "mirror", "status", "decision") else None
                if hit:
                    banned.append({"claim": c["id"], "at": m.get("at"), "phrase": hit.group(0), "text": (m.get("text") or "")[:160]})
            for d in self.repo.documents(c["id"]):
                totals["docs"] += 1
                totals["retake"] += any(f["code"] == "retake" for f in d.get("flags", []))
            totals["support"] += sum(1 for a in self.repo.actions(c["id"]) if a.get("type") in self.SUPPORT | self.STAFF_ONLY)
        rows = []
        for e in events.values():
            e["to_submit_h"], e["to_decide_h"] = avg([x for x in e["to_submit_h"] if x is not None]), avg([x for x in e["to_decide_h"] if x is not None])
            rows.append(e)
        pct = lambda a, b: round(100 * a / b) if b else None
        return {"events": sorted(rows, key=lambda r: r["event"]),
                "quality": {"cited_pct": pct(totals["cited"], totals["replies"]), "replies": totals["replies"],
                            "retake_pct": pct(totals["retake"], totals["docs"]), "docs": totals["docs"],
                            "changed_pct": pct(totals["changed"], totals["decided"]), "decided": totals["decided"],
                            "agent_errors": sum(1 for d in days for a in self.repo.audits(d) if a["action"] == "agent_error")},  # in the period
                "control": {"callbacks": totals["callbacks"], "appeals": totals["appeals"], "appeals_open": totals["appeals_open"], "appeals_overdue": totals["appeals_overdue"],
                            "corrections": totals["corrections"], "pending": totals["pending"],
                            "pending_avg_h": avg([x for x in totals["pending_h"] if x is not None])},
                "series": {k: [v[d] for d in days] for k, v in series.items()}, "days": days,
                "messaging": {"sms": totals["sms"], "sms_parts": totals["sms_parts"], "cost_usd": round(totals["sms_parts"] * self.s.sms_price_usd, 2),
                              "support_actions": totals["support"]},
                "banned": banned, "blocked": blocked, "fallbacks": fallbacks,
                "processing": {"region": os.environ.get("AWS_REGION", "local"), "model": self.s.model_id,
                               "fallback": self.s.fallback_model_id or None},
                "period": {"from": days[0], "to": days[-1], "days": n_days, "basis": "flow figures: claims created in the period; backlog (open objections, second reviews, call-backs): all claims"}}

    def _twin(self, claim, doc):
        """For a document flagged as another claim's: that claim's copy (same photo), so a reviewer can compare them."""
        if not doc.get("sha256") or not any(f["code"] == "other_claim_document" for f in doc.get("flags", [])):
            return None
        for c in self.repo.list_claims():
            if c["id"] != claim["id"] and c.get("flight") == claim.get("flight"):
                for d in self.repo.documents(c["id"]):
                    if d.get("sha256") == doc["sha256"]:
                        return {"claim": c["id"], "doc": d["id"]}
        return None

    def document_image(self, claim_id, doc_id):
        d = next((x for x in self.repo.documents(claim_id) if x["id"] == doc_id), None)
        if not d or not d.get("s3_key"):
            return None, None
        return self.docs.get(d["s3_key"]), d.get("mime") or "image/jpeg"

    def claim_view(self, claim_id):
        claim = self.repo.get_claim(claim_id)
        traveler = self.repo.get_traveler(claim["traveler_id"])
        docs = self.repo.documents(claim_id)
        return {"claim": {k: v for k, v in claim.items() if k != "packet_html"}, "traveler": traveler,
                "event": self.repo.get_event(claim["event_id"]) if claim.get("event_id") else None,
                "documents": [{**d, "view": self._doc_view(d), "twin": self._twin(claim, d)} for d in docs],
                "checklist": checklist.compute(docs, claim.get("secure_submitted")), "unresolved": self.unresolved(claim_id),
                "estimate": estimate(docs, self._policy(traveler["id"]).get("plan") or claim.get("plan")),
                "messages": self.repo.messages(claim_id), "actions": self.repo.actions(claim_id),
                "due": self.due(claim),  # the same expected date the traveler sees
                "sender": {"sms": self.s.sms_origination or None},
                "insurer": self.s.insurer(loc(traveler)),  # "is this text really from you?": the number it came from
                # clauses a partial payment or denial can rest on, with names, for the decision form
                # (names in the review language; "names" in both, for a desk switched to the other language)
                "decision_clauses": [{"id": c, "name": rag.clause_name(c, self.s.review_locale),
                                      "names": {lg: rag.clause_name(c, lg) for lg in ("en", "ko")}}
                                     for c in policy_clauses(self.s.review_locale) if c.split("-")[0] in ("PLAN", "TD", "EX")],
                # readable names for the clauses the assessment cites, in the reviewer's language
                "clause_names": {c: rag.clause_name(c, self.s.review_locale)
                                 for c in (claim.get("coverage") or {}).get("cited_clauses", [])},
                "clause_names_by_lang": {lg: {c: rag.clause_name(c, lg) for c in (claim.get("coverage") or {}).get("cited_clauses", [])}
                                         for lg in ("en", "ko")}}
