"""Render the sample policy and FAQ (English and Korean) to PDF.

  .venv/bin/python policy/make_pdfs.py          -> policy/pdf/*.pdf

Fonts: Nanum Gothic (SIL Open Font License) for Korean and English, downloaded to data/fonts/.
Supports the Markdown used in these files: #/## headings, paragraphs, **bold**, [CLAUSE-ID] tags, (a) items, tables.
"""
import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent
FONTS = HERE.parent / "data" / "fonts"
BLUE = colors.HexColor("#1F4FD8")
INK = colors.HexColor("#1B2230")
MUTED = colors.HexColor("#5B6474")

FONT_URL = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/{}.ttf"


def ensure_fonts():
    import urllib.request
    FONTS.mkdir(parents=True, exist_ok=True)
    for name in ("NanumGothic-Regular", "NanumGothic-Bold"):
        path = FONTS / f"{name}.ttf"
        if not path.exists():
            urllib.request.urlretrieve(FONT_URL.format(name), path)


ensure_fonts()
pdfmetrics.registerFont(TTFont("Nanum", str(FONTS / "NanumGothic-Regular.ttf")))
pdfmetrics.registerFont(TTFont("Nanum-Bold", str(FONTS / "NanumGothic-Bold.ttf")))
pdfmetrics.registerFontFamily("Nanum", normal="Nanum", bold="Nanum-Bold", italic="Nanum", boldItalic="Nanum-Bold")

S = {
    "title": ParagraphStyle("title", fontName="Nanum-Bold", fontSize=20, leading=26, textColor=INK, spaceAfter=4),
    "lead": ParagraphStyle("lead", fontName="Nanum", fontSize=9.5, leading=14, textColor=MUTED, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName="Nanum-Bold", fontSize=13, leading=18, textColor=BLUE, spaceBefore=12, spaceAfter=6),
    "body": ParagraphStyle("body", fontName="Nanum", fontSize=10.5, leading=16.5, textColor=INK, spaceAfter=6, alignment=TA_LEFT),
    "item": ParagraphStyle("item", fontName="Nanum", fontSize=10.5, leading=16, textColor=INK, leftIndent=14, spaceAfter=2),
    "q": ParagraphStyle("q", fontName="Nanum-Bold", fontSize=10.5, leading=16, textColor=INK, spaceBefore=6, spaceAfter=1),
    "cell": ParagraphStyle("cell", fontName="Nanum", fontSize=10, leading=14, textColor=INK),
}


def inline(text):
    t = html.escape(text, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    # clause tags: [TD-1] -> blue bold; (d) after a tag stays attached
    return re.sub(r"\[([A-Z]+-\d+)\]", r'<font color="#1F4FD8"><b>[\1]</b></font>', t)


def flowables(md):
    out, table, first_para = [], [], True
    lines = md.splitlines()

    def flush_table():
        nonlocal table
        if not table:
            return
        rows = [[Paragraph(inline(c.strip()), S["cell"]) for c in r.strip("|").split("|")] for r in table
                if not re.match(r"^\|\s*-", r)]
        t = Table(rows, hAlign="LEFT", colWidths=[45 * mm, 45 * mm, 45 * mm][:len(rows[0])])
        t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "Nanum"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
                               ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D5D9E2")),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        out.extend([t, Spacer(1, 6)])
        table = []

    for line in lines:
        s = line.strip()
        if s.startswith("|"):
            table.append(s)
            continue
        flush_table()
        if not s:
            continue
        if s.startswith("# "):
            out.append(Paragraph(inline(s[2:]), S["title"]))
        elif s.startswith("## "):
            out.append(Paragraph(inline(s[3:]), S["h2"]))
        elif re.match(r"^\([a-z]\)", s):
            out.append(Paragraph(inline(s), S["item"]))
        elif re.match(r"^\*\*.+\*\*$", s):  # FAQ question on its own line
            out.append(Paragraph(inline(s[2:-2]), S["q"]))
        elif first_para and out and not s.startswith("["):
            out.append(Paragraph(inline(s), S["lead"]))
        else:
            out.append(Paragraph(inline(s), S["body"]))
        if s.startswith("# "):
            first_para = True
        elif not s.startswith("#"):
            first_para = False
    flush_table()
    return out


def footer(label):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Nanum", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 12 * mm, label)
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, str(doc.page))
        canvas.setStrokeColor(BLUE)
        canvas.setLineWidth(2)
        canvas.line(20 * mm, A4[1] - 14 * mm, A4[0] - 20 * mm, A4[1] - 14 * mm)
        canvas.restoreState()
    return draw


DOCS = [
    ("sample_policy.md", "ClausePath_Sample_Policy_EN.pdf", "Sample Travel Insurance Policy · fictional, for the ClausePath demo"),
    ("sample_policy_ko.md", "ClausePath_Sample_Policy_KO.pdf", "샘플 여행자보험 약관 · ClausePath 데모용 가상 약관"),
    ("faq_en.md", "ClausePath_FAQ_EN.pdf", "Travel Delay Cover FAQ · fictional, for the ClausePath demo"),
    ("faq_ko.md", "ClausePath_FAQ_KO.pdf", "항공기 지연 담보 FAQ · ClausePath 데모용"),
]


def main():
    out_dir = HERE / "pdf"
    out_dir.mkdir(exist_ok=True)
    for src, dst, label in DOCS:
        doc = SimpleDocTemplate(str(out_dir / dst), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                                topMargin=22 * mm, bottomMargin=20 * mm, title=label, author="ClausePath demo")
        story = flowables((HERE / src).read_text())
        doc.build(story, onFirstPage=footer(label), onLaterPages=footer(label))
        print(out_dir / dst)


if __name__ == "__main__":
    main()
