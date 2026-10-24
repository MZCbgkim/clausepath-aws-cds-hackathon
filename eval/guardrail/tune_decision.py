"""Usage: .venv/bin/python eval/guardrail/tune_decision.py [decision_topic.json] [guardrail-id]
Puts both topics (OutcomePromise + DecisionStatement) on the DRAFT guardrail, then measures the decision topic on
decision statements (must be flagged) and careful free text (must not be flagged)."""
import sys, json, time
sys.path.insert(0, "tests"); sys.path.insert(0, "src")
import boto3
from concurrent.futures import ThreadPoolExecutor
from tripclaim.guardrail import Guardrail
import test_banned_wording as t
DEC = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "src/tripclaim/prompts/decision_topic.json"))
PRO = json.load(open("src/tripclaim/prompts/promise_topic.json"))
GID = sys.argv[2] if len(sys.argv) > 2 else "qsr930v130ax"
b = boto3.client("bedrock", region_name="us-east-1")
cur = b.get_guardrail(guardrailIdentifier=GID, guardrailVersion="DRAFT")
b.update_guardrail(guardrailIdentifier=GID, name=cur["name"], description=cur.get("description", ""),
                   blockedInputMessaging=cur["blockedInputMessaging"], blockedOutputsMessaging=cur["blockedOutputsMessaging"],
                   crossRegionConfig={"guardrailProfileIdentifier": cur["crossRegionDetails"]["guardrailProfileArn"]},
                   topicPolicyConfig={"topicsConfig": [
                       {"name": "OutcomePromise", "type": "DENY", "definition": PRO["definition"], "examples": PRO["examples"]},
                       {"name": "DecisionStatement", "type": "DENY", "definition": DEC["definition"], "examples": DEC["examples"]}],
                       "tierConfig": {"tierName": "STANDARD"}})
for _ in range(30):
    if b.get_guardrail(guardrailIdentifier=GID, guardrailVersion="DRAFT")["status"] == "READY": break
    time.sleep(2)
g = Guardrail(GID, "DRAFT", "us-east-1")
# held out: never in the regex or the examples; synonyms and paraphrases the word list cannot know
HELD = ["보험사에서 OK 났어요.", "고객님 건은 지급으로 처리됐습니다.", "심사팀에서 받아들였어요.", "이번 건은 안 나오는 걸로 확정됐어요.",
        "They've signed off on your claim.", "Your claim went through.", "The insurer turned your claim down.", "We'll be covering $100 of it.",
        "請求は認められました。", "결과적으로 $100만 인정됐어요.", "보험사가 미니바는 빼고 나머지는 다 주기로 했어요.", "Your claim's a yes."]
OK = t.CAREFUL_FREE_TEXT + ["자세한 내용과 이유는 위 결정 카드와 통지서에 있어요.", "약관 TD-3에 따르면 대기 중 숙박비는 보상하는 비용에 해당해요. 금액과 지급 여부는 보험사가 정해요.",
     "서류가 다 모이면 보험사가 심사를 시작해요.", "If anything is missing, we will text you.", "The decision letter explains each line.",
     "결정 통지서는 인쇄하거나 파일로 저장할 수 있어요.", "이의 신청은 결정 카드의 '이의 신청하기'로 하실 수 있어요.", "미니바는 약관상 보상하지 않는 항목이에요."]
P = t.DECISION_FREE_TEXT + HELD
with ThreadPoolExecutor(8) as ex:
    pf, of = list(ex.map(g.flags, P)), list(ex.map(g.flags, OK))
dec = lambda f: bool(f) and "DecisionStatement" in f
print(f"decision topic: corpus {sum(dec(f) for f in pf[:len(t.DECISION_FREE_TEXT)])}/{len(t.DECISION_FREE_TEXT)}, "
      f"held-out {sum(dec(f) for f in pf[len(t.DECISION_FREE_TEXT):])}/{len(HELD)}, careful flagged {sum(bool(f) for f in of)}/{len(OK)}")
print("held-out missed:", [s for s, f in zip(HELD, pf[len(t.DECISION_FREE_TEXT):]) if not dec(f)])
print("careful flagged:", [(s, f) for s, f in zip(OK, of) if f])
