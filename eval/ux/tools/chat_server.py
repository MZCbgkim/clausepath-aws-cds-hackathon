"""Local demo server for UX rounds: in-memory store, real agent, simulated clock (2026-10-05). Run from the ClausePath dir:
REVIEW_LOCALE=ko .venv/bin/python eval/ux/tools/chat_server.py
The model is the Claude Agent SDK on your own Claude login; LLM_BACKEND=bedrock runs it on Bedrock as deployed."""
import os
import sys
from datetime import datetime, timezone
sys.path.insert(0, "src")
from tripclaim import sim
from tripclaim.agent import make_agent
from tripclaim.app import build
from tripclaim.config import Settings
from tripclaim.llm import make_llm
s = Settings(store="memory", channels="fake", llm_backend=os.environ.get("LLM_BACKEND", "sdk"), public_base_url="http://localhost:8099",
             sim_phone=True)
svc = build(s, outbox=[], llm=make_llm(s), agent=make_agent(s),
            clock=sim.Clock(datetime(2026, 10, 5, 8, 20, tzinfo=timezone.utc)))
sim.seed(svc, "ko")
svc.repo.update_traveler("trv-jordan", preferred_channel="sms", consent={"sms": True})
[cid] = svc.on_disruption(dict(sim.EVENT))
open(os.path.join(os.path.dirname(__file__), "local_chat_link"), "w").write(svc.chat_link(cid))
sim.serve(svc, 8099)
