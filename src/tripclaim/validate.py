"""Deterministic claim checks over extracted documents (design §6.2).

The LLM only extracts; every flag here is a rule a reviewer can re-check by hand.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta

COVERED_CAUSES = {"weather", "natural_disaster", "mechanical", "airport_closure", "strike"}  # [TD-2]
COVERED_CATEGORIES = {"meal", "accommodation", "local_transport", "phone_data"}             # [TD-3]
EXCLUDED_CATEGORIES = {"alcohol", "minibar", "sightseeing", "shopping"}                     # [TD-4](a)
DELAY_THRESHOLD_H = 4                                                                        # [DEF-5]
PLAN_LIMITS_USD = {"Plus": (150, 500), "Premier": (250, 1000)}                               # [PLAN-2]
FX_TO_USD = {"USD": 1.0, "JPY": 1 / 150}  # demo rate; production uses the rate on the expense date [TD-5]
MIN_CONFIDENCE = 0.6
TITLES = {"MR", "MRS", "MS", "MISS", "DR"}


def _names(text):
    return {t for t in re.split(r"[^A-Z]+", (text or "").upper()) if t and t not in TITLES}


def _dt(text):
    """Model output is not trusted to be well-formed: anything that is not an ISO date-time is None."""
    try:
        return datetime.fromisoformat(text.strip().replace(" ", "T", 1)) if isinstance(text, str) and text.strip() else None
    except ValueError:
        return None


def validate(context, docs):
    """context: traveler, flight, scheduled_departure, replacement_departure, plan, allowed_regions
    (prefectures/states around the departure and replacement airports, e.g. Tokyo and Chiba for HND/NRT).
    docs: list of {"id", "extracted"}. Returns list of {"code", "doc", "detail"}."""
    flags = []
    flag = lambda code, doc=None, detail="": flags.append({"code": code, "doc": doc, "detail": detail})

    letters = [d for d in docs if d["extracted"]["doc_type"] == "airline_letter" and d["extracted"]["letter"]]
    receipts = [d for d in docs if d["extracted"]["doc_type"] == "receipt"]
    provided = set()

    if not letters:
        flag("missing_airline_letter", detail="[CL-1](a)")
    for d in letters:
        l = d["extracted"]["letter"]
        if (l["flight"] or "").replace(" ", "").upper() != context["flight"]:
            flag("flight_mismatch", d["id"], f"{l['flight']} vs {context['flight']}")
        expected = _names(context["traveler"])
        if not expected:  # e.g. only a Hangul name on file: cannot compare with the romanised name on airline documents
            flag("name_unverified", d["id"], f"{l['passenger']}; no passport (Latin) name on file")
        elif not expected <= _names(l["passenger"]):
            flag("name_mismatch", d["id"], f"{l['passenger']} vs {context['traveler']}")
        if l["status"] == "departed" or (l["status"] == "delayed" and (l["delay_hours"] or 0) < DELAY_THRESHOLD_H):
            flag("delay_below_threshold", d["id"], f"{l['status']} {l['delay_hours']}h [DEF-5]")
        if l["reason_category"] not in COVERED_CAUSES:
            flag("cause_not_covered", d["id"], f"{l['reason_category']}: {l['reason_text']} [TD-2]")
        provided |= set(l["airline_provided"])

    # tickets and boarding passes must be the traveler's too (a companion's pass is not proof of this claim)
    for d in docs:
        t = d["extracted"].get("ticket") or {}
        if d["extracted"]["doc_type"] not in ("eticket", "boarding_pass") or not t.get("passenger"):
            continue
        expected = _names(context["traveler"])
        if expected and not expected <= _names(t["passenger"]):
            flag("name_mismatch", d["id"], f"{t['passenger']} vs {context['traveler']}")

    start, end = _dt(context["scheduled_departure"]), _dt(context["replacement_departure"])
    if start and (not end or end.tzinfo is None or end < start):
        end = start + timedelta(hours=48)
    tz = start.tzinfo if start else None
    seen = {}
    per_day = defaultdict(float)
    allowed = {c.lower() for c in context["allowed_regions"]}
    for d in receipts:
        e = d["extracted"]
        r = e["receipt"]
        if e["legibility"] == "unreadable" or e["confidence"] < MIN_CONFIDENCE or not r or not r["datetime"]:
            flag("retake", d["id"], f"legibility {e['legibility']}, confidence {e['confidence']}")
            continue
        when = _dt(r["datetime"])
        if when is None:
            flag("retake", d["id"], f"date/time not readable: {r['datetime']!r}")
            continue
        when = when.replace(tzinfo=tz) if when.tzinfo is None else when
        if start and not start <= when <= end:
            flag("out_of_window", d["id"], f"{r['datetime']} outside delay window [TD-4](e)")
            continue
        key = ((r["merchant"] or "").strip(), r["datetime"], r["total"])
        if key in seen:
            flag("duplicate", d["id"], f"same as {seen[key]}")
            continue
        seen[key] = d["id"]
        if r["prefecture"] and r["prefecture"].lower() not in allowed:
            flag("location_mismatch", d["id"], f"{r['city']}, {r['prefecture']} not near the airports")
        rate = FX_TO_USD.get((r["currency"] or "").upper())
        day = r["service_date"] or r["datetime"][:10]
        for item in r["line_items"]:
            if item["category"] in EXCLUDED_CATEGORIES:
                flag("excluded_item", d["id"], f"{item['description']} ({item['category']}) [TD-4](a)")
            elif item["category"] == "accommodation" and "accommodation" in provided:
                flag("airline_provided", d["id"], f"{item['description']}: airline provided accommodation [TD-4](d)")
            elif item["category"] == "meal" and "meals" in provided:
                flag("airline_provided", d["id"], f"{item['description']}: airline provided meals [TD-4](d)")
            elif item["category"] in COVERED_CATEGORIES and rate and item["amount"]:
                per_day[day] += item["amount"] * rate

    limits = PLAN_LIMITS_USD.get(context["plan"])
    if limits:
        daily, trip = limits
        for day, usd in sorted(per_day.items()):
            if usd > daily:
                flag("over_daily_limit", None, f"{day}: USD {usd:.0f} > {daily} [PLAN-2]")
        if sum(per_day.values()) > trip:
            flag("over_trip_limit", None, f"USD {sum(per_day.values()):.0f} > {trip} [PLAN-2]")
    return flags
