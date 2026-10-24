"""Seed the demo traveler into the deployed stack and (optionally) fire the typhoon cancellation.

  TABLE_NAME=... TRIPCLAIM_STORE=dynamodb python -m tripclaim.seed --phone +15551230001 --email you@example.com
  python -m tripclaim.seed --event --bus tripclaim-flights        # puts FlightDisruption on EventBridge

Phone and email must be the demo devices you registered (WhatsApp test number, RCS test device, SES identity).
Dates are relative to today (flight today 17:00 JST, replacement tomorrow) so the live demo is always inside the cover.
"""
import argparse
import json
from datetime import date, datetime, timedelta, timezone

import boto3

from .config import settings
from .store import repository


def dates(today):
    """Demo dates relative to today (JST), so the policy period and delay window are always current."""
    d = lambda n: (today + timedelta(days=n)).isoformat()
    return {"flight": d(0), "replacement": d(1), "period_start": d(-4), "period_end": d(2),
            "purchased": f"{d(-18)}T10:00+09:00", "announced": f"{d(-2)}T09:00+09:00"}


def event(today, depart="17:00"):
    """depart: the replacement flight's time the next day (a demo recorded late in the day moves it later)."""
    x = dates(today)
    return {"flight": "XX123", "date": x["flight"], "type": "cancelled", "reason": "Typhoon Hagi",
            "reason_category": "weather", "announced_at": x["announced"],
            "replacement_flight": f"XX123 {x['replacement']} {depart}", "replacement_departure": f"{x['replacement']}T{depart}+09:00",
            "allowed_regions": ["Tokyo", "Chiba", "Kanagawa"]}


# Same typhoon, different policies: what the demo shows side by side. Offsets are days from today.
PERSONAS = [
    {"key": "plus", "name": "김지수", "passport": "KIM/JISOO MS", "lang": "ko", "plan": "Plus", "bought": -18, "period": (-4, 2),
     "expect": "플러스, 2주 전 가입 → [TD-3] 보상하는 비용에 해당, 하루 한도 150달러 [PLAN-2]"},
    {"key": "premier", "name": "박서연", "passport": "PARK/SEOYEON MS", "lang": "ko", "plan": "Premier", "bought": -18, "period": (-4, 2),
     "expect": "프리미어 → 하루 한도 250달러 [PLAN-2]"},
    {"key": "basic", "name": "이민호", "passport": "LEE/MINHO MR", "lang": "ko", "plan": "Basic", "bought": -18, "period": (-4, 2),
     "expect": "베이직 → 지연 담보 없음 [PLAN-1]"},
    {"key": "late", "name": "최유나", "passport": "CHOI/YUNA MS", "lang": "ko", "plan": "Plus", "bought": -1, "period": (-4, 2),
     "expect": "태풍 특보 다음 날 가입 → 알려진 사건 제외 조항 [EX-1]"},
    {"key": "extended", "name": "정우진", "passport": "JUNG/WOOJIN MR", "lang": "ko", "plan": "Plus", "bought": -18, "period": (-7, -1),
     "expect": "보험기간을 넘겨 여행 연장 → 보험기간 밖 제외 조항 [EX-3]"},
    {"key": "lastday", "name": "강민준", "passport": "KANG/MINJUN MR", "lang": "ko", "plan": "Plus", "bought": -18, "period": (-6, 0),
     "expect": "보험 마지막 날 결항 → 보험기간 자동 연장 [TD-6], 한도 [PLAN-2]"},
    {"key": "jordan", "name": "Jordan Taylor", "passport": "TAYLOR/JORDAN MR", "lang": "en", "plan": "Plus", "bought": -18, "period": (-4, 2),
     "expect": "Plus, English → hotel is a listed expense [TD-3], USD 150/day limit [PLAN-2]"},
]


def persona_records(p, today):
    d = lambda n: (today + timedelta(days=n)).isoformat()
    tid = f"trv-persona-{p['key']}"
    traveler = {"id": tid, "name": p["name"], "passport_name": p["passport"], "locale": p["lang"],
                "locale_source": "explicit", "preferred_channel": "web", "consent": {}}
    policy = {"id": f"pol-persona-{p['key']}", "plan": p["plan"], "purchased_at": f"{d(p['bought'])}T10:00+09:00",
              "period_start": d(p["period"][0]), "period_end": d(p["period"][1])}
    segment = {"flight": "XX123", "date": d(0), "route": "HND-LAX", "origin": "HND", "scheduled_departure": f"{d(0)}T17:00+09:00"}
    return traveler, policy, segment


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone")
    ap.add_argument("--email")
    ap.add_argument("--name", default="Jordan Taylor")
    ap.add_argument("--event", action="store_true")
    ap.add_argument("--bus", default="tripclaim-flights")
    ap.add_argument("--lang", choices=["en", "ko"], default="en")
    ap.add_argument("--passport-name", help="romanised name on airline documents, e.g. KIM/JISOO MS")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in Japan)")
    args = ap.parse_args()
    s = settings()
    today = date.fromisoformat(args.today) if args.today else datetime.now(timezone(timedelta(hours=9))).date()
    x = dates(today)
    if args.phone or args.email:
        repo = repository(s)
        repo.put_traveler({"id": "trv-demo", "name": args.name, "phone": args.phone, "email": args.email,
                           "locale": args.lang, "passport_name": args.passport_name, "preferred_channel": "whatsapp",
                           "consent": {"whatsapp": bool(args.phone), "sms": bool(args.phone), "email": bool(args.email)}})
        repo.put_policy("trv-demo", {"id": "pol-demo", "plan": "Plus", "purchased_at": x["purchased"],
                                     "period_start": x["period_start"], "period_end": x["period_end"]})
        repo.put_segment("trv-demo", {"flight": "XX123", "date": x["flight"], "route": "HND-LAX", "origin": "HND",
                                      "scheduled_departure": f"{x['flight']}T17:00+09:00"})
        print("seeded traveler trv-demo")
    if args.event:
        res = boto3.client("events", region_name=s.region).put_events(Entries=[{
            "Source": "tripclaim.flights", "DetailType": "FlightDisruption", "Detail": json.dumps(event(today)),
            "EventBusName": args.bus}])
        print("event sent", res["Entries"][0].get("EventId"))


if __name__ == "__main__":
    main()
