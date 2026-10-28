"""ClausePath on AWS (CloudFormation stack and internal names keep the working name "TripClaim"): CDS channels in, agent on Bedrock (Lambda or AgentCore Runtime), DynamoDB/S3, dashboard API.

Context flags (cdk deploy -c key=value):
  inbound_domain=claims.example.com   create the SES receipt rule (domain must be verified in SES; activate the
                                      rule set once: aws ses set-active-receipt-rule-set --rule-set-name tripclaim)
  cognito_prefix=tripclaim-demo-123   Cognito hosted UI domain prefix (globally unique)
  agentcore=true                      build the agent container and run it on Bedrock AgentCore Runtime
  skip_bundling=true                  synth without Docker bundling (CI / quick checks)
Channel identities come from environment at deploy time (WA_PHONE_NUMBER_ID, SMS_ORIGINATION_IDENTITY,
RCS_AGENT_ID, EMAIL_FROM, CLAIMS_INBOX, REVIEWER_EMAIL, MODEL_ID).
"""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import jsii

from aws_cdk import (CfnOutput, Duration, RemovalPolicy, Stack, aws_apigatewayv2 as apigw,
                     aws_apigatewayv2_authorizers as authz, aws_apigatewayv2_integrations as integ,
                     aws_bedrock as bedrock, aws_bedrockagentcore as agentcore, aws_cognito as cognito, aws_dynamodb as ddb,
                     aws_ecr_assets as ecr_assets, aws_events as events, aws_events_targets as targets,
                     aws_iam as iam, aws_lambda as lambda_, aws_lambda_event_sources as sources, aws_s3 as s3,
                     aws_s3_notifications as s3n, aws_ses as ses, aws_ses_actions as ses_actions, aws_sns as sns,
                     aws_cloudwatch as cw, aws_cloudwatch_actions as cw_actions, aws_logs as logs,
                     aws_sns_subscriptions as subs, aws_sqs as sqs, aws_lambda_destinations as destinations, BundlingOptions)
from aws_cdk import ILocalBundling
from constructs import Construct

ROOT = Path(__file__).resolve().parents[1]
# The guardrail's denied topic, owned by compliance next to the banned-wording list. Tuned with eval/guardrail/tune.py
# (2026-10-10: 0 of 59 careful sentences blocked; 10 of 12 promises the regex misses caught).
PROMISE_TOPIC = json.loads((ROOT / "src/tripclaim/prompts/promise_topic.json").read_text(encoding="utf-8"))
# free text may not state the claim's outcome at all; the decision goes out only in server-filled templates
DECISION_TOPIC = json.loads((ROOT / "src/tripclaim/prompts/decision_topic.json").read_text(encoding="utf-8"))
PROFILE_PREFIXES = ("us", "eu", "apac", "jp", "au", "ca", "global")  # cross-region inference profile ids: "<prefix>.<model>"
DEPENDENCIES = ["boto3>=1.40", "strands-agents>=1.50", "bedrock-agentcore>=1.20"]


@jsii.implements(ILocalBundling)
class UvBundle:
    """Build the Lambda package without Docker: arm64 manylinux wheels via uv, plus our source."""

    def try_bundle(self, output_dir, *args, **kwargs):
        subprocess.run(["uv", "pip", "install", "--quiet", "--target", output_dir, "--python-platform",
                        "aarch64-manylinux_2_28", "--python-version", "3.13", "--only-binary", ":all:", *DEPENDENCIES],
                       check=True)
        shutil.copytree(ROOT / "src" / "tripclaim", Path(output_dir) / "tripclaim",
                        ignore=shutil.ignore_patterns("__pycache__"))
        return True
ASSET_EXCLUDE = [".venv", "data", "eval", "infra", "demo", "cdk.out", "tests", "**/__pycache__", ".git", "docs", "*.md"]


class ClausePathStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs):
        super().__init__(scope, cid, **kwargs)
        ctx = self.node.try_get_context
        env = os.environ.get
        # -c profile=prod: data kept on stack delete, audit copy in compliance mode for 5 years (nobody can delete it early).
        # The demo default cleans up and keeps a 30-day governance lock, so a hackathon account is not left with undeletable data.
        prod = ctx("profile") == "prod"
        keep = RemovalPolicy.RETAIN if prod or str(ctx("retain")).lower() == "true" else RemovalPolicy.DESTROY

        table = ddb.Table(self, "Table", partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
                          sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
                          billing_mode=ddb.BillingMode.PAY_PER_REQUEST, removal_policy=keep,
                          point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True))
        table.add_global_secondary_index(index_name="GSI1",
                                         partition_key=ddb.Attribute(name="GSI1PK", type=ddb.AttributeType.STRING),
                                         sort_key=ddb.Attribute(name="GSI1SK", type=ddb.AttributeType.STRING))

        bucket = s3.Bucket(self, "Docs", encryption=s3.BucketEncryption.KMS_MANAGED, enforce_ssl=True,
                           block_public_access=s3.BlockPublicAccess.BLOCK_ALL, removal_policy=keep,
                           auto_delete_objects=keep == RemovalPolicy.DESTROY,
                           lifecycle_rules=[s3.LifecycleRule(prefix="claims/", expiration=Duration.days(90)),
                                            s3.LifecycleRule(prefix="inbound/", expiration=Duration.days(30))])
        # Audit trail copy outside the working table: write-once (Object Lock), kept even when the stack is deleted.
        audit_days = int(ctx("audit_days") or (1825 if prod else 30))
        audit_bucket = s3.Bucket(self, "Audit", encryption=s3.BucketEncryption.KMS_MANAGED, enforce_ssl=True,
                                 block_public_access=s3.BlockPublicAccess.BLOCK_ALL, removal_policy=RemovalPolicy.RETAIN,
                                 versioned=True, object_lock_enabled=True,
                                 # -c audit_mode=compliance: nobody, not even the account root, can delete before the period ends
                                 object_lock_default_retention=(s3.ObjectLockRetention.compliance if prod or ctx("audit_mode") == "compliance"
                                                                else s3.ObjectLockRetention.governance)(Duration.days(audit_days)),
                                 lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(audit_days + 365),
                                                                   noncurrent_version_expiration=Duration.days(audit_days + 1))])

        # One topic for WhatsApp events, two-way SMS/RCS and SMS/RCS delivery events.
        topic = sns.Topic(self, "Inbound", display_name="ClausePath inbound")
        for svc in ("social-messaging.amazonaws.com", "sms-voice.amazonaws.com"):
            topic.add_to_resource_policy(iam.PolicyStatement(
                actions=["sns:Publish"], principals=[iam.ServicePrincipal(svc)], resources=[topic.topic_arn],
                conditions={"StringEquals": {"aws:SourceAccount": self.account}}))

        bus = events.EventBus(self, "Flights", event_bus_name="tripclaim-flights")

        common_env = {
            "TRIPCLAIM_STORE": "dynamodb", "TABLE_NAME": table.table_name, "DOCS_BUCKET": bucket.bucket_name,
            "AUDIT_BUCKET": audit_bucket.bucket_name,
            "TRIPCLAIM_CHANNELS": "live", "LLM_BACKEND": "bedrock",
            "MODEL_ID": env("MODEL_ID", "us.anthropic.claude-opus-5-5"),
            # off by default: with -c allow_global_fallback=true, document reading may use the global profile when the
            # regional one is down (data may then be processed outside the region; each use is in the claim's audit trail)
            "FALLBACK_MODEL_ID": ("global." + env("MODEL_ID", "us.anthropic.claude-opus-5-5").split(".", 1)[1])
                                 if str(self.node.try_get_context("allow_global_fallback")).lower() == "true" else "",
            "WA_PHONE_NUMBER_ID": env("WA_PHONE_NUMBER_ID", ""), "SMS_ORIGINATION_IDENTITY": env("SMS_ORIGINATION_IDENTITY", ""),
            "RCS_AGENT_ID": env("RCS_AGENT_ID", ""), "SIM_PHONE": env("SIM_PHONE", ""), "LLM_DAILY_CAP": env("LLM_DAILY_CAP", "300"), "DEMO_EMAIL": env("DEMO_EMAIL", ""), "SMS_CONFIGURATION_SET": env("SMS_CONFIGURATION_SET", ""),
            "EMAIL_FROM": env("EMAIL_FROM", "claims@example.com"), "CLAIMS_INBOX": env("CLAIMS_INBOX", "claim@example.com"),
            "REVIEWER_EMAIL": env("REVIEWER_EMAIL", "reviewer@example.com"),
            "REVIEW_LOCALE": env("REVIEW_LOCALE", "ko"),
            "DUAL_APPROVAL_OVER_USD": env("DUAL_APPROVAL_OVER_USD", "300"),  # a payment at or above it waits for a second reviewer  # the insurer's staff read assessments and packets in Korean
        }

        if ctx("skip_bundling") == "true":
            code = lambda_.Code.from_asset(str(ROOT / "src"))
        else:
            code = lambda_.Code.from_asset(str(ROOT / "src"), exclude=["**/__pycache__"], bundling=BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_13.bundling_image, local=UvBundle(),  # Docker image only if uv is missing
                command=["bash", "-c", "pip install --no-cache-dir " + " ".join(f"'{d}'" for d in DEPENDENCIES)
                         + " -t /asset-output && cp -r /asset-input/tripclaim /asset-output/"]))

        # Bedrock, least privilege: only the configured profiles (the regional one, and the global one only when the
        # fallback is allowed), and the model behind them only when the call comes through one of those profiles
        models = [m for m in (common_env["MODEL_ID"], common_env["FALLBACK_MODEL_ID"]) if m]
        profiles = [f"arn:aws:bedrock:*:{self.account}:inference-profile/{m}" for m in models if m.split(".")[0] in PROFILE_PREFIXES]
        direct = [m for m in models if m.split(".")[0] not in PROFILE_PREFIXES]  # a bare model id: this region only
        base = sorted({m.split(".", 1)[1] if m.split(".")[0] in PROFILE_PREFIXES else m for m in models})
        bedrock_actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        bedrock_statements = []
        if profiles:
            bedrock_statements += [
                iam.PolicyStatement(actions=bedrock_actions, resources=profiles),
                iam.PolicyStatement(actions=bedrock_actions, resources=[f"arn:aws:bedrock:*::foundation-model/{b}*" for b in base],
                                    conditions={"StringLike": {"bedrock:InferenceProfileArn": profiles}})]
        if direct:
            bedrock_statements.append(iam.PolicyStatement(
                actions=bedrock_actions, resources=[f"arn:aws:bedrock:{self.region}::foundation-model/{m}" for m in direct]))

        # Second check on every message to a traveler (after the compliance regex): a Bedrock guardrail whose denied topic
        # is a promised outcome. Standard tier (Korean is supported there) runs through the guardrail profile of this
        # deployment's geography, so the text stays in it. -c guardrail=false leaves the regex alone.
        if str(self.node.try_get_context("guardrail")).lower() != "false":
            geo = {"us": "us", "eu": "eu", "ap": "apac", "ca": "us"}.get(self.region.split("-")[0], "us") if not self.region.startswith("${") else "us"
            gr_profile = f"{geo}.guardrail.v1:0"
            guardrail = bedrock.CfnGuardrail(
                self, "PromiseGuardrail", name="tripclaim-outcome-promises",
                description="Messages to travelers must not promise a claim outcome (Korean Financial Consumer Protection Act).",
                blocked_input_messaging="Blocked: promises a claim outcome.", blocked_outputs_messaging="Blocked: promises a claim outcome.",
                cross_region_config=bedrock.CfnGuardrail.GuardrailCrossRegionConfigProperty(
                    guardrail_profile_arn=f"arn:aws:bedrock:{self.region}:{self.account}:guardrail-profile/{gr_profile}"),
                topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                    topics_tier_config=bedrock.CfnGuardrail.TopicsTierConfigProperty(tier_name="STANDARD"),
                    topics_config=[bedrock.CfnGuardrail.TopicConfigProperty(
                        name="OutcomePromise", type="DENY",
                        definition=PROMISE_TOPIC["definition"], examples=PROMISE_TOPIC["examples"]),
                        bedrock.CfnGuardrail.TopicConfigProperty(
                        name="DecisionStatement", type="DENY",
                        definition=DECISION_TOPIC["definition"], examples=DECISION_TOPIC["examples"])]))
            # a new version whenever compliance changes the topic (its hash is in the description, so CloudFormation replaces it)
            gr_version = bedrock.CfnGuardrailVersion(
                self, "PromiseGuardrailVersion", guardrail_identifier=guardrail.attr_guardrail_id,
                description="topics " + hashlib.sha256(json.dumps([PROMISE_TOPIC, DECISION_TOPIC], ensure_ascii=False).encode()).hexdigest()[:12])
            common_env["GUARDRAIL_ID"], common_env["GUARDRAIL_VERSION"] = guardrail.attr_guardrail_id, gr_version.attr_version
            bedrock_statements.append(iam.PolicyStatement(
                actions=["bedrock:ApplyGuardrail"],
                resources=[guardrail.attr_guardrail_arn, f"arn:aws:bedrock:*:{self.account}:guardrail-profile/{gr_profile}"]))

        # Messaging, this account and Region only: WhatsApp numbers, SMS/RCS identities and configuration sets, SES identities
        messaging_statements = [
            iam.PolicyStatement(actions=["social-messaging:SendWhatsAppMessage", "social-messaging:GetWhatsAppMessageMedia"],
                                resources=[f"arn:aws:social-messaging:{self.region}:{self.account}:phone-number-id/*"]),
            iam.PolicyStatement(actions=["sms-voice:SendTextMessage", "sms-voice:SendRcsMessage"],
                                resources=[f"arn:aws:sms-voice:{self.region}:{self.account}:{k}/*"
                                           for k in ("phone-number", "sender-id", "pool", "configuration-set", "rcs-agent")]),
            iam.PolicyStatement(actions=["ses:SendEmail", "ses:SendRawEmail"],
                                resources=[f"arn:aws:ses:{self.region}:{self.account}:{k}/*" for k in ("identity", "configuration-set")])]

        def fn(name, handler, timeout=60, memory=1024, concurrency=None):
            # concurrency caps the functions that call Bedrock, so a public demo login cannot run up model costs
            f = lambda_.Function(self, name, runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
                                 handler=f"tripclaim.handlers.{handler}", code=code, timeout=Duration.seconds(timeout),
                                 memory_size=memory, environment=dict(common_env), reserved_concurrent_executions=concurrency)
            table.grant_read_write_data(f)
            bucket.grant_read_write(f)
            audit_bucket.grant_put(f)  # append only: Lambdas cannot read back, change or delete the audit copy
            for st in bedrock_statements:
                f.add_to_role_policy(st)
            for st in messaging_statements:
                f.add_to_role_policy(st)
            f.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/*"]))
            return f

        inbound = fn("InboundFn", "inbound_sns.handler", timeout=300, concurrency=2)
        inbound.add_event_source(sources.SnsEventSource(topic))

        email_in = fn("EmailInboundFn", "ses_inbound.handler", timeout=300)
        bucket.add_event_notification(s3.EventType.OBJECT_CREATED, s3n.LambdaDestination(email_in),
                                       s3.NotificationKeyFilter(prefix="inbound/"))

        disruption = fn("DisruptionFn", "disruption.handler", timeout=300)
        events.Rule(self, "DisruptionRule", event_bus=bus,
                    event_pattern=events.EventPattern(source=["tripclaim.flights"], detail_type=["FlightDisruption"]),
                    targets=[targets.LambdaFunction(disruption)])

        reminders = fn("RemindersFn", "reminders.handler", timeout=300)
        events.Rule(self, "ReminderSchedule", schedule=events.Schedule.rate(Duration.hours(1)),
                    targets=[targets.LambdaFunction(reminders)])

        api_fn = fn("ApiFn", "api.handler", timeout=300, concurrency=5)
        http = apigw.HttpApi(self, "Api", api_name="tripclaim")
        api_int = integ.HttpLambdaIntegration("ApiInt", api_fn)

        pool = cognito.UserPool(self, "Reviewers", self_sign_up_enabled=False, sign_in_aliases=cognito.SignInAliases(email=True),
                                removal_policy=keep)
        client = pool.add_client("Dashboard", o_auth=cognito.OAuthSettings(
            flows=cognito.OAuthFlows(implicit_code_grant=True), scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
            callback_urls=[f"{http.api_endpoint}/"], logout_urls=[f"{http.api_endpoint}/"]))
        for group, about in (("admins", "Operations: flight disruptions, reminders, demo data, everything reviewers do"),
                             ("reviewers", "Claims reviewers: record approval, request information"),
                             ("agents", "Contact centre: read claims, add internal notes")):
            cognito.CfnUserPoolGroup(self, f"Group{group.title()}", user_pool_id=pool.user_pool_id, group_name=group, description=about)
        prefix = ctx("cognito_prefix") or f"tripclaim-{self.account}"
        domain = pool.add_domain("Domain", cognito_domain=cognito.CognitoDomainOptions(domain_prefix=prefix))
        jwt = authz.HttpJwtAuthorizer("Jwt", f"https://cognito-idp.{self.region}.amazonaws.com/{pool.user_pool_id}",
                                      jwt_audience=[client.user_pool_client_id])
        api_fn.add_environment("COGNITO_CLIENT_ID", client.user_pool_client_id)
        api_fn.add_environment("COGNITO_DOMAIN", f"{prefix}.auth.{self.region}.amazoncognito.com")
        api_fn.add_environment("PUBLIC_BASE_URL", http.api_endpoint)
        for f in (inbound, email_in, disruption, reminders):
            f.add_environment("PUBLIC_BASE_URL", http.api_endpoint)

        # A missed write-once audit copy must reach a person: log metric -> alarm -> SNS (-c alarm_email=ops@example.com)
        ops_topic = sns.Topic(self, "OpsAlarms")
        if ctx("alarm_email"):
            ops_topic.add_subscription(subs.EmailSubscription(ctx("alarm_email")))
        for name, f in (("Api", api_fn), ("Inbound", inbound), ("Email", email_in)):
            logs.MetricFilter(self, f"AuditCopyFailed{name}", log_group=f.log_group, filter_pattern=logs.FilterPattern.literal("AUDIT_SINK_FAILED"),
                              metric_namespace="TripClaim", metric_name="AuditCopyFailed", metric_value="1")
        alarm = cw.Alarm(self, "AuditCopyFailedAlarm", metric=cw.Metric(namespace="TripClaim", metric_name="AuditCopyFailed",
                                                                        statistic="Sum", period=Duration.minutes(5)),
                         threshold=1, evaluation_periods=1, comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                         treat_missing_data=cw.TreatMissingData.NOT_BREACHING, alarm_description="Audit entry not copied to the Object Lock bucket")
        alarm.add_alarm_action(cw_actions.SnsAction(ops_topic))
        # Operations signals logged by the service, each a metric and an alarm to the same topic:
        # (log word, metric, threshold within 15 minutes, what it means)
        for word, metric, threshold, what in (
                ("AGENT_ERROR", "AgentErrors", 5, "The AI assistant failed 5+ times in 15 minutes (travelers get the fallback text)"),
                ("WORDING_AFTER_SEND", "WordingAfterSend", 1, "A sent message matched the banned-wording list (missed by the checks)"),
                ("SMS_FAILED", "SmsFailed", 10, "10+ text messages failed to deliver in 15 minutes"),
                ("APPEAL_OVERDUE", "AppealOverdue", 1, "An objection is past its reply-by date (moved to the reviewers' queue)"),
                ("BEDROCK_FALLBACK", "BedrockFallback", 1, "The regional Bedrock profile was unavailable; requests went to the global profile"),
                ("GUARDRAIL_ERROR", "GuardrailErrors", 5, "The wording guardrail failed 5+ times in 15 minutes (messages were checked by the regex alone)")):
            for name, f in (("Api", api_fn), ("Inbound", inbound), ("Email", email_in), ("Reminders", reminders)):
                logs.MetricFilter(self, f"{metric}{name}", log_group=f.log_group, filter_pattern=logs.FilterPattern.literal(word),
                                  metric_namespace="TripClaim", metric_name=metric, metric_value="1")
            cw.Alarm(self, f"{metric}Alarm", metric=cw.Metric(namespace="TripClaim", metric_name=metric, statistic="Sum",
                                                             period=Duration.minutes(15)),
                     threshold=threshold, evaluation_periods=1, comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                     treat_missing_data=cw.TreatMissingData.NOT_BREACHING, alarm_description=what
                     ).add_alarm_action(cw_actions.SnsAction(ops_topic))

        # Infrastructure, so a failing platform is noticed before travelers notice it (runbook: README "Operations alarms")
        def alarm(cid, metric, threshold, what):
            cw.Alarm(self, cid, metric=metric, threshold=threshold, evaluation_periods=1,
                     comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                     treat_missing_data=cw.TreatMissingData.NOT_BREACHING, alarm_description=what
                     ).add_alarm_action(cw_actions.SnsAction(ops_topic))
        for name, f in (("Api", api_fn), ("Inbound", inbound), ("Email", email_in), ("Disruption", disruption), ("Reminders", reminders)):
            alarm(f"{name}FnErrors", f.metric_errors(period=Duration.minutes(5), statistic="Sum"), 5, f"{name} function: 5+ errors in 5 minutes")
            alarm(f"{name}FnThrottles", f.metric_throttles(period=Duration.minutes(5), statistic="Sum"), 1, f"{name} function throttled (concurrency limit)")
        alarm("Api5xx", http.metric_server_error(period=Duration.minutes(5), statistic="Sum"), 10, "HTTP API: 10+ server errors (5xx) in 5 minutes")
        # events that failed every retry (inbound messages, disruption alerts, reminders) are kept, not lost
        failed = sqs.Queue(self, "FailedEvents", retention_period=Duration.days(14), enforce_ssl=True)
        for f in (inbound, email_in, disruption, reminders):
            f.configure_async_invoke(on_failure=destinations.SqsDestination(failed), retry_attempts=2)
        alarm("FailedEventsDlq", failed.metric_approximate_number_of_messages_visible(period=Duration.minutes(5), statistic="Maximum"), 1,
              "An inbound message or scheduled job failed all retries (see the FailedEvents queue)")

        http.add_routes(path="/", methods=[apigw.HttpMethod.GET], integration=api_int)
        http.add_routes(path="/config.js", methods=[apigw.HttpMethod.GET], integration=api_int)
        http.add_routes(path="/phone", methods=[apigw.HttpMethod.GET], integration=api_int)  # demo phone page; data via /api
        http.add_routes(path="/docs/{file}", methods=[apigw.HttpMethod.GET], integration=api_int)  # policy/FAQ PDFs
        for public in ("/about", "/privacy", "/terms"):  # linked from channel registrations; no login
            http.add_routes(path=public, methods=[apigw.HttpMethod.GET], integration=api_int)
        http.add_routes(path="/secure/{token}", methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST], integration=api_int)
        # Traveler web chat, reached from the SMS link; the link token is the credential (no login).
        http.add_routes(path="/c/{token}", methods=[apigw.HttpMethod.GET], integration=api_int)
        http.add_routes(path="/c/{token}/messages", methods=[apigw.HttpMethod.GET], integration=api_int)
        http.add_routes(path="/c/{token}/send", methods=[apigw.HttpMethod.POST], integration=api_int)
        http.add_routes(path="/c/{token}/secure", methods=[apigw.HttpMethod.GET], integration=api_int)
        http.add_routes(path="/c/{token}/submit", methods=[apigw.HttpMethod.POST], integration=api_int)
        http.add_routes(path="/c/{token}/lang", methods=[apigw.HttpMethod.POST], integration=api_int)
        http.add_routes(path="/c/{token}/appeal", methods=[apigw.HttpMethod.POST], integration=api_int)
        http.add_routes(path="/c/{token}/appeal_withdraw", methods=[apigw.HttpMethod.POST], integration=api_int)
        http.add_routes(path="/c/{token}/callback", methods=[apigw.HttpMethod.POST], integration=api_int)  # "call me back"
        http.add_routes(path="/c/{token}/decision", methods=[apigw.HttpMethod.GET], integration=api_int)
        http.add_routes(path="/c/{token}/photo/{msg}/{i}", methods=[apigw.HttpMethod.GET], integration=api_int)  # sent-photo thumbnails
        api_fn.add_environment("INBOUND_FN", inbound.function_name)
        inbound.grant_invoke(api_fn)
        http.add_routes(path="/api/{proxy+}", methods=[apigw.HttpMethod.ANY], integration=api_int, authorizer=jwt)

        inbound_domain = ctx("inbound_domain")
        if inbound_domain:
            bucket.add_to_resource_policy(iam.PolicyStatement(
                actions=["s3:PutObject"], principals=[iam.ServicePrincipal("ses.amazonaws.com")],
                resources=[bucket.arn_for_objects("inbound/*")], conditions={"StringEquals": {"aws:SourceAccount": self.account}}))
            rules = ses.ReceiptRuleSet(self, "MailRules", receipt_rule_set_name="tripclaim")
            rules.add_rule("Claims", recipients=[f"claim@{inbound_domain}"], scan_enabled=True,
                           actions=[ses_actions.S3(bucket=bucket, object_key_prefix="inbound/")])

        if ctx("agentcore") == "true":
            image = ecr_assets.DockerImageAsset(self, "AgentImage", directory=str(ROOT), file="Dockerfile.agentcore",
                                                platform=ecr_assets.Platform.LINUX_ARM64, exclude=ASSET_EXCLUDE)
            role = iam.Role(self, "AgentRuntimeRole", assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"))
            image.repository.grant_pull(role)
            table.grant_read_write_data(role)
            bucket.grant_read_write(role)
            for st in bedrock_statements:  # the same Bedrock scope as the Lambdas
                role.add_to_policy(st)
            role.add_to_policy(iam.PolicyStatement(
                actions=["ecr:GetAuthorizationToken",
                         "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "xray:PutTraceSegments",
                         "cloudwatch:PutMetricData"], resources=["*"]))  # these actions take no resource-level scope
            for st in messaging_statements:  # sending: the same account/Region scope as the Lambdas
                role.add_to_policy(st)
            runtime = agentcore.CfnRuntime(
                self, "AgentRuntime", agent_runtime_name="tripclaim_agent", role_arn=role.role_arn,
                agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                    container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(container_uri=image.image_uri)),
                network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(network_mode="PUBLIC"),
                environment_variables={**common_env, "PUBLIC_BASE_URL": http.api_endpoint})
            runtime.node.add_dependency(role)
            for f in (inbound, email_in):
                f.add_environment("AGENT_RUNTIME_ARN", runtime.attr_agent_runtime_arn)
            CfnOutput(self, "AgentRuntimeArn", value=runtime.attr_agent_runtime_arn)

        CfnOutput(self, "DashboardUrl", value=f"{http.api_endpoint}/")
        CfnOutput(self, "InboundTopicArn", value=topic.topic_arn, description="Set as event destination for WhatsApp and SMS/RCS")
        CfnOutput(self, "FlightEventBus", value=bus.event_bus_name)
        CfnOutput(self, "DocsBucket", value=bucket.bucket_name)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "LoginDomain", value=domain.base_url())
