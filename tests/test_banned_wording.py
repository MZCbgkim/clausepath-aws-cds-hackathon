"""The compliance list (prompts/banned_wording.txt) against examples: promises are caught, careful wording passes."""
import pytest

from tripclaim.service import Service

PROMISES = [
    "호텔비는 보상돼요.", "보상됩니다.", "보상 가능합니다", "보상받으실 수 있어요", "보상받을 수 있어요",
    "10/14에 꼭 입금돼요", "지급될 거예요", "지급해 드릴게요", "반드시 지급됩니다", "100% 보상",
    "무조건 나와요", "걱정 마세요, 다 나와요", "보상될 가능성이 높아요", "틀림없이 들어가요",
    "You will get $150 back.", "You will be reimbursed.", "It will be paid on Monday.", "Your hotel is covered.",
    "You're covered.", "We guarantee it.", "You'll definitely be paid.", "Don't worry about it.",
    # round 9: dates promised for the money, polite variants, contractions
    "10/14에 들어와요", "14일에 들어올 거예요", "10/14(수)에 들어와요", "내일 지급해요", "다음 주에 입금돼요",
    "입금해 드릴게요", "입금될 예정이에요", "보상해 드려요", "지급해 드려요", "지급해요",
    "보험금을 받으실 수 있어요", "보상 대상입니다", "You'll get $150.", "You'll be paid on Monday.",
    # round 10: more polite variants, vague dates, English
    "지급받으실 수 있어요", "환급받으실 수 있어요", "돌려받으실 수 있어요", "$100을 받으실 수 있어요", "보험금으로 받으실 수 있어요",
    "전액 나와요", "보험금은 10/14에 나와요", "지급될 예정이에요", "보상될 예정이에요", "승인될 거예요",
    "곧 들어와요", "금요일쯤 들어와요", "14일쯤 들어올 거예요", "다음 주쯤 지급돼요",
    "You will be compensated.", "We will pay you.", "You'll be refunded.", "It's covered.",
    # round 11: general forms instead of one phrase at a time
    "환급돼요", "환불돼요", "보상받으실 거예요", "보상이 됩니다", "지급이 될 거예요", "100달러 나올 거예요", "보상받으세요",
    "The hotel is covered.", "The insurer will pay $100 on 10/14.", "You are eligible for compensation.",
    # round 12: spacing, other verbs, amounts, English approvals
    "보상 받으실 거예요", "환급 받으실 거예요", "지급 받으세요", "100달러 받으실 거예요", "보험금 드릴게요", "송금해 드릴게요",
    "$100 바로 드릴게요", "Your claim will be approved.", "This expense is covered.", "You qualify for compensation.", "You're entitled to a refund.",
    # round 13: polite future tense, run-together spelling, confirmed/possible/guaranteed, percentages
    "지급해 드리겠습니다", "보상해 드리겠습니다", "입금 해드릴게요", "보상받으실수있어요", "지급 확정이에요", "100% 나옵니다",
    "지급 가능해요", "보장돼요", "Your claim is approved.",
    # round 14: a promise after a negated guarantee, "처리해 드리겠습니다", settled past forms, contractions
    "We cannot guarantee it, but you will be paid.", "I can't guarantee anything, you'll be paid for sure.",
    "보상 드리겠습니다", "지급 처리해 드릴게요", "입금 처리해 드리겠습니다", "보상이 확정됐습니다", "지급 대상입니다",
    "지급 승인됐습니다", "승인 났어요", "걱정하지 마세요", "보장된 금액입니다", "Your claim's been approved", "Good news: it's approved.",
    # round 15: other joints after a negated guarantee; money going out, certainty, approval declared, receiving
    "I can't guarantee timing and you will be paid in full.", "We can't guarantee the date — you will be paid anyway.",
    "We do not guarantee anything other than that you will receive $500",
    "보험금 나갑니다", "보상 나올 거예요", "보상 확실합니다", "승인 완료됐어요", "지급 결정 났어요", "심사 통과했어요",
    "보험금이 곧 송금됩니다", "보상금 수령 가능합니다", "보험금 수령하실 수 있어요", "전액 돌려드릴게요", "100달러 받으세요",
    "돈은 들어올 테니 안심하세요", "이번 건은 승인입니다",
    "We've approved your claim", "Payment is approved", "Your payout is $122.53", "The insurer has agreed to pay",
    # round 16
    "Not guaranteed (but you will be paid).", "Payment is certain.", "확답은 못 드리지만 나올 거예요.",
    "If you upload it today, the money is yours by Friday.", "Your payout is 150 dollars",
    "If you upload the receipt tonight, you'll have the money on Monday.", "영수증만 내시면 이번 주에 바로 받으세요.", "보상은 따놓은 당상이에요.",
]
CAREFUL = [
    "약관상 오늘 밤 호텔비는 보상하는 비용에 해당해요. 금액은 보험사가 정해요.",
    "추가 확인이 없으면 10/14(수) 지급 예정이에요(보험사 심사에 따라 달라질 수 있어요).",
    "미니바 금액은 보상하지 않아요.", "보험사가 $100.00 지급을 승인했어요.",
    "Under the policy, tonight's hotel counts as a covered expense; the insurer decides the amount.",
    "If nothing needs checking, the insurer expects to pay within 3 business days.",
    "This is definitely not covered under your plan.", "You'll get a text when the insurer decides.",
    "결정 통지서는 문자로 받으실 수 있어요.", "내일 지급 여부를 알려 드릴게요.", "링크로 들어와서 사진을 올려 주세요.",
    "Meals are covered up to $50 per day under TD-3.", "Here is what is covered:", "It is not likely covered under your plan.",
    "Definitely send the receipt.", "추가 확인이 없으면 10/14(수) 지급 예정이에요.", "결정 통지서가 곧 나와요.",
    "I cannot guarantee anything.", "Payment is not guaranteed.", "The hotel is covered up to $150 a day under TD-3.",
    "지급되지 않는 비용이에요.", "보상되는 경우는 약관 TD-3에 있어요.", "환급 여부는 보험사가 정해요.", "10월 6일 사진이 나와요.",
    "There is no guarantee.", "No guarantee of payment.", "Whether the hotel is covered depends on the insurer.",
    "고객님이 '항공사에서 환불돼요?'라고 물으셨죠.", "영수증에 금액이 나와요.", "서류는 제가 확인해 드릴게요.", "If this expense is covered, the insurer tells you.",
    "고객님이 '보상돼요?'라고 물으셨죠.", "We cannot guarantee it will be covered.", "보상 가능 여부는 보험사가 정해요.",
    "약관상 보상하는 비용에 해당해요.", "지급되지 않는 비용이에요.",
    # round 14
    "Whether your claim is approved is up to the insurer.", "We cannot guarantee payment; the insurer decides.",
    "보상 드릴 수 있는지는 보험사가 정해요.", "지급 대상인지 확인 중이에요.", "보험사가 $100.00 지급을 승인했어요.",
    "If it's approved, you get a text.",
    # round 15
    "We cannot guarantee it will be covered.", "보험금 수령 방법은 보안 페이지에 있어요.", "승인 여부는 보험사가 정해요.",
    "결정이 나면 문자로 알려 드려요.", "If payment is approved, it arrives in 2–5 business days.",
    # round 16
    "Payment is not guaranteed; the insurer decides.", "확답은 못 드리지만 보험사가 서류를 보고 정해요.", "The money is sent to the account you registered.",
]


