"""Build the demo's synthetic documents (fictional Kumo Air, invented hotel and shops).

Usage: python3 demo/make_assets.py                     -> demo/assets/* (flight on 2026-10-05, as in the simulator)
       python3 demo/make_assets.py --date 2026-10-20   -> demo/assets_2026-10-20/* (the same documents, re-dated)

The deployed demo puts the cancelled flight on today's date (Japan), so a recording needs documents dated that day:
the receipts, the airline letter, the boarding pass for the next day's flight and the forwarded email.
"""
import argparse
import random
import sys
import types
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "eval" / "tools"))
import make_docs as md  # noqa: E402

SHIFT = {"day": "05", "next": "06OCT26", "depart": "17:00"}  # the replacement flight on the boarding pass


def redate(day, depart=None, checkout=None):
    """make_docs with every date moved: the flight on `day`, the replacement flight and checkout the day after.
    depart / checkout ("HH:MM"): the replacement flight's time and the hotel checkout on the next day, so a recording can
    be made at any hour (the replacement flight still ahead, the checkout already past)."""
    global md
    nxt = day + timedelta(days=1)
    times = []
    if depart:
        times += [("2026-10-06T17:00", f"2026-10-06T{depart}"), ("06 OCT 2026, 17:00", f"06 OCT 2026, {depart}"),
                  ("06 October 2026, departing 17:00", f"06 October 2026, departing {depart}")]
        SHIFT["depart"] = depart
    if checkout:
        times += [("2026-10-06 09:12", f"2026-10-06 {checkout}"), ("2026-10-06 09:14", f"2026-10-06 {checkout}")]
    mon = lambda d: d.strftime("%b").upper()
    pairs = [("Mon, 5 Oct 2026", f"{day:%a}, {day.day} {day:%b} {day.year}"),
             ("05 October 2026", f"{day:%d} {day:%B} {day.year}"), ("06 October 2026", f"{nxt:%d} {nxt:%B} {nxt.year}"),
             ("05 OCT 2026", f"{day:%d} {mon(day)} {day.year}"), ("06 OCT 2026", f"{nxt:%d} {mon(nxt)} {nxt.year}"),
             ("05 OCT", f"{day:%d} {mon(day)}"), ("2026-10-05", day.isoformat()), ("2026-10-06", nxt.isoformat()),
             ("(10/5)", f"({day.month}/{day.day})")]
    src = Path(md.__file__).read_text(encoding="utf-8")
    for old, new in times + pairs:  # times first: they name the original dates
        src = src.replace(old, new)
    mod = types.ModuleType("make_docs_redated")
    mod.__file__ = md.__file__
    exec(compile(src, md.__file__, "exec"), mod.__dict__)
    md = mod
    SHIFT["next"] = f"{nxt:%d}{mon(nxt)}{nxt:%y}"
    return pairs

PEOPLE = {
    "en": {"dir": "assets", "passport": "TAYLOR/JORDAN MR", "name": "Jordan Taylor"},
    "ko": {"dir": "assets_ko", "passport": "KIM/JISOO MS", "name": "Jisoo Kim"},
}


def boarding_pass(passport):
    img = Image.new("RGB", (1100, 460), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1100, 80], fill=(20, 60, 120))
    d.text((40, 20), "KUMO AIR", font=md.f(md.LATIN_B, 36), fill="white")
    d.text((780, 30), "BOARDING PASS", font=md.f(md.LATIN_B, 24), fill="white")
    rows = [("PASSENGER", passport, 40, 110), ("FLIGHT", "XX123", 40, 200), ("DATE", SHIFT["next"], 260, 200),
            ("FROM", "TOKYO HND", 480, 200), ("TO", "LOS ANGELES LAX", 700, 200), ("DEPARTS", SHIFT["depart"], 40, 290),
            ("GATE", "108", 260, 290), ("SEAT", "34K", 480, 290), ("BOOKING", "K7Q2LM", 700, 290)]
    for label, value, x, y in rows:
        d.text((x, y), label, font=md.f(md.LATIN, 18), fill=(110, 110, 110))
        d.text((x, y + 26), value, font=md.f(md.LATIN_B, 30), fill="black")
    for i in range(60):
        d.rectangle([40 + i * 16, 390, 40 + i * 16 + (i * 7 % 9) + 2, 440], fill="black")
    return img


def airline_email(name):
    m = EmailMessage()
    m["From"] = f"{name} <jordan.taylor@example.com>"  # the demo traveler's registered address in the simulator
    m["To"] = "claim@claims.example.com"
    m["Subject"] = "Fwd: " + md.EMAIL.split("Subject: ", 1)[1].split("\n", 1)[0]
    m.set_content(md.EMAIL.replace("Jordan Taylor", name).replace("jordan.taylor@example.com", "traveler@example.com"))
    return m.as_bytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="the cancelled flight's date, YYYY-MM-DD (default: 2026-10-05)")
    ap.add_argument("--depart", help="the replacement flight's time the next day, HH:MM (default 17:00)")
    ap.add_argument("--checkout", help="the hotel checkout time the next day, HH:MM (default 09:12)")
    args = ap.parse_args()
    suffix = ""
    if args.date:
        redate(date.fromisoformat(args.date), args.depart, args.checkout)
        suffix = f"_{args.date}" + (f"_{args.depart.replace(':', '')}" if args.depart else "")
    for person in PEOPLE.values():
        build(person, suffix)


def build(person, suffix=""):
    OUT = HERE / (person["dir"] + suffix)
    OUT.mkdir(exist_ok=True)
    rng = random.Random(11)
    md.photo(md.draw_letter({"passenger": person["passport"]}), rng).save(OUT / "airline_letter.jpg", quality=80)
    md.photo(md.draw_receipt(md.RECEIPTS["R_hotel"]), rng).save(OUT / "hotel_receipt.jpg", quality=80)
    md.photo(md.draw_receipt(md.RECEIPTS["R_hotel"]), rng, blur=6.0).save(OUT / "hotel_receipt_blurry.jpg", quality=70)
    # the hotel bill with a minibar line: the demo's receipt (the minibar is left out under [TD-4](a))
    md.photo(md.draw_receipt(md.RECEIPTS["R_hotel_minibar"]), rng).save(OUT / "hotel_minibar_receipt.jpg", quality=80)
    md.photo(md.draw_receipt(md.RECEIPTS["R_hotel_minibar"]), rng, blur=6.0).save(OUT / "hotel_minibar_receipt_blurry.jpg", quality=70)
    md.photo(md.draw_receipt(md.RECEIPTS["R_dinner"]), rng).save(OUT / "dinner_receipt.jpg", quality=80)
    md.photo(md.draw_receipt(md.RECEIPTS["R_conbini"]), rng).save(OUT / "conbini_receipt.jpg", quality=80)
    md.photo(md.draw_receipt(md.RECEIPTS["R_lunch_before"]), rng).save(OUT / "lunch_before_receipt.jpg", quality=80)
    md.photo(boarding_pass(person["passport"]), rng).save(OUT / "boarding_pass.jpg", quality=80)
    (OUT / "airline_cancellation.eml").write_bytes(airline_email(person["name"]))
    print("demo assets ->", OUT)


if __name__ == "__main__":
    main()
