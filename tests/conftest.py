import json
from datetime import datetime, timezone

import pytest

from tripclaim.agent import ScriptedAgent
from tripclaim.app import build
from tripclaim.config import Settings
from tripclaim.llm import FakeLLM

PHONE = "+15551230001"
EMAIL = "jordan@example.com"

LETTER = {"doc_type": "airline_letter", "legibility": "good", "confidence": 0.9, "receipt": None, "ticket": None,
          "letter": {"flight": "XX123", "date": "2026-10-05", "passenger": "TAYLOR/JORDAN MR", "status": "cancelled",
                     "delay_hours": None, "reason_text": "Typhoon Hagi", "reason_category": "weather",
                     "airline_provided": [], "booking_ref": "K7Q2LM", "scheduled_departure": "17:00",
                     "rebooked_flight": "XX123", "rebooked_departure": "2026-10-06 17:00"}}


def receipt(merchant, dt, total, category="meal", prefecture="Tokyo"):
    return {"doc_type": "receipt", "legibility": "good", "confidence": 0.9, "letter": None, "ticket": None,
            "receipt": {"merchant": merchant, "city": "Ota", "prefecture": prefecture, "datetime": dt, "service_date": None,
                        "currency": "JPY", "total": total,
                        "line_items": [{"description": merchant, "amount": total, "category": category}]}}


BOARDING = {"doc_type": "boarding_pass", "legibility": "good", "confidence": 0.9, "letter": None, "receipt": None,
            "ticket": {"passenger": "TAYLOR/JORDAN", "flight": "XX123", "date": "2026-10-06", "departure_time": "17:00",
                       "booking_ref": "K7Q2LM"}}

DOCS = {b"LETTER": LETTER, b"HOTEL": receipt("Bayside Airport Hotel", "2026-10-06 09:12", 16500, "accommodation"),
        b"LUNCH": receipt("Cafe Sora", "2026-10-05 12:10", 1350), b"BOARDING": BOARDING}

COVERAGE = {"status": "likely_covered", "cited_clauses": ["[TD-1]", "TD-3"], "covered_expenses": ["hotel"],
            "not_covered_expenses": [], "checklist": [], "clarifying_question": None,
            "reply": "Tonight's hotel is a cost [TD-3] covers; the insurer decides the amount. Ask the counter for a cancellation letter now."}


def fake_extract(system, content):
    for c in content:
        if c["type"] == "image":
            return json.loads(json.dumps(DOCS[c["bytes"]]))
        if c["type"] == "text" and "Kumo Air" in c["text"]:
            return json.loads(json.dumps(LETTER))
    return {"doc_type": "other", "legibility": "good", "confidence": 0.9, "letter": None, "receipt": None, "ticket": None}


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def env():
    outbox, clock = [], Clock()
    llm = FakeLLM({"document": fake_extract, "coverage": COVERAGE})
    svc = build(Settings(store="memory", channels="fake", llm_backend="fake", public_base_url="https://demo.test"),
                outbox=outbox, llm=llm, agent=ScriptedAgent(), clock=clock)
    svc.register_traveler(
        {"id": "trv-1", "name": "Jordan Taylor", "phone": PHONE, "email": EMAIL, "preferred_channel": "whatsapp",
         "consent": {"whatsapp": True, "sms": True, "email": True}},
        {"id": "pol-1", "plan": "Plus", "purchased_at": "2026-09-20T10:00+09:00", "period_start": "2026-10-01",
         "period_end": "2026-10-07"},
        [{"flight": "XX123", "date": "2026-10-05", "route": "HND-LAX", "origin": "HND",
          "scheduled_departure": "2026-10-05T17:00+09:00"}])
    return {"svc": svc, "outbox": outbox, "clock": clock, "llm": llm}


EVENT = {"flight": "XX123", "date": "2026-10-05", "type": "cancelled", "reason": "Typhoon Hagi",
         "reason_category": "weather", "announced_at": "2026-10-03T09:00+09:00",
         "replacement_flight": "XX123 2026-10-06 17:00", "replacement_departure": "2026-10-06T17:00+09:00",
         "allowed_regions": ["Tokyo", "Chiba", "Kanagawa"]}