@pytest.mark.parametrize("text", PROMISES)
def test_promises_are_caught(text):
    assert Service.BANNED.search(text), text


@pytest.mark.parametrize("text", CAREFUL)
def test_careful_wording_passes(text):
    assert not Service.BANNED.search(text), Service.BANNED.search(text).group(0)


def test_agent_reply_is_checked_before_sending(env):
    """A sentence that promises an outcome is taken out of the agent's reply; compliance sees what was removed."""
    svc = env["svc"]
    claim_id = "clm-x"
    out = svc.guard_reply(claim_id, "Your hotel is likely covered [1]. Keep the receipt.\n10/14에 들어와요.", "en")
    assert "likely covered" not in out and "들어와요" not in out and "Keep the receipt." in out
    assert not Service.BANNED.search(out)
    assert svc.guard_reply(claim_id, "Keep the receipt.", "en") == "Keep the receipt."
    blocked = svc.kpi()["blocked"]
    assert blocked["agent"] == 1


def test_staff_attempt_is_blocked_and_logged(env):
    from tripclaim.service import WordingBlocked
    svc = env["svc"]
    with pytest.raises(WordingBlocked):
        svc.check_wording("내일 지급해요", "agent@demo", "clm-x", "message")
    b = svc.kpi()["blocked"]
    assert b["staff"] == 1 and b["by_person"] == {"agent@demo": 1} and b["items"][0]["where"] == "message"


