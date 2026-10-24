"""Coverage-guidance eval: does the agent read the policy, cite it, and build the right checklist?

Usage: python3 runners/run_coverage.py [--run-id NAME] [--model M] [--effort E] [--jobs N] [--only C01,C02]

The policy text goes into the system prompt (fixed prefix, cacheable); each case is the user message.
Outputs are cached per case in results/<run-id>/; re-running the same run id only re-scores.
"""
import argparse
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EVAL = Path(__file__).resolve().parent.parent
POLICY = EVAL.parent / "policy" / "sample_policy.md"
PROMPT = EVAL / "prompts" / "coverage.md"
SCHEMA = EVAL / "schemas" / "coverage.schema.json"
GOLDEN = EVAL / "golden" / "coverage_cases.jsonl"
TIMEOUT_S = 1800
MAX_REPLY_WORDS = 60  # one WhatsApp screen
MAX_CITES = 6         # cites should be the clauses the answer relies on, not the whole policy

VALID_CLAUSES = set(re.findall(r"\[([A-Z]+-\d+)\]", POLICY.read_text()))
PROMISE = re.compile(r"\byou(?:'ll| will) (?:get|receive|be paid|be reimbursed)\b|\bguarantee|\bdefinitely\b"
                     r"|\b(?:is|are|will be) (?:fully )?covered\b(?![^.]*\b(?:likely|probably|may)\b)", re.I)
ASKS_BANK = re.compile(r"\b(?:send|share|give|reply with)\b[^.]*\b(?:bank|account number|iban|routing)\b", re.I)


def call(case_input, system_prompt, model, effort):
    cmd = ["claude", "-p", json.dumps(case_input), "--model", model, "--effort", effort,
           "--system-prompt-file", str(system_prompt), "--json-schema", SCHEMA.read_text(),
           "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
           "--output-format", "json"]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, cwd="/tmp")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    out = json.loads(proc.stdout)
    out["_wall_s"] = round(time.time() - started, 1)
    return out


def check(case, res):
    exp, problems = case["expected"], []
    cited = {c.strip().strip("[]").strip() for c in res["cited_clauses"]}  # model sometimes keeps the brackets
    items = {c["item"]: c for c in res["checklist"]}
    lower = lambda xs: " ".join(xs).lower()

    if res["status"] not in exp["status_any"]:
        problems.append(f"status {res['status']} not in {exp['status_any']}")
    missing_cites = set(exp["cite_all"]) - cited
    if missing_cites:
        problems.append(f"missing cite {sorted(missing_cites)}")
    invalid = cited - VALID_CLAUSES
    if invalid:
        problems.append(f"invalid cite {sorted(invalid)}")
    for item in exp["checklist_must"]:
        if item not in items:
            problems.append(f"checklist missing {item}")
    for item in exp["checklist_must_not"]:
        if item in items:
            problems.append(f"checklist has received {item}")
    for item in exp["when_now"]:
        if item in items and items[item]["when"] != "now":
            problems.append(f"{item} when={items[item]['when']}")
    if res["status"] == "likely_not_covered" and res["checklist"]:
        problems.append("checklist on not-covered")
    for kw in exp["covered_kw"]:
        if kw not in lower(res["covered_expenses"]):
            problems.append(f"covered lacks '{kw}'")
    for kw in exp["not_covered_kw"]:
        if kw not in lower(res["not_covered_expenses"]):
            problems.append(f"not-covered lacks '{kw}'")
    for kw in exp["reply_kw"]:
        if kw.lower() not in res["reply"].lower():
            problems.append(f"reply lacks '{kw}'")
    promise = PROMISE.search(res["reply"])
    if promise:
        problems.append(f"PROMISE '{promise.group(0)}'")
    for sentence in re.split(r"(?<=[.!?;:])\s+", res["reply"]):
        # asking for bank details is the failure; pointing to the secure page or refusing them is correct
        if ASKS_BANK.search(sentence) and not re.search(r"secure|don't|do not|never|won't", sentence, re.I):
            problems.append("ASKS BANK DETAILS")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", res["reply"].strip()) if s]
    if len(sentences) > 3:
        problems.append(f"reply {len(sentences)} sentences")
    words = len(res["reply"].split())
    if words > MAX_REPLY_WORDS:
        problems.append(f"reply {words} words")
    if len(cited) > MAX_CITES:
        problems.append(f"{len(cited)} cites")
    if res["status"] == "needs_info" and not res["clarifying_question"]:
        problems.append("needs_info without question")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=time.strftime("cov-%Y%m%d-%H%M%S"))
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--only")
    args = ap.parse_args()

    cases = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    if args.only:
        cases = [c for c in cases if c["id"] in set(args.only.split(","))]
    out_dir = EVAL / "results" / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    system_prompt = out_dir / "system_prompt.md"
    system_prompt.write_text(PROMPT.read_text() + POLICY.read_text())

    def run(case):
        path = out_dir / f"{case['id']}.json"
        if not path.exists():
            path.write_text(json.dumps(call(case["case"], system_prompt, args.model, args.effort), indent=1))
            print(f"  {case['id']} done", flush=True)
        return json.loads(path.read_text())

    print(f"run {args.run_id}: {len(cases)} cases, {args.model} effort={args.effort}", flush=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        outs = list(pool.map(run, cases))

    lines = ["| id | note | status | cited | problems |", "| --- | --- | --- | --- | --- |"]
    clean = 0
    critical = 0
    for case, out in zip(cases, outs):
        res = out["structured_output"]
        problems = check(case, res)
        clean += not problems
        # the costly error: telling someone who is likely covered that they are not
        if "likely_covered" in case["expected"]["status_any"] and res["status"] == "likely_not_covered":
            critical += 1
            problems.insert(0, "CRITICAL wrongly told not covered")
        lines.append(f"| {case['id']} | {case['note']} | {res['status']} | {', '.join(res['cited_clauses'])} | "
                     f"{'; '.join(problems) or 'ok'} |")
    usage = [o.get("usage", {}) for o in outs]
    lines += ["", f"- cases with no problems: {clean}/{len(cases)}",
              f"- wrongly told not covered: {critical}",
              f"- wall {sum(o.get('_wall_s', 0) for o in outs) / len(outs):.1f}s/case, "
              f"cache read {sum(u.get('cache_read_input_tokens', 0) for u in usage)}, "
              f"cache write {sum(u.get('cache_creation_input_tokens', 0) for u in usage)}, "
              f"output {sum(u.get('output_tokens', 0) for u in usage)}",
              "", "## replies", ""]
    for case, out in zip(cases, outs):
        lines.append(f"- {case['id']}: {out['structured_output']['reply']}")
    text = "\n".join(lines)
    (out_dir / "report.md").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
