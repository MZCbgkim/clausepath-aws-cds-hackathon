"""Generate synthetic claim documents (fictional airline Kumo Air, Japanese-style receipts, a forwarded
cancellation email), their ground truth, and claim bundles with planted errors.

Usage: python3 tools/make_docs.py OUT_DIR
Writes OUT_DIR/docs/*.jpg|*.txt, OUT_DIR/truth.json, OUT_DIR/bundles.jsonl. Everything is invented;
no real airline, hotel or person.
"""
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

JP = "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"
LATIN = "/System/Library/Fonts/Supplemental/Arial.ttf"
LATIN_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
MONO = "/System/Library/Fonts/Supplemental/Courier New.ttf"


def f(path, size):
    return ImageFont.truetype(path, size)


CONTEXT = {
    "traveler": "Jordan Taylor", "flight": "XX123", "route": "HND-LAX",
    "scheduled_departure": "2026-10-05T17:00+09:00", "replacement_departure": "2026-10-06T17:00+09:00",
    "plan": "Plus", "allowed_regions": ["Tokyo", "Chiba"],
}

# ---------- airline letters ----------

LETTER_BASE = {
    "passenger": "TAYLOR/JORDAN MR", "booking": "K7Q2LM", "flight": "XX123", "date": "2026-10-05",
    "route": "Tokyo Haneda (HND) - Los Angeles (LAX)", "scheduled": "17:00", "status": "CANCELLED",
    "actual": None, "reason": "Typhoon Hagi - adverse weather conditions", "provided": None,
    "rebooked": "XX123 on 06 OCT 2026, 17:00",
}
LETTERS = {
    "L_ok": {},
    "L_wrongflight": {"flight": "XX125"},
    "L_crew": {"reason": "Operational reasons - crew scheduling"},
    "L_hotelprovided": {"provided": "Hotel accommodation (Bayside Airport Hotel) and meal vouchers were provided by Kumo Air."},
    "L_delay3h": {"status": "DELAYED", "actual": "20:05 (delay 3 h 05 min)", "rebooked": None},
    "L_wrongname": {"passenger": "TAYLOR/MORGAN MS"},
}
REASON_CATEGORY = {"Typhoon": "weather", "Operational": "other"}


def letter_truth(spec):
    s = {**LETTER_BASE, **spec}
    return {"doc_type": "airline_letter", "flight": s["flight"], "date": s["date"], "passenger": s["passenger"],
            "status": s["status"].lower(), "delay_hours": 3.1 if s["status"] == "DELAYED" else None,
            "reason_category": REASON_CATEGORY[s["reason"].split()[0]],
            "airline_provided": ["accommodation", "meals"] if s["provided"] else []}


def draw_letter(spec):
    s = {**LETTER_BASE, **spec}
    img = Image.new("RGB", (1000, 1300), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1000, 110], fill=(20, 60, 120))
    d.text((50, 32), "KUMO AIR", font=f(LATIN_B, 44), fill="white")
    d.text((640, 48), "Flight Disruption Certificate", font=f(LATIN, 22), fill="white")
    y = 160
    d.text((50, y), "To whom it may concern,", font=f(LATIN, 22), fill="black"); y += 60
    rows = [("Passenger name", s["passenger"]), ("Booking reference", s["booking"]), ("Flight", s["flight"]),
            ("Date", "05 OCT 2026"), ("Route", s["route"]), ("Scheduled departure", s["scheduled"] + " local time"),
            ("Flight status", s["status"])]
    if s["actual"]:
        rows.append(("Actual departure", s["actual"]))
    rows.append(("Reason", s["reason"]))
    if s["rebooked"]:
        rows.append(("Rebooked on", s["rebooked"]))
    for k, v in rows:
        d.text((50, y), k, font=f(LATIN, 22), fill=(80, 80, 80))
        d.text((380, y), v, font=f(LATIN_B, 22), fill="black")
        y += 46
    y += 30
    body = ("We confirm that the above flight was disrupted for the reason stated. This certificate is issued "
            "at the passenger's request for insurance purposes.")
    for line in wrap(body, 80):
        d.text((50, y), line, font=f(LATIN, 21), fill="black"); y += 32
    if s["provided"]:
        y += 10
        for line in wrap(s["provided"], 80):
            d.text((50, y), line, font=f(LATIN, 21), fill="black"); y += 32
    y += 50
    d.text((50, y), "Issued: 05 OCT 2026, Tokyo Haneda Airport, Kumo Air Customer Service", font=f(LATIN, 20), fill="black")
    d.ellipse([720, y + 40, 900, y + 220], outline=(180, 30, 30), width=5)
    d.text((748, y + 115), "KUMO AIR HND", font=f(LATIN_B, 20), fill=(180, 30, 30))
    return img


def wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + [cur]


# ---------- receipts ----------

