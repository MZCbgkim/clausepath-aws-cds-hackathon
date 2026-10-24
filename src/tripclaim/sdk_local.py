"""Local development on the Claude Agent SDK with the developer's own Claude login (LLM_BACKEND=sdk).

The same system prompt, tools and CONTEXT block as StrandsAgent, and the same structured calls as BedrockLLM, so a
local run behaves like the deployed one without Bedrock calls. Deployed (Lambda, AgentCore) the model is Bedrock only:
the SDK is not in the Lambda packages, and these classes refuse to run there. The host's settings files are never read
(setting_sources=[]) and no built-in tool (files, shell, web) is offered: only the claim tools below.
"""
import asyncio
import base64
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

from .agent import SYSTEM, history, turn_prompt

SERVER = "tripclaim"
MAX_TURNS = 12


def check_local():
    if (os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get("AWS_EXECUTION_ENV")) \
            and os.environ.get("CLAUDE_CODE_USE_BEDROCK") != "1":
        raise RuntimeError("LLM_BACKEND=sdk is for local development; deployed, the model is reached through Bedrock only")


def run_sync(coro):
    """Run a coroutine to the end from sync code, also inside a tool of a running agent (assess_coverage calls the
    structured LLM): there the loop is busy, so the nested run gets its own thread and loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


def sdk_model(model_id):
    """"us.anthropic.claude-opus-5-5" (Bedrock profile) -> "claude-opus-5-5" (Claude login)."""
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1":
        return model_id
    return model_id.rsplit("anthropic.", 1)[-1]


def options(model, effort, **kw):
    from claude_agent_sdk import ClaudeAgentOptions
    return ClaudeAgentOptions(model=model, effort=effort, tools=[], setting_sources=[], permission_mode="dontAsk",
                              cwd=tempfile.gettempdir(), **kw)


def _text(result):
    return {"content": [{"type": "text", "text": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)}]}


def claim_tools(service, claim_id):
    """The StrandsAgent tools as an in-process MCP server, and the names the agent may call. Each runs in a thread,
    as Strands runs tools: the agent often asks for the policy check and a search at once, and the 20-second check
    must not hold the search (and its line in the chat) back."""
    from claude_agent_sdk import create_sdk_mcp_server, tool
    from mcp.types import ToolAnnotations
    parallel = ToolAnnotations(readOnlyHint=True)  # Claude Code runs only read-only tools side by side

    @tool("assess_coverage", "Check the policy for this traveler's situation. Returns status, clause IDs relied on, "
          "covered and not-covered expenses, and the documents still needed.", {"question": str}, parallel)
    async def assess_coverage(args):
        await asyncio.to_thread(service.progress, claim_id)
        return _text(await asyncio.to_thread(service.assess_coverage, claim_id, args["question"]))

    @tool("search_documents", "Search the policy PDF and the FAQ PDF (traveler's language first). Returns the top "
          "policy clauses and FAQ answers with ids, page and text. Use it to find the passage to show the traveler.",
          {"query": str}, parallel)
    async def search_documents(args):
        return _text(await asyncio.to_thread(service.search_documents, claim_id, args["query"]))

    @tool("show_evidence", "Attach a source to your web reply: one passage (an id from search_documents), highlighted "
          "in the original PDF. Returns its reference number n; write [n] in your reply right after what it supports. "
          "caption: one short line in the traveler's language saying what to look at. Up to 3 per reply.",
          {"chunk_id": str, "caption": str})
    async def show_evidence(args):
        return _text(await asyncio.to_thread(service.evidence_panel, claim_id, "show", args["chunk_id"], args["caption"]))

    @tool("minimize_evidence", "Fold the open source card to one line (it stays in the chat), e.g. when the traveler "
          "starts sending documents or the topic has moved on.", {})
    async def minimize_evidence(args):
        return _text(service.evidence_panel(claim_id, "minimize"))

    @tool("secure_link", "Link to the secure claims page for the claim form, photo ID and bank details.", {})
    async def secure_link(args):
        return _text(service.secure_link(claim_id))

    @tool("submit_claim", "Submit the claim to the insurer. Only after the traveler confirmed and the checklist is "
          "complete.", {})
    async def submit_claim(args):
        return _text(await asyncio.to_thread(service.submit_claim, claim_id, actor="agent"))

    tools = [assess_coverage, search_documents, show_evidence, minimize_evidence, secure_link, submit_claim]
    return create_sdk_mcp_server(name=SERVER, tools=tools), [f"mcp__{SERVER}__{t.name}" for t in tools]


def transcript(messages):
    """Earlier turns as text: the SDK takes one prompt per run, Strands takes the message list."""
    lines = [("Traveler: " if m["role"] == "user" else "You: ") + m["content"][0]["text"] for m in history(messages)]
    return "CONVERSATION SO FAR\n" + "\n".join(lines) + "\n\nNEW MESSAGE\n" if lines else ""


class SdkAgent:
    """StrandsAgent's turn on the Claude Agent SDK."""

    def __init__(self, model_id, effort="medium"):
        check_local()
        self.model, self.effort = sdk_model(model_id), effort

    def run(self, service, claim_id, text, context):
        return run_sync(self._run(service, claim_id, text, context))

    async def _run(self, service, claim_id, text, context):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query
        server, allowed = claim_tools(service, claim_id)
        opts = options(self.model, self.effort, system_prompt=SYSTEM, mcp_servers={SERVER: server},
                       strict_mcp_config=True, allowed_tools=allowed, max_turns=MAX_TURNS)
        prompt = transcript(service.repo.messages(claim_id)) + turn_prompt(text, context)
        last, result = "", None
        async for m in query(prompt=prompt, options=opts):
            if isinstance(m, AssistantMessage):
                texts = [b.text for b in m.content if isinstance(b, TextBlock)]
                if texts:
                    last = "\n".join(texts)  # the reply is the text after the last tool call
            elif isinstance(m, ResultMessage):
                result = m
        if result is not None and result.is_error:
            raise RuntimeError(f"agent run ended with {result.subtype}: {result.result}")
        return ((result.result if result is not None and result.result else last) or "").strip()


class SdkLLM:
    """BedrockLLM.structured on the Claude Agent SDK: one user message (text, images, PDFs) -> one JSON object."""

    def __init__(self, model_id, effort="medium"):
        check_local()
        self.model, self.effort = sdk_model(model_id), effort

    def structured(self, system, content, schema, name="result"):
        return run_sync(self._structured(system, content, schema))

    async def _structured(self, system, content, schema):
        from claude_agent_sdk import ResultMessage, query
        blocks = []
        for c in content:
            if c["type"] == "text":
                blocks.append({"type": "text", "text": c["text"]})
            else:
                data = base64.b64encode(c["bytes"]).decode()
                media = "application/pdf" if c["type"] == "document" else f"image/{c.get('format', 'jpeg')}"
                blocks.append({"type": c["type"] if c["type"] == "document" else "image",
                               "source": {"type": "base64", "media_type": media, "data": data}})

        async def prompt():
            yield {"type": "user", "message": {"role": "user", "content": blocks}, "parent_tool_use_id": None}

        opts = options(self.model, self.effort, system_prompt=system, strict_mcp_config=True,
                       output_format={"type": "json_schema", "schema": schema})
        result = None
        async for m in query(prompt=prompt(), options=opts):
            if isinstance(m, ResultMessage):
                result = m
        if result is None or result.structured_output is None:
            raise RuntimeError(f"no structured output: {getattr(result, 'subtype', None)} {getattr(result, 'result', '')}"[:1500])
        return result.structured_output
