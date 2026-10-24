"""Second check on words sent to a traveler: an Amazon Bedrock guardrail with a denied topic for promised outcomes.

The compliance regex (prompts/banned_wording.txt) runs first: free, instant and exact. The guardrail judges meaning
("보험금 나갑니다", "We've approved your claim" in words nobody listed), Standard tier for Korean, through the
cross-region guardrail profile of the deployment's geography. If it cannot be reached the message is judged by the
regex alone and GUARDRAIL_ERROR is logged for the ops alarm."""
import boto3

from .llm import bedrock_retry_config


class Guardrail:
    def __init__(self, guardrail_id, version, region):
        self.id, self.version = guardrail_id, version or "DRAFT"
        self.client = boto3.client("bedrock-runtime", region_name=region, config=bedrock_retry_config())

    def flags(self, text):
        """The denied topics this text falls under ([] = fine, None = the guardrail could not be asked)."""
        if not (text or "").strip():
            return []
        try:
            r = self.client.apply_guardrail(guardrailIdentifier=self.id, guardrailVersion=self.version, source="OUTPUT",
                                            content=[{"text": {"text": text}}])
        except Exception as e:  # the regex already ran; an outage must not stop a reply, but ops must see it
            print(f"GUARDRAIL_ERROR {type(e).__name__}: {e}")
            return None
        if r.get("action") != "GUARDRAIL_INTERVENED":
            return []
        topics = [t["name"] for a in r.get("assessments", []) for t in (a.get("topicPolicy") or {}).get("topics", [])
                  if t.get("action", "BLOCKED") == "BLOCKED"]
        return topics or ["guardrail"]


def make_guardrail(settings):
    return Guardrail(settings.guardrail_id, settings.guardrail_version, settings.region) if settings.guardrail_id else None
