"""Sample real Japanese receipts from llm-jp/jawildtext (receipt_kie, Apache-2.0) via the HF dataset viewer API.

Usage: python3 -I tools/fetch_jawildtext.py OUT_DIR [--n 120] [--seed 5]
Writes OUT_DIR/images/<image_id>.jpg (long edge <= 1568 px) and OUT_DIR/truth.jsonl with normalised labels.
Attribution: JaWildText, llm-jp, Apache-2.0.
"""
import io
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

API = "https://datasets-server.huggingface.co/rows?dataset=llm-jp/jawildtext&config=receipt_kie&split=train"


def get(url, tries=6):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tripclaim-eval"})) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == tries - 1:
                raise
            time.sleep(10 * (attempt + 1))  # dataset viewer rate limit


def num(text):
    text = text or ""
    if "¥" in text:  # e.g. "3点¥4,260": the item count before ¥ is not part of the amount
        text = text.rsplit("¥", 1)[1]
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def norm_date(text):
    if not text:
        return None
    m = re.search(r"(令和|R)\s*(\d{1,2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})", text)
    if m:
        return f"{2018 + int(m.group(2)):04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
    m = re.search(r"(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})", text)
    return f"20{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def norm_time(text):
    m = re.search(r"(\d{1,2})\s*[:時]\s*(\d{2})", text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def value(fields, key):
    v = fields.get(key)
    return v.get("value") if isinstance(v, dict) else None


def main():
    out = Path(sys.argv[1])
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 120
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 5
    total = json.loads(get(API + "&offset=0&length=1"))["num_rows_total"]
    picks = sorted(random.Random(seed).sample(range(total), n))
    (out / "images").mkdir(parents=True, exist_ok=True)
    pages = {}
    for page in sorted({i // 100 for i in picks}):  # 100 rows per call keeps us under the rate limit
        for r in json.loads(get(API + f"&offset={page * 100}&length=100"))["rows"]:
            pages[r["row_idx"]] = r["row"]
        time.sleep(1)
    with open(out / "truth.jsonl", "w") as fh:
        for i in picks:
            row = pages[i]
            f = row["fields"] or {}
            path = out / "images" / f"{row['image_id']}.jpg"
            if not path.exists():
                img = Image.open(io.BytesIO(get(row["image"]["src"]))).convert("RGB")
                img.thumbnail((1568, 1568))
                img.save(path, quality=88)
            items = [li for li in (f.get("line_items") or []) if isinstance(li, dict)]
            fh.write(json.dumps({
                "id": row["image_id"],
                "store_name": value(f, "store_name"), "store_address": value(f, "store_address"),
                "date_raw": value(f, "date"), "date": norm_date(value(f, "date")),
                "time_raw": value(f, "time"), "time": norm_time(value(f, "time")),
                "total_raw": value(f, "total_amount"), "total": num(value(f, "total_amount")),
                "tax": num(value(f, "tax_amount")),
                "item_prices": sorted(p for p in (num(value(li, "item_price")) for li in items) if p is not None),
            }, ensure_ascii=False) + "\n")
            print(f"  {row['image_id']}", flush=True)
    print(f"wrote {n} receipts to {out}")


if __name__ == "__main__":
    main()
