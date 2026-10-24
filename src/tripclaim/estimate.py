"""Estimated claim amount from the receipts received: USD per receipt, daily and per-trip limits of the plan [PLAN-2].

Guidance for the traveler and a worksheet for the reviewer; the insurer decides the amount [CL-5].
Demo exchange rates ([TD-5] uses the rate on the expense date in production)."""
from collections import defaultdict

from .validate import COVERED_CATEGORIES, FX_TO_USD, PLAN_LIMITS_USD

KRW_PER_USD = 1380  # demo rate, for the traveler's sense of the amount
DISQUALIFY = {"retake", "out_of_window", "duplicate", "location_mismatch", "other_claim_document"}  # the whole receipt does not count (yet)


# Why a reviewer counted less of a receipt than the rules did (kept with the decision and shown to the traveler)
ADJUST_REASONS = ("excluded_item", "airline_paid", "other_insurance", "over_reasonable", "other")


def estimate(documents, plan, adjust=None):
    """adjust: {doc_id: {"usd": counted amount the reviewer accepts, "reason": one of ADJUST_REASONS}}."""
    adjust = adjust or {}
    limits = PLAN_LIMITS_USD.get(plan)
    rows, per_day = [], defaultdict(float)
    for d in documents:
        ex = d.get("extracted") or {}
        if ex.get("doc_type") != "receipt" or not ex.get("receipt"):
            continue
        r, codes = ex["receipt"], {f["code"] for f in d.get("flags", [])}
        rate = FX_TO_USD.get((r.get("currency") or "").upper())
        provided = " ".join(f.get("detail", "") for f in d.get("flags", []) if f["code"] == "airline_provided")
        counted = 0.0
        if not (codes & DISQUALIFY) and rate:
            for item in r.get("line_items") or []:
                if item.get("category") not in COVERED_CATEGORIES or not item.get("amount"):
                    continue
                if item.get("description") and item["description"] in provided:  # the airline already pays for it [TD-4](d)
                    continue
                counted += item["amount"] * rate
        a = adjust.get(d["id"])
        rule_counted = counted
        if a is not None and counted:
            counted = max(0.0, min(counted, float(a.get("usd") or 0)))
        day = r.get("service_date") or (r.get("datetime") or "")[:10]
        if counted:
            per_day[day] += counted
        paid = (r.get("datetime") or "")[:10]
        rows.append({"doc": d["id"], "merchant": r.get("merchant"), "date": day, "currency": r.get("currency"),
                     # a hotel counts on the night of the stay, not the checkout day it was paid
                     "paid": paid if paid and paid != day else None,
                     "per_usd": round(1 / rate, 2) if rate else None,  # "1 USD = 150 JPY"
                     "total": r.get("total"), "usd": round(r["total"] * rate, 2) if rate and r.get("total") else None,
                     "counted_usd": round(counted, 2), "excluded": sorted(codes & DISQUALIFY),
                     **({"rule_usd": round(rule_counted, 2), "reason": a.get("reason")} if a is not None and counted < rule_counted else {})})
    if not limits:
        return {"plan": plan, "covered": False, "rows": rows, "estimate_usd": 0, "estimate_krw": 0}
    daily, trip = limits
    days = {day: {"usd": round(v, 2), "capped": round(min(v, daily), 2)} for day, v in sorted(per_day.items())}
    est = round(min(sum(x["capped"] for x in days.values()), trip), 2)
    return {"plan": plan, "covered": True, "daily": daily, "trip": trip, "rows": rows, "days": days,
            "receipts_usd": round(sum(per_day.values()), 2), "estimate_usd": est, "remaining_usd": round(trip - est, 2),
            "estimate_krw": int(round(est * KRW_PER_USD, -3)), "krw_per_usd": KRW_PER_USD}
