"""Structured LLM calls (one JSON object out) behind three backends.

- bedrock: Bedrock Converse with a forced tool whose input schema is the output schema. Used in AWS.
- sdk:     Claude Agent SDK with the developer's Claude login (sdk_local.SdkLLM). Local development.
- cli:     `claude -p --json-schema` with subscription auth. Used for local evals.
- fake:    canned responses for tests.

Content blocks are backend-neutral: {"type": "text", "text": ...} or {"type": "image", "bytes": ..., "format": "jpeg"}.
"""
import base64
import json
import threading
import subprocess
import tempfile
from pathlib import Path


UNSUPPORTED_KEYWORDS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength"}


def strict_schema(schema):
    """Drop JSON-schema keywords that Bedrock structured output rejects (numeric/length bounds)."""
    if isinstance(schema, dict):
        return {k: strict_schema(v) for k, v in schema.items() if k not in UNSUPPORTED_KEYWORDS}
    if isinstance(schema, list):
        return [strict_schema(v) for v in schema]
    return schema


def bedrock_retry_config():
    """Bedrock sometimes answers "unable to process your request" for a minute: back off and try longer than the default 4."""
    from botocore.config import Config
    return Config(retries={"max_attempts": 10, "mode": "adaptive"}, read_timeout=300, connect_timeout=10)


class BedrockLLM:
    """Converse with native structured output (outputConfig.textFormat json_schema) at a fixed effort."""

    def __init__(self, model_id, region, effort="medium", fallback_model_id=None):
        import boto3
        self.model_id, self.effort = model_id, effort
        # Off unless the deployment opts in: a fallback profile may process documents (ID, receipts) outside the region.
        # The insurer turns it on knowingly (-c allow_global_fallback=true), and every use is recorded per claim.
        self.fallback_model_id = fallback_model_id or None
        self._local = threading.local()  # per thread: concurrent claims never see each other's fallback
        self.client = boto3.client("bedrock-runtime", region_name=region, config=bedrock_retry_config())

    @property
    def last_fallback(self):
        """The fallback this thread's last call used, if any; the caller records it with the claim and clears it."""
        return getattr(self._local, "fb", None)

    @last_fallback.setter
    def last_fallback(self, value):
        self._local.fb = value

    def structured(self, system, content, schema, name="result"):
        blocks = []
        for c in content:
            if c["type"] == "text":
                blocks.append({"text": c["text"]})
            elif c["type"] == "document":
                blocks.append({"document": {"format": c["format"], "name": "attachment", "source": {"bytes": c["bytes"]}}})
            else:
                blocks.append({"image": {"format": c.get("format", "jpeg"), "source": {"bytes": c["bytes"]}}})
        req = dict(
            system=[{"text": system}, {"cachePoint": {"type": "default"}}],  # fixed prompt + policy is cached
            messages=[{"role": "user", "content": blocks}],
            outputConfig={"textFormat": {"type": "json_schema", "structure": {"jsonSchema": {
                "schema": json.dumps(strict_schema(schema)), "name": name}}}, "effort": self.effort},
            inferenceConfig={"maxTokens": 8000},
        )
        try:
            res = self.client.converse(modelId=self.model_id, **req)
        except self.client.exceptions.ServiceUnavailableException:
            if not self.fallback_model_id:
                raise
            print(f"BEDROCK_FALLBACK {self.model_id} -> {self.fallback_model_id} ({name})")  # log metric: how often it happens
            res = self.client.converse(modelId=self.fallback_model_id, **req)
            self.last_fallback = {"from": self.model_id, "to": self.fallback_model_id, "call": name}
        texts = [b["text"] for b in res["output"]["message"]["content"] if "text" in b]
        if not texts:
            raise RuntimeError(f"no structured output: {res['output']}")
        return json.loads(texts[-1])


class CliLLM:
    def __init__(self, model="claude-opus-5-5", effort="medium"):
        self.model, self.effort = model, effort

    def structured(self, system, content, schema, name="result"):
        blocks = []
        for c in content:
            if c["type"] == "text":
                blocks.append({"type": "text", "text": c["text"]})
            elif c["type"] == "document":
                data = base64.b64encode(c["bytes"]).decode()
                blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}})
            else:
                data = base64.b64encode(c["bytes"]).decode()
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": f"image/{c.get('format', 'jpeg')}", "data": data}})
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(system)
        cmd = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--model", self.model, "--effort", self.effort, "--system-prompt-file", fh.name,
               "--json-schema", json.dumps(schema), "--tools", "", "--strict-mcp-config", "--setting-sources", "",
               "--no-session-persistence"]
        msg = {"type": "user", "message": {"role": "user", "content": blocks}}
        proc = subprocess.run(cmd, input=json.dumps(msg) + "\n", capture_output=True, text=True, timeout=1800, cwd="/tmp")
        Path(fh.name).unlink(missing_ok=True)
        for line in reversed(proc.stdout.splitlines()):
            if line.startswith('{"') and '"type":"result"' in line:
                out = json.loads(line).get("structured_output")
                if out is not None:
                    return out
        raise RuntimeError((proc.stderr or proc.stdout)[-1500:])


class FakeLLM:
    """responses: {name: dict or callable(system, content) -> dict}."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def structured(self, system, content, schema, name="result"):
        self.calls.append((name, content))
        r = self.responses[name]
        return r(system, content) if callable(r) else json.loads(json.dumps(r))


def make_llm(settings):
    if settings.llm_backend == "sdk":
        from .sdk_local import SdkLLM
        return SdkLLM(settings.model_id)
    if settings.llm_backend == "cli":
        return CliLLM()
    if settings.llm_backend == "fake":
        return FakeLLM({})
    return BedrockLLM(settings.model_id, settings.region, fallback_model_id=settings.fallback_model_id)