RECEIPTS = {
    "R_hotel": {"merchant": "ベイサイド エアポートホテル", "addr": "東京都大田区羽田空港 3-1", "city": "Ota",
                "dt": "2026-10-06 09:12", "items": [("宿泊料 1泊 (10/5)", 16500, "accommodation")]},
    "R_hotel_minibar": {"merchant": "ベイサイド エアポートホテル", "addr": "東京都大田区羽田空港 3-1", "city": "Ota",
                        "dt": "2026-10-06 09:14", "items": [("宿泊料 1泊 (10/5)", 16500, "accommodation"),
                                                           ("ミニバー ビール 2本", 1800, "minibar")]},
    "R_hotel_expensive": {"merchant": "グランド スカイ ホテル東京", "addr": "東京都港区芝浦 1-2", "city": "Tokyo",
                          "dt": "2026-10-06 10:02", "items": [("スイートルーム 1泊 (10/5)", 63000, "accommodation")]},
    "R_dinner": {"merchant": "らーめん 羽田亭", "addr": "東京都大田区羽田空港 2-6", "city": "Ota",
                 "dt": "2026-10-05 19:42", "items": [("醤油らーめん", 1100, "meal"), ("餃子", 480, "meal"),
                                                     ("烏龍茶", 300, "meal")]},
    "R_lunch_before": {"merchant": "カフェ ソラ", "addr": "東京都大田区羽田空港 2-6", "city": "Ota",
                       "dt": "2026-10-05 12:10", "items": [("サンドイッチセット", 1350, "meal")]},
    "R_dinner_after": {"merchant": "居酒屋 つばさ", "addr": "東京都大田区蒲田 5-3", "city": "Ota",
                       "dt": "2026-10-06 20:30", "items": [("定食", 1600, "meal"), ("ソフトドリンク", 800, "meal")]},
    "R_taxi": {"merchant": "東京エアタクシー", "addr": "東京都大田区", "city": "Ota",
               "dt": "2026-10-06 13:05", "items": [("運賃 ホテル→羽田空港 第3ターミナル", 3200, "local_transport")]},
    "R_osaka": {"merchant": "なにわ食堂", "addr": "大阪府大阪市北区梅田 1-1", "city": "Osaka",
                "dt": "2026-10-05 21:00", "items": [("日替わり定食", 2200, "meal")]},
    "R_conbini": {"merchant": "ファミリーストア 羽田空港店", "addr": "東京都大田区羽田空港 3-4", "city": "Ota",
                  "dt": "2026-10-05 22:15", "items": [("おにぎり", 180, "meal"), ("お茶", 160, "meal"),
                                                      ("プリペイドSIM 3GB", 3000, "phone_data")]},
}
RECEIPTS["R_dinner_dup"] = dict(RECEIPTS["R_dinner"])
RECEIPTS["R_blurry"] = dict(RECEIPTS["R_taxi"])


def receipt_truth(r):
    total = sum(p for _, p, _ in r["items"])
    return {"doc_type": "receipt", "merchant": r["merchant"], "city": r["city"],
            "prefecture": "Osaka" if r["addr"].startswith("大阪") else "Tokyo", "datetime": r["dt"],
            "currency": "JPY", "total": total, "categories": sorted({c for _, _, c in r["items"]})}


def draw_receipt(r):
    img = Image.new("RGB", (560, 900), (252, 252, 248))
    d = ImageDraw.Draw(img)
    y = 30
    d.text((280, y), "領 収 書", font=f(JP, 34), fill="black", anchor="mt"); y += 70
    d.text((280, y), r["merchant"], font=f(JP, 24), fill="black", anchor="mt"); y += 40
    d.text((280, y), r["addr"], font=f(JP, 17), fill="black", anchor="mt"); y += 30
    d.text((280, y), "TEL 03-0000-0000", font=f(MONO, 17), fill="black", anchor="mt"); y += 40
    date, time = r["dt"].split()
    yy, mm, dd = date.split("-")
    d.text((30, y), f"{yy}年{int(mm)}月{int(dd)}日 {time}", font=f(JP, 20), fill="black"); y += 40
    d.line([30, y, 530, y], fill="black", width=2); y += 20
    for name, price, _ in r["items"]:
        d.text((30, y), name, font=f(JP, 20), fill="black")
        d.text((530, y), f"¥{price:,}", font=f(JP, 20), fill="black", anchor="ra"); y += 36
    d.line([30, y, 530, y], fill="black", width=2); y += 20
    total = sum(p for _, p, _ in r["items"])
    d.text((30, y), "合計", font=f(JP, 26), fill="black")
    d.text((530, y), f"¥{total:,}", font=f(JP, 26), fill="black", anchor="ra"); y += 44
    d.text((30, y), f"(内消費税等 ¥{round(total / 11):,})", font=f(JP, 17), fill="black"); y += 36
    d.text((30, y), "お預り  クレジット", font=f(JP, 18), fill="black"); y += 50
    d.text((280, y), "ありがとうございました", font=f(JP, 18), fill="black", anchor="mt")
    return img


