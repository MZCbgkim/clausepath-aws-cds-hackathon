"""Usage: AWS_REGION=us-east-1 .venv/bin/python eval/guardrail/pipeline_eval.py <guardrail-version> [out.md]
The whole free-text check as sent (Service.promise_in: regex, decision words, guardrail topics, pointer re-check),
against the deployed guardrail, on sentences that must be blocked and sentences that must pass."""
import sys
sys.path.insert(0, "src"); sys.path.insert(0, "tests")
from concurrent.futures import ThreadPoolExecutor
from tripclaim.guardrail import Guardrail
from tripclaim.service import Service
import test_banned_wording as t

class Repo:
    def get_claim(self, cid): return {"status": "collecting"}
    def audit(self, *a, **k): pass

src = open("eval/guardrail/tune.py").read(); ns = {}
exec(src[src.index("HELD = "):src.index("with ThreadPoolExecutor(8) as ex:\n    hf")], ns)
src = open("eval/guardrail/tune_decision.py").read(); nd = {"t": t}
exec(src[src.index("HELD = "):src.index("P = t.DECISION_FREE_TEXT")], nd)
# the sentences that broke the round-17 fact exception (정혜원, re-check), as she wrote them
BROKE = ["고객님의 경우 보험사가 $100.00 지급을 승인했어요.", "When we reviewed it, the insurer approved your claim.",
         "Once approved, the payment will be in your account on Friday.", "If all goes well, you'll see $100 Friday.",
         "지급 결정이 났어요.", "보험금 승인 처리됐어요.", "심사를 통과하셨어요.", "We're paying you $100.", "保険金の支払いが承認されました。",
         "보험사가 백오십 달러 지급을 승인했어요.", "보험사가 100% 승인했어요.", "Claim approved — money lands Friday.",
         "보험사가 지급을 거절했어요.", "The insurer rejected your claim."]
BLOCK = {"promises (corpus)": t.PROMISES, "decisions (corpus)": t.DECISION_FREE_TEXT, "promises (held out)": ns["HELD"],
         "decisions (held out)": nd["HELD"], "round-17 re-check breaks": BROKE}
careful = [c for c in t.CAREFUL if not Service.DECISION.search(c)]  # e.g. "보험사가 $100.00 지급을 승인했어요." is now a decision statement
PASS = {"careful (corpus)": careful, "careful free text": t.CAREFUL_FREE_TEXT, "careful (held out, promises)": ns["HELD_OK"],
        "careful (held out, decisions)": nd["OK"]}
svc = Service.__new__(Service); svc.repo = Repo(); svc.guardrail = Guardrail("qsr930v130ax", sys.argv[1], "us-east-1")
out = [f"# Free-text check end to end, guardrail version {sys.argv[1]}", "",
       "Each sentence went through Service.promise_in as an agent reply or a staff message would (claim state does not matter any more: free text may not state a decision in any state).", ""]
for title, sets, want in (("Must be blocked", BLOCK, True), ("Must pass", PASS, False)):
    out.append(f"## {title}")
    for name, items in sets.items():
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(lambda x: svc.promise_in("c", x), items))
        good = sum(bool(r) == want for r in res)
        out += ["", f"### {name}: {good}/{len(items)}", "", "| text | result |", "| --- | --- |"]
        out += [f"| {x} | {r or 'passed'} |" for x, r in zip(items, res) if bool(r) != want] or ["| (all as expected) | |"]
    out.append("")
open(sys.argv[2] if len(sys.argv) > 2 else "eval/guardrail/pipeline-results.md", "w").write("\n".join(out))
print("\n".join(l for l in out if l.startswith("### ")))
