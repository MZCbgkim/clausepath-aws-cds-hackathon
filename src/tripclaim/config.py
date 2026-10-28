"""Runtime settings from environment variables. See .env.example for the full list."""
import os
from dataclasses import dataclass, field


def _env(name, default=""):
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    region: str = field(default_factory=lambda: _env("AWS_REGION", "us-east-1"))
    store: str = field(default_factory=lambda: _env("TRIPCLAIM_STORE", "memory"))          # memory | dynamodb
    table_name: str = field(default_factory=lambda: _env("TABLE_NAME", "tripclaim"))
    bucket: str = field(default_factory=lambda: _env("DOCS_BUCKET", ""))
    audit_bucket: str = field(default_factory=lambda: _env("AUDIT_BUCKET", ""))  # write-once copy of the audit trail
    channels: str = field(default_factory=lambda: _env("TRIPCLAIM_CHANNELS", "fake"))       # fake | live
    llm_backend: str = field(default_factory=lambda: _env("LLM_BACKEND", "bedrock"))        # bedrock | sdk (local: Claude Agent SDK, own login) | cli | fake
    model_id: str = field(default_factory=lambda: _env("MODEL_ID", "us.anthropic.claude-opus-5-5"))
    # empty = no fallback (documents stay in the regional profile); set only by a deployment that allows it
    fallback_model_id: str = field(default_factory=lambda: os.environ.get("FALLBACK_MODEL_ID", ""))
    # Bedrock guardrail for promised outcomes (second check after the regex); empty = regex only (local, tests)
    guardrail_id: str = field(default_factory=lambda: os.environ.get("GUARDRAIL_ID", ""))
    guardrail_version: str = field(default_factory=lambda: os.environ.get("GUARDRAIL_VERSION", "DRAFT"))
    # demo: SMS/RCS go to the simulated phone page (/phone) instead of a carrier; never in production
    sim_phone: bool = field(default_factory=lambda: _env("SIM_PHONE", "").lower() in ("on", "1", "true"))
    # demo: the English persona (Jordan) gets this address so the decision letter goes out by SES (sandbox: verified)
    demo_email: str = field(default_factory=lambda: _env("DEMO_EMAIL", ""))
    # public demo: model turns allowed per day across all travelers (0 = no cap); one inbound message or photo batch = one turn
    llm_daily_cap: int = field(default_factory=lambda: int(_env("LLM_DAILY_CAP", "0") or 0))
    agent_runtime_arn: str = field(default_factory=lambda: _env("AGENT_RUNTIME_ARN", ""))   # set to run the agent on AgentCore

    # WhatsApp (AWS End User Messaging Social)
    wa_phone_number_id: str = field(default_factory=lambda: _env("WA_PHONE_NUMBER_ID", ""))
    wa_meta_api_version: str = field(default_factory=lambda: _env("WA_META_API_VERSION", "v20.0"))
    wa_template_language: str = field(default_factory=lambda: _env("WA_TEMPLATE_LANGUAGE", "en_US"))
    # SMS / RCS (AWS End User Messaging, pinpoint-sms-voice-v2)
    sms_origination: str = field(default_factory=lambda: _env("SMS_ORIGINATION_IDENTITY", ""))
    rcs_agent: str = field(default_factory=lambda: _env("RCS_AGENT_ID", ""))
    sms_configuration_set: str = field(default_factory=lambda: _env("SMS_CONFIGURATION_SET", ""))
    # Email (Amazon SES)
    email_from: str = field(default_factory=lambda: _env("EMAIL_FROM", "claims@example.com"))
    claims_inbox: str = field(default_factory=lambda: _env("CLAIMS_INBOX", "claim@example.com"))   # travelers forward mail here
    reviewer_email: str = field(default_factory=lambda: _env("REVIEWER_EMAIL", "reviewer@example.com"))
    ses_configuration_set: str = field(default_factory=lambda: _env("SES_CONFIGURATION_SET", ""))

    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8080"))
    max_reminders: int = field(default_factory=lambda: int(_env("MAX_REMINDERS", "3")))
    review_locale: str = field(default_factory=lambda: _env("REVIEW_LOCALE", "en"))  # language of the reviewer packet
    # Who the traveler is dealing with: shown in texts, the chat header and the secure page (demo defaults are fictional)
    insurer_name_ko: str = field(default_factory=lambda: _env("INSURER_NAME_KO", "샘플손해보험"))
    insurer_name_en: str = field(default_factory=lambda: _env("INSURER_NAME_EN", "Sample Insurance"))
    insurer_phone: str = field(default_factory=lambda: _env("INSURER_PHONE", "02-000-0000"))
    # the same line dialled from abroad (a traveler's foreign SIM cannot reach "02-…")
    insurer_phone_intl: str = field(default_factory=lambda: _env("INSURER_PHONE_INTL", "+82-2-000-0000"))
    # Expected dates shown after submission ([CL-4]): business days to pay a complete claim / to report back on a check.
    # DUE_DATES=off hides the dates (e.g. while a mass event slows reviews); the traveler is told the insurer will confirm.
    pay_business_days: int = field(default_factory=lambda: int(_env("PAY_BUSINESS_DAYS", "3")))
    review_business_days: int = field(default_factory=lambda: int(_env("REVIEW_BUSINESS_DAYS", "10")))
    # Bulk-send preview: price per SMS segment / WhatsApp template (demo values; set per carrier contract),
    # and above how many recipients a second admin must approve the send.
    sms_price_usd: float = field(default_factory=lambda: float(_env("SMS_PRICE_USD", "0.02")))
    wa_price_usd: float = field(default_factory=lambda: float(_env("WA_PRICE_USD", "0.03")))
    bulk_second_admin_over: int = field(default_factory=lambda: int(_env("BULK_SECOND_ADMIN_OVER", "100")))
    # Four eyes: decisions that wait for a second reviewer
    dual_approval_over_usd: float = field(default_factory=lambda: float(_env("DUAL_APPROVAL_OVER_USD", "300")))
    dual_for_partial: bool = field(default_factory=lambda: _env("DUAL_FOR_PARTIAL", "on") != "off")
    dual_for_deny: bool = field(default_factory=lambda: _env("DUAL_FOR_DENY", "on") != "off")
    # who staff can hand a claim to (comma-separated emails); empty = anyone seen in the audit trail
    staff_directory: str = field(default_factory=lambda: _env("STAFF_DIRECTORY", ""))
    due_dates: bool = field(default_factory=lambda: _env("DUE_DATES", "on") != "off")

    def insurer(self, locale="en"):
        """Korean travelers see the local number; everyone else the international one (both reach the same desk)."""
        phone = self.insurer_phone if locale == "ko" else self.insurer_phone_intl
        return {"name": self.insurer_name_ko if locale == "ko" else self.insurer_name_en, "phone": phone,
                "tel": "+" + "".join(ch for ch in self.insurer_phone_intl if ch.isdigit())}


def settings():
    return Settings()
