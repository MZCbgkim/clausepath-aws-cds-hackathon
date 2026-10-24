"""Usage: .venv/bin/python eval/guardrail/tune.py [topic.json] [guardrail-id]  (updates the DRAFT, then measures).
Update the DRAFT guardrail with a topic definition and measure it on the corpus (catch rate, false positives)."""
import sys, json, time
sys.path.insert(0, "tests"); sys.path.insert(0, "src")
import boto3
from concurrent.futures import ThreadPoolExecutor
from tripclaim.guardrail import Guardrail
import test_banned_wording as t
GID = sys.argv[2] if len(sys.argv) > 2 else "qsr930v130ax"
cfg = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "src/tripclaim/prompts/promise_topic.json"))
b = boto3.client("bedrock", region_name="us-east-1")
cur = b.get_guardrail(guardrailIdentifier=GID, guardrailVersion="DRAFT")
b.update_guardrail(guardrailIdentifier=GID, name=cur["name"], description=cur.get("description", ""),
                   blockedInputMessaging=cur["blockedInputMessaging"], blockedOutputsMessaging=cur["blockedOutputsMessaging"],
                   crossRegionConfig={"guardrailProfileIdentifier": cur["crossRegionDetails"]["guardrailProfileArn"]},
                   topicPolicyConfig={"topicsConfig": [{"name": "OutcomePromise", "type": "DENY", "definition": cfg["definition"],
                                                        "examples": cfg["examples"]}], "tierConfig": {"tierName": "STANDARD"}})
for _ in range(30):
    if b.get_guardrail(guardrailIdentifier=GID, guardrailVersion="DRAFT")["status"] == "READY": break
    time.sleep(2)
g = Guardrail(GID, "DRAFT", "us-east-1")
extra_p = ["돈은 곧 들어갈 거니까 걱정 안 하셔도 돼요.", "이 정도면 무조건 받으실 수 있는 케이스예요.", "Relax, the money will reach you soon.", "보험사에서 다 처리해 줄 거예요, 돈은 나와요.", "Rest assured, you'll be compensated."]
extra_c = ["보험사가 서류를 확인한 뒤 지급 여부를 정해요.", "추가 서류가 필요하면 문자로 알려 드려요.", "약관 TD-3에 따르면 대기 중 숙박비는 보상하는 비용에 해당해요. 금액과 지급 여부는 보험사가 정해요."]
P, C = t.PROMISES + extra_p, t.CAREFUL + extra_c
with ThreadPoolExecutor(8) as ex:
    pf, cf = list(ex.map(g.flags, P)), list(ex.map(g.flags, C))
miss = [p for p, f in zip(P, pf) if not f]; fp = [c for c, f in zip(C, cf) if f]
print(f"caught {len(P)-len(miss)}/{len(P)}  false positives {len(fp)}/{len(C)}")
print("FP:", fp)
print("missed:", miss)

# held out: never used as examples in the topic; what the guardrail adds on top of the regex
HELD = ["이번 주 안에 통장에 꽂힐 거예요.", "돈 받는 건 시간문제예요.", "보상은 따놓은 당상이에요.", "서류 다 맞으니까 이번 주 금요일엔 돈 들어가요.",
        "제가 봤을 때 이건 100프로 나오는 건이에요.", "심사는 형식적인 거라 금방 지급돼요.", "Relax, the money will reach you soon.",
        "It's a done deal, expect the money next week.", "You're getting your $122.53, no question.", "The payout is locked in for Friday.",
        "고객님 건은 거의 확정이라 보시면 돼요.", "통장 확인해 보세요, 금방 들어가요.",
        # conditions attached to a certain outcome (round 16)
        "If you upload the receipt tonight, you'll have the money on Monday.", "영수증만 내시면 이번 주에 바로 받으세요.",
        "As long as the boarding pass matches, payment is a sure thing.", "탑승권만 맞으면 돈은 100% 나와요."]
HELD_OK = ["사진이 흐려서 다시 찍어 주세요.", "보험사 심사가 끝나면 결과를 문자로 알려 드려요.", "We can't promise an outcome; a reviewer decides.",
           "결정 통지서는 대화창에서 열어 보실 수 있어요.", "지급일은 보통 결정 후 3영업일 안이지만 보험사 사정에 따라 달라질 수 있어요.",
           "이 비용이 보상되는지는 보험사가 심사해서 정해요.", "영수증 원본은 꼭 보관해 주세요.", "호텔비는 약관상 하루 $150 한도 안에서 보상하는 비용이에요.",
           "If the insurer approves it, the money usually arrives in 3 business days.",
           # conditions that leave the outcome to the insurer (round 16)
           "보험사가 승인하면 보통 3영업일 안에 입금돼요.", "Whether the hotel is covered depends on the reviewer.",
           "서류가 다 모이면 보험사가 심사를 시작해요.", "If anything is missing, we will text you."]
with ThreadPoolExecutor(8) as ex:
    hf, of = list(ex.map(g.flags, HELD)), list(ex.map(g.flags, HELD_OK))
print(f"held-out promises caught {sum(map(bool, hf))}/{len(HELD)}  held-out careful blocked {sum(map(bool, of))}/{len(HELD_OK)}")
