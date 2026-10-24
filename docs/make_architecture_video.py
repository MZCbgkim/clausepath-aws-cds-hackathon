"""The architecture for the demo video: 11 nodes, large labels, and one PNG per step of a claim.

Writes docs/video/architecture-video.drawio and exports docs/video/arch-all.png plus arch-step1.png ... arch-step6.png,
where everything outside the step is faded, so the edit can light the steps up one by one in time with the narration.
Full diagram: docs/make_architecture.py. Run: .venv/bin/python docs/make_architecture_video.py (needs the drawio CLI).
"""
import subprocess
from html import escape
from pathlib import Path

OUT = Path(__file__).parent / "video"
AI, COMPUTE, DB, STORAGE, INTEG, SEC, NET, BIZ = "#01A88D", "#ED7100", "#C925D1", "#7AA116", "#E7157B", "#DD344C", "#8C4FFF", "#DD344C"
INK, MUTED, LIVE, SIM = "#232F3E", "#5A6B86", "#0F8A5F", "#B25E09"

# key -> (resIcon, colour, x, y, name, detail, badge)
NODES = {
    "trav": ("mobile_client", INK, 60, 380, "Traveler", "phone", None),
    "staff": ("users", INK, 60, 650, "Insurer reviewers", "dashboard", None),
    "eum": ("end_user_messaging", BIZ, 380, 130, "AWS End User Messaging", "pinpoint-sms-voice-v2 · SendRcsMessage", ("SIMULATED CARRIER", SIM)),
    "api": ("api_gateway", NET, 380, 380, "Amazon API Gateway", "web chat · secure page", None),
    "ses": ("simple_email_service", BIZ, 380, 650, "Amazon SES", "sesv2 · send_email", ("LIVE", LIVE)),
    "evb": ("eventbridge", INTEG, 700, 150, "Amazon EventBridge", "flight cancelled", None),
    "lam": ("lambda", COMPUTE, 700, 380, "AWS Lambda", "channel router · rules", None),
    "brk": ("bedrock", AI, 1020, 250, "Amazon Bedrock", "Claude", None),
    "grd": ("bedrock", AI, 1020, 520, "Bedrock Guardrails", "wording check", None),
    "ddb": ("dynamodb", DB, 1322, 260, "Amazon DynamoDB", "claims", None),
    "s3": ("s3", STORAGE, 1322, 520, "Amazon S3", "documents", None),
}
S = 96  # icon size
# (from, to, label, exit, entry, waypoints, dashed, both)
EDGES = {
    "e1": ("evb", "lam", "", (0.5, 1), (0.5, 0), [], False, False),
    "e2a": ("lam", "eum", "", (0, 0.15), (1, 0.5), [(600, 394), (600, 178)], False, True),
    "e2b": ("eum", "trav", "text ↔ reply", (0, 0.5), (0.5, 0), [(108, 178)], False, True),
    "e3a": ("trav", "api", "web chat", (1, 0.5), (0, 0.5), [], False, True),
    "e3b": ("api", "lam", "", (1, 0.5), (0, 0.5), [], False, True),
    "e4a": ("lam", "brk", "", (1, 0.3), (0, 0.5), [(920, 409), (920, 298)], False, True),
    "e4b": ("lam", "grd", "", (1, 0.7), (0, 0.5), [(920, 447), (920, 568)], False, False),
    "e5a": ("lam", "ses", "", (0, 0.85), (1, 0.5), [(620, 462), (620, 698)], False, False),
    "e5b": ("ses", "staff", "claim packet", (0, 0.5), (1, 0.5), [], False, False),
    "e6a": ("staff", "api", "", (1, 0.2), (0, 0.85), [(250, 669), (250, 462)], False, False),
    "e6b": ("ses", "trav", "", (0, 0.15), (1, 0.85), [(305, 664), (305, 462)], True, False),
}
STEPS = {  # what lights up for each numbered step, and where its number sits
    1: ({"evb", "lam"}, {"e1"}, (748, 300)),
    2: ({"lam", "eum", "trav"}, {"e2a", "e2b"}, (600, 290)),
    3: ({"trav", "api", "lam"}, {"e3a", "e3b"}, (560, 428)),
    4: ({"lam", "brk", "grd"}, {"e4a", "e4b"}, (920, 360)),
    5: ({"lam", "ses", "staff"}, {"e5a", "e5b"}, (620, 600)),
    6: ({"staff", "api", "ses", "trav"}, {"e6a", "e6b"}, (277, 600)),
}


