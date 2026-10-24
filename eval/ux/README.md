# Persona-based usability reviews (AI-simulated, not user testing)

**These are not tests with real users.** Each round, six LLM agents were each given one persona and asked to review
ClausePath from that person's point of view. We used them to find problems early and often during the hackathon,
the way a design review would. They do not replace testing with real travelers and claims staff, which is the next
step before production.

The reports are in Korean (the personas are mostly Korean users); this page summarises them in English.

## Method

- **Material** per round: real screenshots of the build (mobile chat at 390 px, desk at 1280 px, the claim packet),
  a brief with the scenario and what changed since the last round, and read-only access to the source.
  The chats in the screenshots were produced by the real agent (Claude on Amazon Bedrock) on a local server.
- **Rubric**: nine metrics scored 1–5 (5 = excellent, 3 = acceptable with friction, 1 = blocks the task), an
  NPS-style recommend score 0–10, and issues ranked P0–P2 with a proposed fix.
  - M1 usefulness · M2 comprehensibility · M3 ease of use · M4 feature completeness · M5 trust and transparency ·
    M6 visual design · M7 efficiency · M8 error prevention and recovery
  - M9, per role: travelers — mobile and accessibility; adjuster — decision support; contact centre — case handling;
    operations — control and compliance.
- **Loop**: fix the P0/P1 issues, re-capture the screens, re-score with the same personas. 17 rounds, 9–10 Oct 2026.

## Personas

| Role | Persona | Situation |
| --- | --- | --- |
| Traveler | 김지수, 34, office worker | At Haneda right after the cancellation, 30% battery, does not know her plan |
| Traveler | 박영자, 67, retired teacher | Her daughter bought the policy; large text; worried about smishing |
| Traveler | Jordan Taylor, 29 | English only, distrusts insurers, wants amounts in USD |
| Staff | 이정훈, 41, claims adjuster | 40–60 claims a day in typhoon season; limits, FX, denials |
| Staff | 한소라, 29, contact-centre agent | Call surges, finding a claim by phone number, hand-offs |
| Staff | 정혜원, 45, head of digital channels and claims operations | The adoption decision: controls, compliance, KPIs |

## Results

| Persona | v1 | v14 | v15 | v16 | v17 |
| --- | --- | --- | --- | --- | --- |
| 김지수 | 3.33 | 4.56 | 4.67 | 4.56 | 4.56 |
| 박영자 | 3.00 | 3.89 | 4.00 | 4.00 | 4.00 |
| Jordan | 3.33 | 4.33 | 4.44 | 4.44 | 4.44 |
| 이정훈 (adjuster) | 2.67 | 4.00 | 4.00 | 4.11 | 4.33 |
| 한소라 (contact centre) | 2.56 | 4.56 | 4.44 | 4.56 | 4.11 |
| 정혜원 (operations) | 3.00 | 4.00 | 4.00 | 3.89 | 3.78 |
| **Average** (recommend) | **2.98** (6.0) | **4.22** | **4.26** | **4.26** (8.2) | **4.20** (8.2) |

Scores did not only go up: in v17 the contact-centre persona dropped to 4.11 because a new fraud-hold feature
reached her without a call script, and the operations persona stayed below 4 until the end. The last round's
remaining conditions are recorded as known limits in `persona_eval_v17.md`.

## Limits of the method

Research on LLM-simulated users (e.g. UXAgent, 2025; Nielsen Norman Group's comparisons with real studies) finds
them useful for early iteration but too agreeable and not always faithful to real behaviour. We read the scores as
direction, not as measurements, and treated the concrete issues each persona raised as the useful output.

## Files

- `persona_eval_v1.md` … `persona_eval_v17.md` — each round's scores, issues and what was fixed.
- `tools/` — scripts that set up the demo state and serve the chat for the screenshots.
- The screenshots and briefs (`screens/`) are not included in this repository.
