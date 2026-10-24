"""Wire a Service from settings. Lambda handlers, AgentCore and the simulator all call build()."""
from functools import lru_cache

from .agent import make_agent
from .config import settings as load_settings
from .docstore import docstore
from .llm import make_llm
from .service import Service
from .store import repository


def build(settings=None, **overrides):
    s = settings or load_settings()
    if "channels" not in overrides:
        if s.channels == "live":
            from .channels.live import live_channels
            overrides["channels"] = live_channels(s)
        else:
            from .channels.fake import fake_channels
            overrides["outbox"] = overrides.get("outbox", [])
            overrides["channels"] = fake_channels(overrides["outbox"])
    overrides.pop("outbox", None)
    repo = overrides.pop("repo", None) or repository(s)
    if s.audit_bucket:  # every audit entry is also written once to the Object Lock bucket
        import json
        import uuid

        import boto3
        s3 = boto3.client("s3", region_name=s.region)
        repo.audit_sink = lambda e: s3.put_object(
            Bucket=s.audit_bucket, Key=f"audit/{e['at'][:10]}/{e['at']}-{uuid.uuid4().hex[:6]}.json",
            Body=json.dumps(e, ensure_ascii=False, default=str).encode(), ContentType="application/json",
            ChecksumAlgorithm="SHA256")
    from .guardrail import make_guardrail
    overrides.setdefault("guardrail", make_guardrail(s))
    return Service(s, repo, overrides.pop("channels"),
                   overrides.pop("llm", None) or make_llm(s), overrides.pop("agent", None) or make_agent(s),
                   overrides.pop("docs", None) or docstore(s), **overrides)


@lru_cache
def service():
    """Process-wide service for Lambda (reused across warm invocations)."""
    return build()