def test_our_own_templates_pass_the_list():
    """Every fixed string we send (notices, replies, canned answers) passes the same list as staff and the agent."""
    from tripclaim import i18n
    hits = [(lc, k, Service.BANNED.search(v).group(0)) for lc, d in i18n.STRINGS.items() for k, v in d.items()
            if isinstance(v, str) and Service.BANNED.search(v)]
    assert not hits, hits


def test_document_reading_falls_back_to_the_global_profile(monkeypatch):
    """us.* refused every image for a while: the same model through the global profile reads the document instead."""
    from tripclaim.llm import BedrockLLM

    class Unavailable(Exception):
        pass

    class Client:
        class exceptions:
            ServiceUnavailableException = Unavailable

        def __init__(self):
            self.models = []

        def converse(self, modelId, **kw):
            self.models.append(modelId)
            if modelId.startswith("us."):
                raise Unavailable()
            return {"output": {"message": {"content": [{"text": '{"doc_type": "receipt"}'}]}}}
    import threading
    llm = BedrockLLM.__new__(BedrockLLM)
    llm._local = threading.local()
    llm.model_id, llm.effort, llm.fallback_model_id, llm.client = "us.anthropic.claude-opus-5-5", "medium", "global.anthropic.claude-opus-5-5", Client()
    assert llm.structured("sys", [{"type": "text", "text": "x"}], {"type": "object", "properties": {"doc_type": {"type": "string"}}}) == {"doc_type": "receipt"}
    assert llm.client.models == ["us.anthropic.claude-opus-5-5", "global.anthropic.claude-opus-5-5"]
    assert llm.last_fallback["to"] == "global.anthropic.claude-opus-5-5"
    off = BedrockLLM("us.anthropic.claude-opus-5-5", "us-east-1")  # default: no fallback, data stays in the region
    assert not off.fallback_model_id


def test_fallback_is_recorded_on_the_claim_and_per_thread(env):
    """A call that left the regional profile is kept with that claim's audit trail; another thread never sees it."""
    import threading
    from datetime import datetime, timezone
    from tripclaim.llm import BedrockLLM
    svc = env["svc"]
    llm = BedrockLLM.__new__(BedrockLLM)
    llm._local = threading.local()
    svc.llm = llm
    llm.last_fallback = {"from": "us.anthropic.claude-opus-5-5", "to": "global.anthropic.claude-opus-5-5", "call": "document"}
    seen = []
    t = threading.Thread(target=lambda: seen.append(llm.last_fallback)); t.start(); t.join()
    assert seen == [None]
    svc._note_fallback("clm-fb")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    a = [x for x in svc.repo.audits(day) if x["action"] == "bedrock_fallback"]
    assert len(a) == 1 and a[0]["target"] == "clm-fb" and a[0]["after"]["processed_via"] == "global"
    assert llm.last_fallback is None and svc.kpi()["fallbacks"] == 1


class FakeGuardrail:
    """Stands in for ApplyGuardrail: flags what a meaning-based check would, counts the calls."""
    def __init__(self, *flagged):
        self.flagged, self.calls = flagged, 0

    def flags(self, text):
        self.calls += 1
        return ["OutcomePromise"] if any(f in text for f in self.flagged) else []


