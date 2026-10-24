"""Public pages (no login): about, privacy policy, terms. Required by messaging-channel registrations
(RCS agent, WhatsApp business profile) and linked from the agent profile."""

STYLE = ("font-family:system-ui,-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;font-size:16px;"
         "line-height:1.65;color:#1b2230;max-width:760px;margin:40px auto;padding:0 20px")
NAV = '<p style="color:#4f5868"><b style="color:#1f4fd8">ClausePath</b> · <a href="/about">About</a> · <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a></p>'

ABOUT = """<h1>ClausePath</h1>
<p>ClausePath is a travel-claim assistant built for the AWS Communication Developer Services Agentic AI Partner Hackathon (2026).
When a flight is cancelled or delayed, it tells insured travelers what their travel insurance may cover, which documents to keep,
checks the receipts and letters they send, and prepares a complete claim for the insurer by email.</p>
<p><b>This is a demonstration.</b> The insurer, airline and policy shown in the demo are fictional. Messages are sent only to
test devices registered by the project team. The insurer, not ClausePath, decides every claim.</p>
<h2>Channels</h2>
<p>RCS and SMS (AWS End User Messaging), email (Amazon SES). You can stop messages at any time by replying STOP.</p>
<h2>Contact</h2>
<p>For questions about this demonstration, reply to any ClausePath message or use the contact link on this page.</p>"""

PRIVACY = """<h1>Privacy policy</h1>
<p>Last updated: 9 October 2026. This policy covers the ClausePath demonstration.</p>
<h2>What we collect</h2>
<ul><li>Your phone number and email address, as registered for the demonstration.</li>
<li>Messages you send to ClausePath and the photos or emails you share (receipts, airline letters, boarding passes).</li>
<li>Information read from those documents (dates, amounts, flight numbers).</li></ul>
<h2>What we do not collect by message</h2>
<p>We never ask for bank details, passport or ID images by message. These are entered only on a secure page, and in the
demonstration bank details are not stored. If you send an ID photo by message, it is not kept.</p>
<h2>How we use it</h2>
<p>Only to guide your travel-delay claim and to prepare the claim packet for the insurer. Documents are analysed by an AI model
on Amazon Bedrock. We do not sell or share your data, and we do not use it for advertising.</p>
<h2>Storage and retention</h2>
<p>Data is stored in Amazon Web Services (us-east-1), encrypted at rest. Documents are deleted after 90 days; raw inbound
email after 30 days.</p>
<h2>Your choices</h2>
<p>Reply STOP to stop messages. Ask us by reply to delete your data.</p>"""

TERMS = """<h1>Terms of use</h1>
<p>Last updated: 9 October 2026.</p>
<ol><li>ClausePath is a demonstration service for a hackathon and is provided as is, without warranty.</li>
<li>Information from ClausePath about insurance cover is guidance only. It is not an insurance decision, legal or financial
advice. The insurer decides whether and how much to pay.</li>
<li>Only send documents you are entitled to share. Do not send bank details or ID documents by message.</li>
<li>Messages are sent to registered test devices only. Reply STOP to opt out at any time.</li>
<li>We may change or stop the demonstration at any time.</li></ol>"""

PAGES = {"/about": ("About ClausePath", ABOUT), "/privacy": ("Privacy policy", PRIVACY), "/terms": ("Terms of use", TERMS)}


def render(path):
    title, body = PAGES[path]
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{title} · ClausePath</title></head>'
            f'<body style="{STYLE}">{NAV}{body}</body></html>')