def build(step=None):
    cells, k = [], [0]

    def add(xml):
        k[0] += 1
        cells.append(xml.replace("{id}", f"c{k[0]}"))
        return f"c{k[0]}"

    on_n, on_e = (STEPS[step][0], STEPS[step][1]) if step else (set(NODES), set(EDGES))
    fade = lambda on: "" if on else "opacity=18;textOpacity=18;"
    ids = {}
    for key, (res, col, x, y, name, detail, badge) in NODES.items():
        label = f"<b>{escape(name)}</b><br><font color='{MUTED}'>{escape(detail)}</font>"
        pos = "verticalLabelPosition=top;verticalAlign=bottom;" if key == "evb" else "verticalLabelPosition=bottom;verticalAlign=top;"
        ids[key] = add(f'<mxCell id="{{id}}" value="{escape(label)}" style="sketch=0;outlineConnect=0;fontColor={INK};strokeColor=#ffffff;'
                       f'{pos}align=center;html=1;fontSize=18;aspect=fixed;'
                       f'shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{res};fillColor={col};{fade(key in on_n)}" vertex="1" parent="1">'
                       f'<mxGeometry x="{x}" y="{y}" width="{S}" height="{S}" as="geometry"/></mxCell>')
        if badge:
            text, bg = badge
            w = 20 + 10.5 * len(text)
            add(f'<mxCell id="{{id}}" value="{text}" style="rounded=1;arcSize=50;fillColor={bg};strokeColor=none;fontColor=#ffffff;'
                f'fontStyle=1;fontSize=15;html=1;{fade(key in on_n)}" vertex="1" parent="1">'
                f'<mxGeometry x="{x + S - 16}" y="{y - 16}" width="{w}" height="30" as="geometry"/></mxCell>')
    add(f'<mxCell id="{{id}}" value="Data · read and written by Lambda" style="rounded=1;arcSize=6;fillColor=none;strokeColor={STORAGE};'
        f'dashed=1;strokeWidth=2;html=1;verticalAlign=top;align=center;fontSize=15;fontStyle=1;fontColor={STORAGE};{fade(step is None)}" '
        f'vertex="1" parent="1"><mxGeometry x="1240" y="205" width="260" height="520" as="geometry"/></mxCell>')
    for key, (a, b, label, ex, en, pts, dashed, both) in EDGES.items():
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor={'#3B6CF0' if step else '#8D99AE'};strokeWidth={4 if step else 3};"
                 f"fontSize=16;fontColor={MUTED};labelBackgroundColor=#ffffff;endArrow=block;endFill=1;"
                 f"exitX={ex[0]};exitY={ex[1]};exitDx=0;exitDy=0;entryX={en[0]};entryY={en[1]};entryDx=0;entryDy=0;"
                 f"{'dashed=1;' if dashed else ''}{'startArrow=block;startFill=1;' if both else ''}{fade(key in on_e)}")
        p = "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in pts)
        add(f'<mxCell id="{{id}}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{ids[a]}" target="{ids[b]}">'
            f'<mxGeometry relative="1" as="geometry">{"<Array as=" + chr(34) + "points" + chr(34) + ">" + p + "</Array>" if p else ""}</mxGeometry></mxCell>')
    for n, (_, _, (x, y)) in STEPS.items():
        on = step is None or step == n
        add(f'<mxCell id="{{id}}" value="{n}" style="ellipse;fillColor={INK};strokeColor=#ffffff;strokeWidth=3;fontColor=#ffffff;fontStyle=1;'
            f'fontSize=20;html=1;{fade(on)}" vertex="1" parent="1"><mxGeometry x="{x - 20}" y="{y - 20}" width="40" height="40" as="geometry"/></mxCell>')
    return ('<mxfile host="drawio"><diagram name="video"><mxGraphModel grid="0" page="1" pageWidth="1500" pageHeight="820" '
            'background="#ffffff"><root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>")


OUT.mkdir(exist_ok=True)
for step in [None, 1, 2, 3, 4, 5, 6]:
    name = "arch-all" if step is None else f"arch-step{step}"
    src = OUT / f"{name}.drawio"
    src.write_text(build(step))
    subprocess.run(["drawio", "-x", "-f", "png", "-s", "2", "-b", "40", "-o", str(OUT / f"{name}.png"), str(src)], check=True,
                   capture_output=True)
    print(OUT / f"{name}.png")
