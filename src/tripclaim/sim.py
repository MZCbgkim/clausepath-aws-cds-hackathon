"""Run the demo storyline end to end on one machine: fake channels, in-memory store, real model.

  python -m tripclaim.sim                 # Claude Agent SDK on your own Claude login (agent + extraction)
  python -m tripclaim.sim --llm bedrock   # Bedrock, as deployed (Strands agent + Converse extraction)
  python -m tripclaim.sim --llm cli       # subscription `claude -p` for extraction/coverage, scripted agent
  python -m tripclaim.sim --serve 8080    # then serve the reviewer dashboard on the same data

Prints the conversation with the channel each message went out on and why, and writes the claim packet
to demo/out/packet.html.
"""
import argparse
import base64
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .agent import ScriptedAgent, StrandsAgent
from .app import build
from .config import Settings
from .inbound import DeliveryStatus, Inbound, parse_email
from .llm import BedrockLLM, CliLLM

DEMO = Path(__file__).resolve().parents[2] / "demo"
ASSETS = DEMO / "assets"  # replaced by demo/assets_ko for the Korean storyline
OUT = Path(__file__).resolve().parents[2] / "demo" / "out"
PHONE, EMAIL = "+15551230001", "jordan.taylor@example.com"
EVENT = {"flight": "XX123", "date": "2026-10-05", "type": "cancelled", "reason": "Typhoon Hagi",
         "reason_category": "weather", "announced_at": "2026-10-03T09:00+09:00",
         "replacement_flight": "XX123 2026-10-06 17:00", "replacement_departure": "2026-10-06T17:00+09:00",
         "allowed_regions": ["Tokyo", "Chiba", "Kanagawa"]}
BADGE = {"whatsapp": "WhatsApp", "rcs": "RCS", "sms": "SMS", "email": "Email", None: "—"}

STORY = {
    "en": {"name": "Jordan Taylor", "passport": "TAYLOR/JORDAN MR", "assets": "assets", "who": "Jordan",
           "yes": "YES. They say the next flight is tomorrow. Is a hotel tonight covered?",
           "letter": "Got this at the counter", "hotel": "Hotel receipt", "again": "Sorry, here it is again",
           "dinner": "Dinner and a SIM card because my roaming stopped",
           "rcs": "Ok thanks. Do I need anything else at the airport tomorrow?", "board": "Boarding now",
           "else": "What else do you need from me?", "submit": "SUBMIT",
           "approve": "Hotel, dinner and SIM receipts checked against the TD-3 limits."},
    "ko": {"name": "김지수", "passport": "KIM/JISOO MS", "assets": "assets_ko", "who": "김지수",
           "yes": "네. 다음 비행기가 내일이래요. 오늘 밤 호텔비도 보상되나요?",
           "letter": "카운터에서 받았어요", "hotel": "호텔 영수증이요", "again": "다시 찍었어요",
           "dinner": "저녁이랑, 로밍이 끊겨서 산 유심 영수증이에요",
           "rcs": "알겠어요. 내일 공항에서 더 챙길 게 있나요?", "board": "지금 탑승해요",
           "else": "더 필요한 게 있나요?", "submit": "제출",
           "approve": "호텔·저녁·유심 영수증을 TD-3 한도와 대조했어요."},
}


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


def seed(svc, lang="en"):
    svc.register_traveler(
        {"id": "trv-jordan", "name": STORY[lang]["name"], "passport_name": STORY[lang]["passport"], "locale": lang,
         "phone": PHONE, "email": EMAIL,
         "preferred_channel": "whatsapp",
         "consent": {"whatsapp": True, "sms": True, "email": True}},
        {"id": "pol-8841", "plan": "Plus", "purchased_at": "2026-09-20T10:00+09:00", "period_start": "2026-10-01",
         "period_end": "2026-10-07"},
        [{"flight": "XX123", "date": "2026-10-05", "route": "HND-LAX", "origin": "HND",
          "scheduled_departure": "2026-10-05T17:00+09:00"}])


def media(svc, filename):
    mid = uuid.uuid4().hex[:10]
    svc.channels["whatsapp"].media[mid] = (ASSETS / filename).read_bytes()
    return [{"media_id": mid, "mime": "image/jpeg", "filename": filename}]


def say(who, channel, text):
    print(f"\n[{BADGE.get(channel, channel)}] {who}: {text}")


