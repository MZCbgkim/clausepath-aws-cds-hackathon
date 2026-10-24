# Channel setup

The steps that need an outside approval or DNS change, in the order to start them (the longest waits first).
Console menu names change; check the current AWS documentation as you go. Use **us-east-1** throughout (SES receiving).

## 1. WhatsApp — AWS End User Messaging Social

- [ ] A Meta Business account.
- [ ] AWS console → End User Messaging → Social messaging → link a WhatsApp Business Account (Meta embedded sign-up).
- [ ] Register and verify a sending phone number (a test number, not one used for personal WhatsApp).
- [ ] Event destination: an SNS topic (inbound messages and delivery statuses).
- [ ] Submit the four templates (category UTILITY). Until they are approved, only session messages inside the 24-hour
      window work.

| Template | Purpose | Body (en_US) |
| --- | --- | --- |
| `flight_disruption_alert` | Proactive disruption alert | Hi {{1}}, your flight {{2}} on {{3}} was {{4}}. Your travel policy may cover extra costs while you wait. Reply YES and we'll tell you what to keep. |
| `document_reminder` | Document reminder | Hi {{1}}, your claim for flight {{2}} still needs: {{3}}. Reply here or forward it to {{4}}. |
| `claim_received` | Claim received | Hi {{1}}, we received your claim {{2}}. We'll update you here when the insurer reviews it. |
| `claim_status_update` | Status change | Hi {{1}}, your claim {{2}} is now {{3}}. {{4}} |

- [ ] Quick-reply buttons on `flight_disruption_alert`: "YES" / "Not now".
- [ ] The same four templates in Korean (language `ko`); bodies are `TEMPLATES["ko"]` in `src/tripclaim/i18n.py`.
      `python -m tripclaim.wa_templates --waba <waba-id>` submits them with the same wording as `i18n.py`.
      Meta may classify the Korean disruption alert as MARKETING; `i18n.py` notes a factual alternative
      (`flight_disruption_notice`).

## 2. RCS testing agent — AWS End User Messaging SMS/RCS

A testing agent works within minutes without carrier approval, on pre-registered test devices.

- [ ] Console → End User Messaging SMS → RCS → create a testing agent.
- [ ] Register the demo phone number as a test device (check that the handset and carrier support RCS).
- [ ] Two-way: set the event destination to the same SNS topic as WhatsApp (one inbound router).
- [ ] Check the `pinpoint-sms-voice-v2` `send_rcs_message` parameters against the current API reference.
- [ ] (Optional) An SMS origination identity for the SMS fallback — check how long registration takes first.

## 3. SES receiving and sending

- [ ] A demo domain you can edit DNS for (e.g. a subdomain `claims.<domain>`).
- [ ] Verify the domain identity in SES us-east-1 (three DKIM CNAME records).
- [ ] MX record: `claims.<domain>` → `10 inbound-smtp.us-east-1.amazonaws.com`.
- [ ] Receipt rule: recipient `claim@claims.<domain>` → (1) store in S3 `inbound/` (2) invoke the Lambda.
- [ ] Mail that fails the spam or virus verdict is not processed.
- [ ] Sending: in the SES sandbox, verify the recipient addresses too, or request production access.
- [ ] Check SPF/DMARC alignment so outgoing mail does not land in spam.

## 4. General

- [ ] One SNS topic for inbound messages and status events, subscribed by the inbound router Lambda.
- [ ] Consent: seed the opt-in of test numbers and addresses on the traveler records.
- [ ] A cost alarm in AWS Budgets.
- [ ] No credentials in the repository: only `.env.example`; use git-secrets or gitleaks.

## 5. After `cdk deploy` (uses the stack outputs)

- [ ] WhatsApp phone number event destination → `InboundTopicArn` (End User Messaging Social console).
- [ ] Create the SMS/RCS configuration set `tripclaim` with an SNS event destination → `InboundTopicArn` (TEXT_* and
      RCS_* events); two-way inbound goes to the same topic.
- [ ] `aws ses set-active-receipt-rule-set --rule-set-name tripclaim`.
- [ ] Create a staff login in the Cognito user pool (`UserPoolId`) and sign in at `DashboardUrl`.
- [ ] `python -m tripclaim.seed --phone <demo phone> --email <demo email>`, then `--event`, and check that the alert arrives.
- [ ] Check the fields of a real RCS two-way inbound event (today an event whose `destinationNumber` equals
      `RCS_AGENT_ID` is treated as RCS).