def photo(img, rng, blur=None):
    """Make a clean render look like a phone photo."""
    angle = rng.uniform(-4, 4)
    bg = Image.new("RGB", (img.width + 160, img.height + 160), tuple(rng.randint(90, 140) for _ in range(3)))
    bg.paste(img, (80, 80))
    out = bg.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(110, 110, 110))
    shade = Image.linear_gradient("L").resize(out.size).rotate(rng.uniform(0, 360))
    out = Image.composite(out, Image.new("RGB", out.size, (40, 40, 40)), shade.point(lambda v: 170 + v // 3))
    return out.filter(ImageFilter.GaussianBlur(blur if blur is not None else rng.uniform(0.4, 1.0)))


EMAIL = """---------- Forwarded message ---------
From: Kumo Air <noreply@kumo-air.example>
Date: Mon, 5 Oct 2026 at 15:41
Subject: Important: Flight XX123 on 05 OCT has been cancelled
To: <jordan.taylor@example.com>

Dear Jordan Taylor,

We regret to inform you that your flight XX123 from Tokyo Haneda (HND) to Los Angeles (LAX),
scheduled to depart on 05 October 2026 at 17:00, has been cancelled due to Typhoon Hagi.

Booking reference: K7Q2LM
New flight: XX123, 06 October 2026, departing 17:00

This email may be used as confirmation of the cancellation for insurance purposes.

Kumo Air Customer Service
"""
EMAIL_TRUTH = {"doc_type": "airline_letter", "flight": "XX123", "date": "2026-10-05", "passenger": "Jordan Taylor",
               "status": "cancelled", "delay_hours": None, "reason_category": "weather", "airline_provided": []}

# ---------- bundles: (doc ids, expected flags) ----------

BUNDLES = [
    ("B01", "clean", ["L_ok", "R_hotel", "R_dinner", "R_taxi", "R_conbini"], []),
    ("B02", "receipt before scheduled departure", ["L_ok", "R_hotel", "R_lunch_before"], ["out_of_window"]),
    ("B03", "receipt after replacement departure", ["L_ok", "R_hotel", "R_dinner_after"], ["out_of_window"]),
    ("B04", "letter flight number mismatch", ["L_wrongflight", "R_hotel"], ["flight_mismatch"]),
    ("B05", "cause not covered (crew)", ["L_crew", "R_hotel"], ["cause_not_covered"]),
    ("B06", "minibar on hotel bill", ["L_ok", "R_hotel_minibar"], ["excluded_item"]),
    ("B07", "airline already provided hotel", ["L_hotelprovided", "R_hotel"], ["airline_provided"]),
    ("B08", "duplicate receipt", ["L_ok", "R_dinner", "R_dinner_dup"], ["duplicate"]),
    ("B09", "hotel above daily limit", ["L_ok", "R_hotel_expensive"], ["over_daily_limit"]),
    ("B10", "unreadable receipt", ["L_ok", "R_blurry"], ["retake"]),
    ("B11", "delay below 4h", ["L_delay3h", "R_dinner"], ["delay_below_threshold"]),
    ("B12", "receipt from another city", ["L_ok", "R_osaka"], ["location_mismatch"]),
    ("B13", "clean, letter by forwarded email", ["E_ok", "R_hotel", "R_dinner"], []),
    ("B14", "passenger name mismatch", ["L_wrongname", "R_hotel"], ["name_mismatch"]),
    ("B15", "no airline letter", ["R_hotel", "R_dinner"], ["missing_airline_letter"]),
]


def main():
    out = Path(sys.argv[1])
    (out / "docs").mkdir(parents=True, exist_ok=True)
    rng = random.Random(3)
    truth = {}
    for doc_id, spec in LETTERS.items():
        photo(draw_letter(spec), rng).save(out / "docs" / f"{doc_id}.jpg", quality=78)
        truth[doc_id] = letter_truth(spec)
    for doc_id, r in RECEIPTS.items():
        blur = 6.0 if doc_id == "R_blurry" else None
        photo(draw_receipt(r), rng, blur).save(out / "docs" / f"{doc_id}.jpg", quality=75)
        truth[doc_id] = receipt_truth(r)
    truth["R_blurry"]["unreadable"] = True
    (out / "docs" / "E_ok.txt").write_text(EMAIL)
    truth["E_ok"] = EMAIL_TRUTH
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1))
    with open(out / "bundles.jsonl", "w") as fh:
        for bid, note, docs, flags in BUNDLES:
            fh.write(json.dumps({"id": bid, "note": note, "docs": docs, "expected_flags": flags}) + "\n")
    (out / "context.json").write_text(json.dumps(CONTEXT, indent=1))
    print(f"{len(truth)} documents, {len(BUNDLES)} bundles -> {out}")


if __name__ == "__main__":
    main()
