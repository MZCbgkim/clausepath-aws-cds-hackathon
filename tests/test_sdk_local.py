"""LLM_BACKEND=sdk: local runs on the Claude Agent SDK, never deployed."""
import os
import sys

import pytest

from tripclaim import sdk_local
from tripclaim.agent import make_agent
from tripclaim.config import Settings
from tripclaim.llm import make_llm


def test_sdk_backend_picks_the_sdk_agent_and_llm(monkeypatch):
    for k in ("AWS_LAMBDA_FUNCTION_NAME", "AWS_EXECUTION_ENV", "CLAUDE_CODE_USE_BEDROCK", "AGENT_RUNTIME_ARN"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(store="memory", channels="fake", llm_backend="sdk")
    agent, llm = make_agent(s), make_llm(s)
    assert isinstance(agent, sdk_local.SdkAgent) and isinstance(llm, sdk_local.SdkLLM)
    assert agent.model == llm.model == "claude-opus-5-5"  # Bedrock profile id -> Claude login model


@pytest.mark.parametrize("var", ["AWS_LAMBDA_FUNCTION_NAME", "AWS_EXECUTION_ENV"])
def test_sdk_backend_refuses_to_run_deployed(monkeypatch, var):
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.setenv(var, "x")
    with pytest.raises(RuntimeError, match="Bedrock only"):
        sdk_local.SdkAgent("us.anthropic.claude-opus-5-5")
    with pytest.raises(RuntimeError, match="Bedrock only"):
        sdk_local.SdkLLM("us.anthropic.claude-opus-5-5")


def test_sdk_is_not_in_the_lambda_packages():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "infra"))
    pytest.importorskip("aws_cdk")
    from stack import DEPENDENCIES
    assert not any("claude-agent-sdk" in d for d in DEPENDENCIES)


def test_transcript_keeps_earlier_turns_but_not_the_current_message():
    msgs = [{"direction": "out", "text": "Your flight was cancelled."}, {"direction": "in", "text": "Is a hotel covered?"},
            {"direction": "out", "text": "Let me check."}, {"direction": "in", "text": "thanks"}]
    t = sdk_local.transcript(msgs)
    assert "Traveler: Is a hotel covered?" in t and "You: Let me check." in t and "thanks" not in t
    assert sdk_local.transcript([]) == ""


def test_claim_tools_match_the_strands_agent():
    server, allowed = sdk_local.claim_tools(None, "clm-x")
    assert {a.split("__")[-1] for a in allowed} == {"assess_coverage", "search_documents", "show_evidence",
                                                   "minimize_evidence", "secure_link", "submit_claim"}


def test_run_sync_works_inside_a_running_loop():
    import asyncio

    async def inner():
        return 7

    async def tool_handler():  # an agent tool calling the structured LLM
        return sdk_local.run_sync(inner())

    assert sdk_local.run_sync(inner()) == 7 and asyncio.run(tool_handler()) == 7