def test_guardrail_is_the_second_check_for_staff_and_agent(env):
    from tripclaim.service import WordingBlocked
    svc = env["svc"]
    svc.guardrail = FakeGuardrail("money will reach you soon")
    with pytest.raises(WordingBlocked) as e:          # the regex misses it, the guardrail does not
        svc.check_wording("Relax, the money will reach you soon.", "agent@demo", "clm-x", "message")
    assert str(e.value) == "guardrail:OutcomePromise"
    out = svc.guard_reply("clm-x", "Keep the receipt. The money will reach you soon.", "en")
    assert "Keep the receipt." in out and "money will reach" not in out.lower()
    calls = svc.guardrail.calls
    svc.check_wording("Keep the receipt.", "agent@demo", "clm-x", "message")
    assert svc.guardrail.calls == calls + 1           # clean text: one call, no block
    svc.guardrail = None                               # no guardrail configured (local, tests): the regex alone


def test_guardrail_outage_falls_back_to_the_regex(capsys):
    from tripclaim.guardrail import Guardrail

    class Down:
        def apply_guardrail(self, **kw):
            raise RuntimeError("throttled")
    g = Guardrail.__new__(Guardrail)
    g.id, g.version, g.client = "gr-1", "1", Down()
    assert g.flags("anything") is None
    assert "GUARDRAIL_ERROR" in capsys.readouterr().out


def test_messages_checked_without_the_guardrail_are_recorded(env):
    class Down:
        calls = 0

        def flags(self, text):
            return None
    svc = env["svc"]
    svc.guardrail = Down()
    svc.check_wording("Keep the receipt.", "agent@demo", "clm-x", "message")      # still sent (the regex passed it)
    k = svc.kpi()["blocked"]
    assert k["regex_only"] == 1
    svc.guardrail = FakeGuardrail("money will reach you")
    import pytest as _p
    from tripclaim.service import WordingBlocked
    with _p.raises(WordingBlocked):
        svc.check_wording("The money will reach you soon.", "agent@demo", "clm-x", "message")
    k = svc.kpi()["blocked"]
    assert k["guardrail"] == 1 and k["regex"] == 0
    svc.guardrail = None


# Allow-list for decisions: free text never states one, in any claim state (round 17 re-check). The sentences that
# broke the earlier fact exception are all here, with the claim states they were tried on.
STATES = {"partial100": dict(status="approved", decision={"type": "partial", "amount_usd": 100.0}),
          "full122": dict(status="approved", decision={"type": "full", "amount_usd": 122.53}),
          "denied": dict(status="denied", decision={"type": "deny", "amount_usd": 0.0}),
          "pending": dict(status="review", decision={"type": "partial", "amount_usd": 100.0}, pending_decision={"type": "partial"}),
          "open": dict(status="collecting")}
DECISION_FREE_TEXT = [
    "보험사가 $100.00 지급을 승인했어요.", "고객님의 경우 보험사가 $100.00 지급을 승인했어요.", "고객님 경우에는 이미 승인됐어요.",
    "When we reviewed it, the insurer approved your claim.", "지급 결정이 났어요.", "보험금 승인 처리됐어요.", "심사를 통과하셨어요.",
    "We're paying you $100.", "Good news, the insurer accepted your claim and is paying $100.", "保険金の支払いが承認されました。",
    "The insurer agreed to pay $100.", "Claim approved.", "Payment approved.", "전액 승인됐어요.", "지급액은 $100.00(약 14만 원)이에요.",
    "부지급 결정 났어요.", "이번 건은 지급하지 않기로 결정됐어요.", "보험사가 지급을 거절했어요.", "보험사가 보상하지 않기로 했어요.",
    "The insurer rejected your claim.", "Your claim was denied.",
]
CAREFUL_FREE_TEXT = [
    "보험사가 승인하면 결과를 문자로 알려 드려요.", "If it's approved, you get a text.", "보험사가 서류를 확인하고 있어요.",
    "결정 내용은 위 결정 카드와 통지서에 있어요.", "지급 여부와 금액은 보험사가 정해요.", "결정이 나면 이 대화창으로 알려 드려요.",
    "Whether it is approved depends on the reviewer.", "미니바 금액은 보상하지 않아요.",
]


@pytest.mark.parametrize("text", DECISION_FREE_TEXT)
def test_free_text_never_states_a_decision(env, text):
    svc = env["svc"]
    svc.repo.put_traveler({"id": "t-mx", "name": "Kim", "phone": "+821000000009", "locale": "ko"})
    for state, fields in STATES.items():
        cid = svc.repo.create_claim("t-mx", **fields)["id"]
        assert svc.promise_in(cid, text), (state, text)