def run(svc, outbox, clock, lang="en"):
    st = STORY[lang]
    seen = 0

    def flush():
        nonlocal seen
        for m in outbox[seen:]:
            to = "reviewer" if m["to"] == svc.s.reviewer_email else st["who"]
            text = m["text"] if m["kind"] != "email" or to == st["who"] else f"(claim packet, {len(m.get('attachments', []))} attachments)"
            say(f"ClausePath -> {to}", m["channel"], (text[:600] + ("…" if len(text) > 600 else "")))
            msgs = [x for x in svc.repo.messages(svc.repo.list_claims()[0]["id"]) if x.get("provider_id") == m["provider_id"]]
            if msgs and msgs[0].get("chosen_reason"):
                print(f"      why this channel: {msgs[0]['chosen_reason']}")
        seen = len(outbox)

    def wa(text, files=()):
        say(st["who"], "whatsapp", text + (f"  [photo: {', '.join(files)}]" if files else ""))
        m = [x for f in files for x in media(svc, f)]
        svc.handle_inbound(Inbound("whatsapp", PHONE, text, m, uuid.uuid4().hex))
        flush()

    print("== 17:20 JST, Haneda. Typhoon Hagi: Kumo Air XX123 to Los Angeles is cancelled ==")
    svc.on_disruption(dict(EVENT))
    flush()
    clock.now += timedelta(minutes=4)
    wa(st["yes"])
    clock.now += timedelta(minutes=10)
    wa(st["letter"], ["airline_letter.jpg"])
    clock.now += timedelta(hours=2)
    wa(st["hotel"], ["hotel_receipt_blurry.jpg"])
    wa(st["again"], ["hotel_receipt.jpg"])
    clock.now += timedelta(minutes=30)
    wa(st["dinner"], ["dinner_receipt.jpg", "conbini_receipt.jpg"])

    print("\n== Hotel Wi-Fi drops: the last WhatsApp message is reported undelivered ==")
    last = [m for m in outbox if m["channel"] == "whatsapp"][-1]
    svc.handle_status(DeliveryStatus("whatsapp", last["provider_id"], "failed", "Message undeliverable"))
    flush()
    clock.now += timedelta(minutes=5)
    say(st["who"], "rcs", st["rcs"])
    svc.handle_inbound(Inbound("rcs", PHONE, st["rcs"], [], uuid.uuid4().hex))
    flush()

    print("\n== Next day, boarding ==")
    clock.now += timedelta(hours=20)
    wa(st["board"], ["boarding_pass.jpg"])

    print(f"\n== Home, two days later: {st['who']} forwards Kumo Air's cancellation email ==")
    clock.now += timedelta(days=2)
    inb = parse_email((ASSETS / "airline_cancellation.eml").read_bytes())
    say(st["who"], "email", f"{inb.subject}")
    svc.handle_inbound(inb)
    flush()
    wa(st["else"])

    claim = svc.repo.list_claims()[0]
    print(f"\n== {st['who']} fills in the secure claims page (claim form, ID, bank details) ==")
    svc.secure_submit(svc.secure_link(claim["id"]).rsplit("/", 1)[1], {"signed_name": st["name"], "holder": st["name"], "country": "kr", "bank_name": "KB국민은행", "account": "123-456-7890"})
    flush()
    wa(st["submit"])

    print("\n== Reviewer records the decision on the dashboard ==")
    open_checks = svc.unresolved(claim["id"])  # a model may flag a receipt to check; the dashboard then asks why
    if open_checks:
        print(f"open checks: {open_checks}")
    svc.reviewer_action(claim["id"], "reviewer@insurer.example", "approve", st["approve"],
                        reason="checked against the documents in the packet" if open_checks else "")
    flush()

    claim = svc.repo.get_claim(claim["id"])
    OUT.mkdir(exist_ok=True)
    if claim.get("packet_html"):
        (OUT / "packet.html").write_text(claim["packet_html"])
    print(f"\nclaim {claim['id']}: status {claim['status']}, checklist {sum(claim['checklist'].values())}/6, "
          f"flags {sorted({f['code'] for f in claim['flags']})}")
    print(f"channels used: {sorted({m['channel'] for m in outbox})}; packet -> {OUT / 'packet.html'}")
    return claim


def serve(svc, port):
    from .handlers import api
    api.SERVICE = svc

    class H(BaseHTTPRequestHandler):
        def _go(self, method):
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n).decode() if n else ""
            url = urlsplit(self.path)
            res = api.handler({"requestContext": {"http": {"method": method}}, "rawPath": url.path,
                               "queryStringParameters": dict(parse_qsl(url.query)), "body": body,
                               "headers": {k.lower(): v for k, v in self.headers.items()}}, None)
            self.send_response(res["statusCode"])
            for k, v in res["headers"].items():
                self.send_header(k, v)
            self.end_headers()
            body = res.get("body") or ""
            self.wfile.write(base64.b64decode(body) if res.get("isBase64Encoded") else body.encode())

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

        def log_message(self, *a):
            pass

    print(f"dashboard: http://localhost:{port}/")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", choices=["sdk", "bedrock", "cli"], default="sdk")
    ap.add_argument("--serve", type=int, help="serve the dashboard on this port after the run")
    ap.add_argument("--no-run", action="store_true", help="only seed data and serve")
    ap.add_argument("--lang", choices=["en", "ko"], default="en", help="traveler language for the storyline")
    args = ap.parse_args(argv)

    s = Settings(store="memory", channels="fake", llm_backend=args.llm, public_base_url=f"http://localhost:{args.serve or 8080}",
                 reviewer_email="claims-review@insurer.example")
    clock = Clock(datetime(2026, 10, 5, 8, 20, tzinfo=timezone.utc))
    outbox = []
    if args.llm == "sdk":
        from .sdk_local import SdkAgent, SdkLLM
        llm, agent = SdkLLM(s.model_id), SdkAgent(s.model_id)
    elif args.llm == "bedrock":
        llm, agent = BedrockLLM(s.model_id, s.region), StrandsAgent(s.model_id, s.region)
    else:
        llm, agent = CliLLM(), ScriptedAgent()
    svc = build(s, outbox=outbox, llm=llm, agent=agent, clock=clock)
    global ASSETS
    ASSETS = DEMO / STORY[args.lang]["assets"]
    seed(svc, args.lang)
    if not args.no_run:
        run(svc, outbox, clock, args.lang)
    if args.serve:
        serve(svc, args.serve)


if __name__ == "__main__":
    sys.exit(main())
