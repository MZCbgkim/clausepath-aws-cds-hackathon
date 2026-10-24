"""Document extraction + rule validation eval on synthetic claim bundles with planted errors.

Usage: python3 runners/run_extract.py --data ../data/synth_v1 [--run-id NAME] [--jobs N]

Each document is extracted once by the model (cached in results/<run-id>/<doc>.json); bundles are then
validated by claim/validate.py. Two scores: field accuracy per document, and flag detection per bundle.
"""
import argparse
import base64
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EVAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EVAL.parent / "src"))
from tripclaim.validate import _names, validate  # noqa: E402

PROMPT = EVAL / "prompts" / "extract.md"
SCHEMA = EVAL / "schemas" / "extract.schema.json"
TIMEOUT_S = 1800
CITY_EQUIV = {"ota": {"ota", "tokyo"}, "tokyo": {"tokyo", "minato"}, "osaka": {"osaka"}}


def call(doc_path, model, effort):
    if doc_path.suffix == ".txt":
        content = [{"type": "text", "text": "Forwarded email:\n\n" + doc_path.read_text()}]
    else:
        data = base64.b64encode(doc_path.read_bytes()).decode()
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                   {"type": "text", "text": "Extract this document."}]
    message = {"type": "user", "message": {"role": "user", "content": content}}
    cmd = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--model", model, "--effort", effort, "--system-prompt-file", str(PROMPT),
           "--json-schema", SCHEMA.read_text(), "--tools", "", "--strict-mcp-config", "--setting-sources", "",
           "--no-session-persistence"]
    started = time.time()
    proc = subprocess.run(cmd, input=json.dumps(message) + "\n", capture_output=True, text=True,
                          timeout=TIMEOUT_S, cwd="/tmp")
    result = next((json.loads(line) for line in reversed(proc.stdout.splitlines())
                   if line.startswith('{"') and '"type":"result"' in line), None)
    if proc.returncode != 0 or result is None:
        raise RuntimeError((proc.stderr or proc.stdout)[-2000:])
    result["_wall_s"] = round(time.time() - started, 1)
    return result


def field_checks(truth, ex):
    """Returns {field: ok} for the fields that matter to validation."""
    if truth.get("unreadable"):
        return {"flagged_unreadable": ex["legibility"] != "good" or ex["confidence"] < 0.6}
    checks = {"doc_type": ex["doc_type"] == truth["doc_type"]}
    if truth["doc_type"] == "airline_letter":
        l = ex["letter"] or {}
        checks |= {
            "flight": (l.get("flight") or "").replace(" ", "") == truth["flight"],
            "date": l.get("date") == truth["date"],
            "passenger": _names(truth["passenger"]) == _names(l.get("passenger")),
            "status": l.get("status") == truth["status"],
            "reason_category": l.get("reason_category") == truth["reason_category"],
            "airline_provided": set(truth["airline_provided"]) <= set(l.get("airline_provided") or []),
        }
        if truth["delay_hours"]:
            checks["delay_hours"] = abs((l.get("delay_hours") or 0) - truth["delay_hours"]) < 0.25
    else:
        r = ex["receipt"] or {}
        city = (r.get("city") or "").lower()
        checks |= {
            "city": any(c in city for c in CITY_EQUIV[truth["city"].lower()]),
            "prefecture": (r.get("prefecture") or "").lower() == truth["prefecture"].lower(),
            "datetime": r.get("datetime") == truth["datetime"],
            "currency": r.get("currency") == truth["currency"],
            "total": r.get("total") == truth["total"],
            "categories": sorted({i["category"] for i in r.get("line_items") or []}) == truth["categories"],
        }
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--run-id", default=time.strftime("extract-%Y%m%d-%H%M%S"))
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()

    data = Path(args.data).resolve()
    truth = json.loads((data / "truth.json").read_text())
    context = json.loads((data / "context.json").read_text())
    bundles = [json.loads(line) for line in (data / "bundles.jsonl").read_text().splitlines()]
    out_dir = EVAL / "results" / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    def extract(doc_id):
        path = out_dir / f"{doc_id}.json"
        if not path.exists():
            src = next(p for p in (data / "docs").iterdir() if p.stem == doc_id)
            path.write_text(json.dumps(call(src, args.model, args.effort), ensure_ascii=False, indent=1))
            print(f"  {doc_id} done", flush=True)
        return json.loads(path.read_text())

    print(f"run {args.run_id}: {len(truth)} documents, {len(bundles)} bundles", flush=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        raw = dict(zip(truth, pool.map(extract, truth)))
    extracted = {k: v["structured_output"] for k, v in raw.items()}

    lines = ["## Field accuracy", "", "| doc | wrong fields |", "| --- | --- |"]
    ok_fields = total_fields = 0
    for doc_id, t in truth.items():
        checks = field_checks(t, extracted[doc_id])
        ok_fields += sum(checks.values())
        total_fields += len(checks)
        wrong = [k for k, v in checks.items() if not v]
        lines.append(f"| {doc_id} | {', '.join(wrong) or '-'} |")
    lines += ["", f"- fields correct: {ok_fields}/{total_fields}", "",
              "## Bundle flags", "", "| bundle | note | expected | found | result |", "| --- | --- | --- | --- | --- |"]
    caught = expected_n = false_alarms = clean_ok = clean_n = 0
    for b in bundles:
        flags = validate(context, [{"id": d, "extracted": extracted[d]} for d in b["docs"]])
        codes = sorted({f["code"] for f in flags})
        exp = set(b["expected_flags"])
        hit = exp & set(codes)
        extra = set(codes) - exp
        caught += len(hit)
        expected_n += len(exp)
        false_alarms += len(extra)
        if not exp:
            clean_n += 1
            clean_ok += not codes
        result = "ok" if hit == exp and not extra else f"missed {sorted(exp - hit)} extra {sorted(extra)}"
        lines.append(f"| {b['id']} | {b['note']} | {', '.join(sorted(exp)) or '-'} | {', '.join(codes) or '-'} | {result} |")
    walls = [v.get("_wall_s", 0) for v in raw.values()]
    lines += ["", f"- planted errors caught: {caught}/{expected_n}",
              f"- unexpected flags: {false_alarms} (clean bundles with no flag: {clean_ok}/{clean_n})",
              f"- extraction wall {sum(walls) / len(walls):.1f}s/doc"]
    text = "\n".join(lines)
    (out_dir / "report.md").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