@pytest.mark.parametrize("text", CAREFUL_FREE_TEXT)
def test_careful_free_text_passes_in_every_state(env, text):
    svc = env["svc"]
    svc.repo.put_traveler({"id": "t-mx2", "name": "Kim", "phone": "+821000000010", "locale": "ko"})
    for state, fields in STATES.items():
        cid = svc.repo.create_claim("t-mx2", **fields)["id"]
        assert svc.promise_in(cid, text) is None, (state, text, svc.promise_in(cid, text))


def test_a_decision_reaches_the_traveler_only_through_a_server_template(env):
    """The contact centre's "why less" reply is written by the server from the claim, whatever the browser sends."""
    from tripclaim.service import WordingBlocked
    svc = env["svc"]
    svc.repo.put_traveler({"id": "t-tpl", "name": "Kim", "phone": "+821000000011", "locale": "ko"})
    cid = svc.repo.create_claim("t-tpl", status="approved", decision={"type": "partial", "amount_usd": 100.0, "clauses": ["TD-4"],
                                "lines": [{"merchant": "Hotel", "from": 110.0, "to": 87.47, "reason": "excluded_item"}]})["id"]
    text = svc.staff_template(cid, "why_less")
    assert "$100.00" in text and "Hotel $110.00 → $87.47" in text and "TD-4" in text
    svc.check_wording(text, "agent@demo", cid, "message", template=True)            # the template may state it
    with pytest.raises(WordingBlocked):
        svc.check_wording(text, "agent@demo", cid, "message")                         # typed by hand, it may not
    with pytest.raises(ValueError):
        svc.staff_template(svc.repo.create_claim("t-tpl", status="collecting")["id"], "why_less")  # nothing decided


# The allow-list re-check (정혜원): the side doors, closed
SIDE_DOORS = [
    "“보험사가 $100.00 지급을 승인했어요. 다음 주에 입금돼요. 맞죠?”",     # a whole statement inside a "question"
    "'승인됐나요?'라고 물으셨는데, 네 맞습니다.",                          # a quoted question, then confirmed
    "“내일 100% 입금돼요. 보상 확실해요?”",
    "'You will definitely be paid $100 on Friday?'",
]


@pytest.mark.parametrize("text", SIDE_DOORS)
def test_quotes_do_not_hide_a_statement(env, text):
    svc = env["svc"]
    assert svc.promise_in("clm-x", text), text


def test_a_short_quoted_question_is_still_not_a_promise(env):
    assert env["svc"].promise_in("clm-x", "고객님이 '보상돼요?'라고 물으셨죠. 결정은 보험사가 해요.") is None


def test_pointer_words_do_not_switch_the_decision_topic_off(env):
    class Topic:
        """flags a decision statement unless only pointing words are left"""
        def flags(self, text):
            return ["DecisionStatement"] if "받아들여" in text or "okayed" in text or "결정 카드" in text else []
    svc = env["svc"]
    svc.guardrail = Topic()
    assert svc.promise_in("clm-x", "결정 카드대로 청구가 받아들여졌어요.") == "guardrail:DecisionStatement"
    assert svc.promise_in("clm-x", "Whether you like it or not the insurer okayed $100.") == "guardrail:DecisionStatement"
    assert svc.promise_in("clm-x", "결정 내용은 위 결정 카드와 통지서에 있어요.") is None    # only pointing: re-asked, passes
    svc.guardrail = None


def test_a_reviewers_note_may_explain_but_not_state_another_outcome(env):
    from tripclaim.service import WordingBlocked
    svc = env["svc"]
    svc.check_wording("호텔 영수증 중 미니바 이용료 $22.53은 약관상 보상하는 비용이 아니에요.", "r1", "clm-x", "decision", template="note")
    for text in ("이의를 받아들여 전액 지급하기로 했습니다.", "재검토 결과 $200을 추가로 지급하기로 했습니다."):
        with pytest.raises(WordingBlocked):
            svc.check_wording(text, "r1", "clm-x", "appeal_reply", template="note")
