"""The stack keeps its safety nets: alarms wired to the ops topic, the failed-events queue, and no global Bedrock
fallback unless a deployment asks for it."""
import os
import sys

import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk.assertions import Match, Template  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "infra"))


def synth(**context):
    from stack import ClausePathStack
    app = cdk.App(context={"skip_bundling": "true", **context})
    return Template.from_stack(ClausePathStack(app, "TripClaim", env=cdk.Environment(account="111111111111", region="us-east-1")))


@pytest.fixture(scope="module")
def template():
    return synth()


def test_every_alarm_notifies_ops(template):
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    assert len(alarms) >= 18
    assert all(a["Properties"].get("AlarmActions") for a in alarms.values())


def test_failed_events_are_kept(template):
    template.resource_count_is("AWS::SQS::Queue", 1)
    template.has_resource_properties("AWS::Lambda::EventInvokeConfig", {"MaximumRetryAttempts": 2,
                                     "DestinationConfig": {"OnFailure": Match.any_value()}})


def test_global_fallback_is_off_unless_allowed(template):
    fns = template.find_resources("AWS::Lambda::Function")
    envs = [f["Properties"]["Environment"]["Variables"] for f in fns.values() if "Environment" in f["Properties"]]
    assert envs and all(e.get("FALLBACK_MODEL_ID", "") == "" for e in envs if "MODEL_ID" in e)
    on = synth(allow_global_fallback="true").find_resources("AWS::Lambda::Function")
    assert any((f["Properties"].get("Environment") or {}).get("Variables", {}).get("FALLBACK_MODEL_ID", "").startswith("global.")
               for f in on.values())


def _bedrock_statements(template):
    out = []
    for p in template.find_resources("AWS::IAM::Policy").values():
        for st in p["Properties"]["PolicyDocument"]["Statement"]:
            acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            if any(a.startswith("bedrock:Invoke") for a in acts):
                out.append(st)
    return out


def test_bedrock_is_least_privilege(template):
    """No '*' and no other model: the configured profile, and the model behind it only through that profile."""
    import json
    sts = _bedrock_statements(template)
    assert sts
    for st in sts:
        res = json.dumps(st["Resource"])
        assert '"*"' not in res and "foundation-model/*" not in res and "global." not in res
        if "foundation-model/" in res:
            assert "claude-opus-5-5" in res and "bedrock:InferenceProfileArn" in json.dumps(st.get("Condition", {}))
    on = json.dumps(_bedrock_statements(synth(allow_global_fallback="true")))
    assert "inference-profile/global." in on  # the global profile only when a deployment allows it


def test_wording_guardrail_is_standard_tier_in_this_geography(template):
    """The second wording check: a denied topic for promised outcomes, Standard tier (Korean), US guardrail profile."""
    import json
    grs = template.find_resources("AWS::Bedrock::Guardrail")
    assert len(grs) == 1
    p = next(iter(grs.values()))["Properties"]
    assert p["TopicPolicyConfig"]["TopicsTierConfig"]["TierName"] == "STANDARD"
    topic = p["TopicPolicyConfig"]["TopicsConfig"][0]
    assert topic["Type"] == "DENY" and len(topic["Definition"]) <= 1000 and all(len(x) <= 100 for x in topic["Examples"])
    assert "us.guardrail.v1:0" in json.dumps(p["CrossRegionConfig"])
    fns = template.find_resources("AWS::Lambda::Function")
    assert all("GUARDRAIL_ID" in f["Properties"]["Environment"]["Variables"] for f in fns.values()
               if "MODEL_ID" in f["Properties"].get("Environment", {}).get("Variables", {}))
    pol = json.dumps(template.find_resources("AWS::IAM::Policy"))
    assert "bedrock:ApplyGuardrail" in pol and "guardrail-profile/us.guardrail.v1:0" in pol
    assert not synth(guardrail="false").find_resources("AWS::Bedrock::Guardrail")


def test_messaging_is_scoped_to_this_account_and_region(template):
    """Sending a text, an email or a WhatsApp message: never a '*' resource."""
    sts = [st for p in template.find_resources("AWS::IAM::Policy").values() for st in p["Properties"]["PolicyDocument"]["Statement"]
           if any(str(a).split(":")[0] in ("sms-voice", "ses", "social-messaging", "bedrock-agentcore")
                  for a in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]]))]
    assert sts
    for st in sts:
        res = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
        assert "*" not in res and all("111111111111" in str(r) and "us-east-1" in str(r) for r in res), st
