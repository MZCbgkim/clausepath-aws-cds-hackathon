"""Write docs/architecture.drawio (AWS Architecture Icons, editable in draw.io) and export docs/architecture.png.

Run: .venv/bin/python docs/make_architecture.py   (needs the draw.io desktop CLI: `drawio`)
The numbered circles follow one claim: (1) flight cancelled ... (6) the reviewer's decision back to the traveler.
Badges say in words what the demo runs live (SES sending) and what it simulates (the SMS/RCS carrier, SES receiving).
"""
import subprocess
from html import escape
from pathlib import Path

HERE = Path(__file__).parent
cells, n = [], [0]

# AWS icon category colours (2023 set)
AI, COMPUTE, DB, STORAGE, INTEG, SEC, NET, MGMT, BIZ = ("#01A88D", "#ED7100", "#C925D1", "#7AA116", "#E7157B",
                                                       "#DD344C", "#8C4FFF", "#E7157B", "#DD344C")
INK, MUTED = "#232F3E", "#5A6B86"


def nid():
    n[0] += 1
    return f"n{n[0]}"


def cell(value, style, x, y, w, h, parent="1", vertex=True):
    i = nid()
    cells.append(f'<mxCell id="{i}" value="{escape(value)}" style="{style}" {"vertex" if vertex else "edge"}="1" parent="{parent}">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return i


def icon(res, color, x, y, label, size=64):
    return cell(label, "sketch=0;outlineConnect=0;fontColor=#232F3E;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;"
                f"verticalAlign=top;align=center;html=1;fontSize=13;aspect=fixed;shape=mxgraph.aws4.resourceIcon;"
                f"resIcon=mxgraph.aws4.{res};fillColor={color};", x, y, size, size)


def badge(text_, color, x, y):
    w = 12 + 7.4 * len(text_)
    cell(text_, f"rounded=1;arcSize=50;fillColor={color};strokeColor=none;fontColor=#ffffff;fontStyle=1;fontSize=10;html=1;",
         x, y, w, 20)


def step(k, x, y):
    cell(str(k), f"ellipse;fillColor={INK};strokeColor=#ffffff;strokeWidth=2;fontColor=#ffffff;fontStyle=1;fontSize=14;html=1;",
         x - 14, y - 14, 28, 28)


def edge(a, b, label="", dashed=False, both=False, points=(), exit=None, entry=None):
    i = nid()
    style = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#8D99AE;strokeWidth=2;fontSize=11;fontColor={MUTED};"
             f"labelBackgroundColor=#ffffff;endArrow=block;endFill=1;{'dashed=1;' if dashed else ''}"
             f"{'startArrow=block;startFill=1;' if both else ''}"
             + (f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;" if exit else "")
             + (f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;" if entry else ""))
    pts = "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in points)
    cells.append(f'<mxCell id="{i}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{a}" target="{b}">'
                 f'<mxGeometry relative="1" as="geometry">{"<Array as=" + chr(34) + "points" + chr(34) + ">" + pts + "</Array>" if pts else ""}'
                 f'</mxGeometry></mxCell>')


def group(label, x, y, w, h, gr, color, dashed=False, fill="none"):
    return cell(label, f"points=[];outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=13;fontStyle=1;"
                f"container=0;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.{gr};"
                f"strokeColor={color};fillColor={fill};verticalAlign=top;align=left;spacingLeft=30;fontColor={color};"
                f"{'dashed=1;' if dashed else 'dashed=0;'}", x, y, w, h)


def box(label, x, y, w, h, color):
    cell(label, f"rounded=1;arcSize=6;fillColor=none;strokeColor={color};dashed=1;html=1;verticalAlign=top;align=left;"
         f"spacingLeft=8;fontSize=12;fontStyle=1;fontColor={color};", x, y, w, h)


def text(value, x, y, w, h, size=13, color=MUTED, bold=False, align="left"):
    cell(value, f"text;html=1;whiteSpace=wrap;align={align};verticalAlign=top;fontSize={size};fontColor={color};"
         f"{'fontStyle=1;' if bold else ''}", x, y, w, h)


G = "<font color='#5A6B86'>{}</font>"
text("ClausePath · travel-claim concierge on AWS Communication Developer Services", 20, 10, 1300, 34, 24, INK, True)
text("A text at the airport, a chat with sources, documents checked on the spot, the claim packet by email. "
     "The agent prepares; the insurer decides.", 20, 46, 1300, 24, 14)

# people (outside the cloud)
trav = icon("mobile_client", INK, 40, 430, "Traveler<br>" + G.format("text · web chat · email"))
staff = icon("users", INK, 40, 830, "Insurer reviewers<br>" + G.format("dashboard · claims inbox"))

group("AWS Cloud · us-east-1", 190, 90, 1480, 880, "group_aws_cloud_alt", INK)
group("AWS Communication Developer Services", 225, 140, 330, 455, "group_aws_cloud", BIZ, dashed=True, fill="#FFF7F8")
eum = icon("end_user_messaging", BIZ, 300, 190, "AWS End User Messaging<br>" + G.format("SMS · RCS"))
badge("SIMULATED CARRIER", "#B25E09", 372, 186)
ses = icon("simple_email_service", BIZ, 300, 330, "Amazon SES<br>" + G.format("claim packet · decision letter"))
badge("LIVE", "#0F8A5F", 372, 326)
sesin = icon("simple_email_service", BIZ, 300, 470, "SES receiving<br>" + G.format("forwarded airline mail"))
badge("SIMULATED", "#B25E09", 372, 466)
web = icon("client", NET, 300, 640, "Web chat + secure page<br>" + G.format("no app · no login"))

sns = icon("sns", INTEG, 640, 300, "Amazon SNS<br>" + G.format("inbound + delivery events"))
badge("SIMULATED", "#B25E09", 712, 296)
evb = icon("eventbridge", INTEG, 640, 428, "Amazon EventBridge<br>" + G.format("flight disruption"))
api = icon("api_gateway", NET, 640, 640, "Amazon API Gateway<br>" + G.format("chat · secure page · staff API"))
cog = icon("cognito", SEC, 640, 830, "Amazon Cognito<br>" + G.format("admins · reviewers"))

lam = icon("lambda", COMPUTE, 900, 420, "AWS Lambda<br>" + G.format("channel router · rules · checklist"), 80)

brk = icon("bedrock", AI, 1170, 200, "Amazon Bedrock<br>" + G.format("Claude Opus 5.5 · Strands agent"))
grd = icon("bedrock", AI, 1170, 428, "Bedrock Guardrails<br>" + G.format("no promised outcomes"))
cell("<b>Every word to a traveler is checked</b><br>1 · compliance rules<br>2 · decision words<br>3 · Bedrock Guardrails<br>"
     "<font color='#5A6B86'>Decisions go out only in templates<br>the server fills from the claim.</font>",
     "rounded=1;arcSize=8;fillColor=#FDECEC;strokeColor=#DD344C;html=1;align=left;spacingLeft=10;fontSize=12;fontColor=#232F3E;"
     "whiteSpace=wrap;", 1350, 400, 290, 120)

box("Data · encrypted with AWS KMS", 860, 600, 790, 160, "#7AA116")
ddb = icon("dynamodb", DB, 908, 640, "Amazon DynamoDB<br>" + G.format("claims · messages · audit"))
s3 = icon("s3", STORAGE, 1100, 640, "Amazon S3<br>" + G.format("documents"))
s3a = icon("s3", STORAGE, 1290, 640, "S3 Object Lock<br>" + G.format("write-once audit copy"))
kms = icon("key_management_service", SEC, 1480, 640, "AWS KMS")
box("Operations", 860, 790, 420, 160, "#E7157B")
cw = icon("cloudwatch", MGMT, 908, 830, "Amazon CloudWatch<br>" + G.format("19 alarms → ops"))
sqs = icon("sqs", INTEG, 1100, 830, "Amazon SQS<br>" + G.format("failed events kept"))

# one claim, numbered
edge(evb, lam)
step(1, 800, 460)
edge(lam, eum, "router picks the channel, logs why", points=[(940, 160), (585, 160), (585, 222)], exit=(0.5, 0), entry=(1, 0.5))
step(2, 585, 160)
edge(eum, trav, "text + chat link", both=True, points=[(72, 222)], exit=(0, 0.5), entry=(0.5, 0))
edge(trav, web, "", both=True, points=[(158, 488), (158, 672)], exit=(1, 0.9), entry=(0, 0.5))
edge(web, api, both=True)
step(3, 520, 672)
edge(api, lam, points=[(800, 672), (800, 500)], exit=(1, 0.5), entry=(0, 0.85))
edge(sns, lam, points=[(800, 332), (800, 436)], exit=(1, 0.5), entry=(0, 0.2))
edge(lam, brk, "read photos · answer from the policy", points=[(1100, 436), (1100, 232)], exit=(1, 0.2), entry=(0, 0.5))
step(4, 1100, 330)
edge(lam, grd, entry=(0, 0.5), exit=(1, 0.5))
edge(lam, ses, "", points=[(920, 250), (600, 250), (600, 362)], exit=(0.25, 0), entry=(1, 0.5))
step(5, 600, 300)
edge(ses, staff, "claim packet", points=[(200, 375), (200, 790), (72, 790)], exit=(0, 0.7), entry=(0.5, 0))
edge(ses, trav, "decision letter", dashed=True, points=[(240, 349), (240, 449)], exit=(0, 0.3), entry=(1, 0.3))
edge(staff, cog, "sign in", both=True)
edge(cog, api)
step(6, 240, 400)

text("<b>One claim:</b> (1) flight cancelled → (2) traveler texted first, on the channel the router picks → (3) the same "
     "conversation continues in a web chat → (4) Claude reads photos and answers from the policy with sources; rules check dates, "
     "flight, place → (5) claim packet to the insurer by SES → (6) reviewers decide (two people for a partial payment or denial); "
     "the decision goes back by email and in the chat.", 20, 980, 1650, 40, 12)

xml = ('<mxfile host="drawio"><diagram name="ClausePath"><mxGraphModel dx="1700" dy="1000" grid="0" page="1" pageWidth="1720" '
       'pageHeight="1040" background="#ffffff"><root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells)
       + "</root></mxGraphModel></diagram></mxfile>")
src = HERE / "architecture.drawio"
src.write_text(xml)
subprocess.run(["drawio", "-x", "-f", "png", "-s", "2", "-b", "20", "-o", str(HERE / "architecture.png"), str(src)], check=True)
print(src, HERE / "architecture.png")
