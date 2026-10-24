"""Extraction accuracy on real Japanese receipts (JaWildText receipt_kie sample).

Usage: python3 runners/run_extract_real.py --data ../data/jawildtext [--run-id NAME] [--jobs N]
Same prompt/schema as run_extract.py. Scores the fields claim validation depends on.
"""
import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EVAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EVAL / "runners"))
from run_extract import call  # noqa: E402

PREFECTURES = {
    "北海道": "Hokkaido", "青森県": "Aomori", "岩手県": "Iwate", "宮城県": "Miyagi", "秋田県": "Akita", "山形県": "Yamagata",
    "福島県": "Fukushima", "茨城県": "Ibaraki", "栃木県": "Tochigi", "群馬県": "Gunma", "埼玉県": "Saitama", "千葉県": "Chiba",
    "東京都": "Tokyo", "神奈川県": "Kanagawa", "新潟県": "Niigata", "富山県": "Toyama", "石川県": "Ishikawa", "福井県": "Fukui",
    "山梨県": "Yamanashi", "長野県": "Nagano", "岐阜県": "Gifu", "静岡県": "Shizuoka", "愛知県": "Aichi", "三重県": "Mie",
    "滋賀県": "Shiga", "京都府": "Kyoto", "大阪府": "Osaka", "兵庫県": "Hyogo", "奈良県": "Nara", "和歌山県": "Wakayama",
    "鳥取県": "Tottori", "島根県": "Shimane", "岡山県": "Okayama", "広島県": "Hiroshima", "山口県": "Yamaguchi",
    "徳島県": "Tokushima", "香川県": "Kagawa", "愛媛県": "Ehime", "高知県": "Kochi", "福岡県": "Fukuoka", "佐賀県": "Saga",
    "長崎県": "Nagasaki", "熊本県": "Kumamoto", "大分県": "Oita", "宮崎県": "Miyazaki", "鹿児島県": "Kagoshima", "沖縄県": "Okinawa",
}


def prefecture_of(address):
    return next((en for jp, en in PREFECTURES.items() if (address or "").startswith(jp)), None)


def score(t, ex):
    r = ex.get("receipt") or {}
    dt = r.get("datetime") or ""
    got_date, got_time = (dt.split(" ") + [None])[:2] if dt else (None, None)
    checks = {"doc_type": ex["doc_type"] == "receipt"}
    if t["date"]:
        checks["date"] = got_date == t["date"]
    if t["time"]:
        checks["time"] = got_time == t["time"]
    if t["total"] is not None:
        checks["total"] = r.get("total") == t["total"]
    pref = prefecture_of(t["store_address"])
    if pref:
        checks["prefecture"] = (r.get("prefecture") or "").lower() == pref.lower()
    if t["item_prices"]:
        got = sorted(int(i["amount"]) for i in r.get("line_items") or [] if i.get("amount") is not None)
        checks["item_prices"] = got == t["item_prices"]
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--run-id", default=time.strftime("real-%Y%m%d-%H%M%S"))
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()

    data = Path(args.data).resolve()
    truth = [json.loads(line) for line in (data / "truth.jsonl").read_text().splitlines()]
    out_dir = EVAL / "results" / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    def extract(t):
        path = out_dir / f"{t['id']}.json"
        if not path.exists():
            try:
                path.write_text(json.dumps(call(data / "images" / f"{t['id']}.jpg", args.model, args.effort),
                                           ensure_ascii=False, indent=1))
            except Exception as e:  # keep going; rerun the same run id to retry
                print(f"  {t['id']} FAILED {str(e)[:150]}", flush=True)
                return None
        return json.loads(path.read_text())

    print(f"run {args.run_id}: {len(truth)} receipts", flush=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        outs = list(pool.map(extract, truth))

    per_field, misses, low_conf = Counter(), [], 0
    totals = Counter()
    for t, out in zip(truth, outs):
        if not out:
            continue
        ex = out["structured_output"]
        low_conf += ex["legibility"] != "good" or ex["confidence"] < 0.6
        for k, ok in score(t, ex).items():
            totals[k] += 1
            per_field[k] += ok
            if not ok:
                r = ex.get("receipt") or {}
                got = {"date": (r.get("datetime") or "")[:10], "time": (r.get("datetime") or "")[11:],
                       "total": r.get("total"), "prefecture": r.get("prefecture"),
                       "item_prices": sorted(i["amount"] for i in r.get("line_items") or [] if i.get("amount") is not None),
                       "doc_type": ex["doc_type"]}[k]
                want = {"date": t["date"], "time": t["time"], "total": t["total"],
                        "prefecture": prefecture_of(t["store_address"]), "item_prices": t["item_prices"],
                        "doc_type": "receipt"}[k]
                misses.append(f"- {t['id']} {k}: got {got} want {want} (label raw: "
                              f"{ {'date': t['date_raw'], 'time': t['time_raw'], 'total': t['total_raw']}.get(k, '') }) "
                              f"conf {ex['confidence']:.2f} {ex['legibility']}")
    n = sum(1 for o in outs if o)
    lines = [f"receipts scored: {n}/{len(truth)}", "", "| field | correct |", "| --- | --- |"]
    for k in ("doc_type", "date", "time", "total", "prefecture", "item_prices"):
        if totals[k]:
            lines.append(f"| {k} | {per_field[k]}/{totals[k]} ({per_field[k] / totals[k]:.0%}) |")
    lines += ["", f"- marked poor/unreadable or confidence < 0.6: {low_conf}", "", "## misses", ""] + misses
    text = "\n".join(lines)
    (out_dir / "report.md").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
