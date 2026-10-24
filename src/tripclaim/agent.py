"""The conversation agent: Strands Agents on Amazon Bedrock, runnable in-process or on AgentCore Runtime.

The agent writes the reply and decides when to call tools; facts come from the service (extraction,
deterministic checks, checklist) through the CONTEXT block and tools, so the agent cannot invent them.
"""
import json
from importlib import resources

from . import policy

SYSTEM = resources.files("tripclaim.prompts").joinpath("agent.md").read_text() + policy.policy_text()
HISTORY_TURNS = 12


def history(messages):
    """Stored messages -> alternating Strands messages (user first), last HISTORY_TURNS."""
    out = []
    for m in messages[-HISTORY_TURNS:]:
        if m.get("kind") == "undelivered" or not m.get("text"):
            continue
        role = "user" if m["direction"] == "in" else "assistant"
        if out and out[-1]["role"] == role:
            out[-1]["content"][0]["text"] += "\n" + m["text"]
        else:
            out.append({"role": role, "content": [{"text": m["text"]}]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if out and out[-1]["role"] == "user":  # the current message is sent separately
        out.pop()
    return out


def turn_prompt(text, context):
    return f"{text}\n\nCONTEXT\n{json.dumps(context, ensure_ascii=False, indent=1)}"


class StrandsAgent:
    """In-process agent (Lambda or AgentCore Runtime)."""

    def __init__(self, model_id, region, effort="medium"):
        self.model_id, self.region, self.effort = model_id, region, effort

    def run(self, service, claim_id, text, context):
        from strands import Agent, tool
        from strands.models import BedrockModel

        @tool
        def assess_coverage(question: str) -> dict:
            """Check the policy for this traveler's situation. Returns status, clause IDs relied on,
            covered and not-covered expenses, and the documents still needed."""
            service.progress(claim_id)
            return service.assess_coverage(claim_id, question)

        @tool
        def search_documents(query: str) -> dict:
            """Search the policy PDF and the FAQ PDF (traveler's language first). Returns the top policy clauses
            and FAQ answers with ids, page and text. Use it to find the passage to show the traveler."""
            return service.search_documents(claim_id, query)

        @tool
        def show_evidence(chunk_id: str, caption: str) -> dict:
            """Attach a source to your web reply: one passage (an id from search_documents), highlighted in the
            original PDF. Returns its reference number n; write [n] in your reply right after what it supports.
            caption: one short line in the traveler's language saying what to look at. Up to 3 per reply."""
            return service.evidence_panel(claim_id, "show", chunk_id, caption)

        @tool
        def minimize_evidence() -> dict:
            """Fold the open source card to one line (it stays in the chat), e.g. when the traveler starts sending
            documents or the topic has moved on."""
            return service.evidence_panel(claim_id, "minimize")

        @tool
        def secure_link() -> str:
            """Link to the secure claims page for the claim form, photo ID and bank details."""
            return service.secure_link(claim_id)

        @tool
        def submit_claim() -> dict:
            """Submit the claim to the insurer. Only after the traveler confirmed and the checklist is complete."""
            return service.submit_claim(claim_id, actor="agent")

        from .llm import bedrock_retry_config
        model = BedrockModel(model_id=self.model_id, region_name=self.region, max_tokens=4000, boto_client_config=bedrock_retry_config(),
                             additional_args={"outputConfig": {"effort": self.effort}})  # same effort as the evals
        agent = Agent(model=model, system_prompt=[{"text": SYSTEM}, {"cachePoint": {"type": "default"}}],
                      messages=history(service.repo.messages(claim_id)),
                      tools=[assess_coverage, search_documents, show_evidence, minimize_evidence,
                             secure_link, submit_claim], callback_handler=None)
        return str(agent(turn_prompt(text, context))).strip()


class RemoteAgent:
    """Same agent hosted on Amazon Bedrock AgentCore Runtime (handlers/agentcore_app.py)."""

    def __init__(self, runtime_arn, region):
        import boto3
        self.arn = runtime_arn
        self.client = boto3.client("bedrock-agentcore", region_name=region)

    def run(self, service, claim_id, text, context):
        session = (claim_id + "-" + "0" * 40)[:40]  # runtime session ids must be >= 33 chars
        res = self.client.invoke_agent_runtime(agentRuntimeArn=self.arn, runtimeSessionId=session,
                                               payload=json.dumps({"claim_id": claim_id, "text": text, "context": context}))
        body = res["response"].read()
        return json.loads(body)["reply"]


class ScriptedAgent:
    """Deterministic stand-in for tests and offline demos. Uses the same tools, no model calls."""

    def run(self, service, claim_id, text, context):
        from .i18n import SUBMIT_WORDS, explicit_switch, label, t
        lc = context.get("locale", "en")
        lower = text.lower().strip()
        missing = context["checklist"]["missing"]
        new_docs = context.get("new_documents", [])
        if explicit_switch(text):
            return t(lc, "language_switched")
        if any(w in lower for w in ("bank", "account number", "passport", "계좌", "여권")):
            return t(lc, "dont_send", link=service.secure_link(claim_id))
        if new_docs:
            service.evidence_panel(claim_id, "minimize")
            parts = [f"{d['summary']}: {d['problems'][0]}" if d["problems"] else t(lc, "got_it", summary=d["summary"])
                     for d in new_docs]
            nxt = t(lc, "next", item=missing[0]) if missing else ""
            if context["checklist"]["complete"]:
                nxt = t(lc, "complete_submit_web" if context.get("channel") == "web" else "complete_submit")
            return " ".join(parts) + nxt
        if (lower in SUBMIT_WORDS or "submit" in lower) and context["checklist"]["complete"]:
            res = service.submit_claim(claim_id, actor="agent")
            return t(lc, "submitted", claim=res["claim_id"])
        if any(w in lower for w in ("cover", "claim", "paid", "pay", "hotel", "yes", "보상", "호텔", "청구", "네")):
            service.progress(claim_id)
            found = service.search_documents(claim_id, text)
            best = (found["faq"] or found["policy"])[0]
            ref = service.evidence_panel(claim_id, "show", best["id"], best["text"].splitlines()[0][:60])["ref"]
            return f"{service.assess_coverage(claim_id, text)['reply']} [{ref}]"
        secure_only = {label(lc, "claim_form"), label(lc, "photo_id")}
        if missing and set(missing) <= secure_only:
            return t(lc, "almost_done", link=service.secure_link(claim_id))
        return t(lc, "still_needed", items=", ".join(missing[:2])) if missing else t(lc, "noted")


def make_agent(settings):
    if settings.agent_runtime_arn:
        return RemoteAgent(settings.agent_runtime_arn, settings.region)
    if settings.llm_backend == "sdk":
        from .sdk_local import SdkAgent
        return SdkAgent(settings.model_id)
    if settings.llm_backend in ("fake", "cli"):
        return ScriptedAgent()
    return StrandsAgent(settings.model_id, settings.region)
