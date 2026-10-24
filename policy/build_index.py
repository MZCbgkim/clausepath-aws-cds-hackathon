"""Build the evidence index for the RAG agent: chunks, where each chunk sits in its PDF, page images, embeddings.

  .venv/bin/python policy/make_pdfs.py && .venv/bin/python policy/build_index.py [--no-embed]

Output (shipped with the package): src/tripclaim/web/docs/
  index.json         chunks: id, doc, lang, section, clause, text, locations [{page, rects (0..1, top-left origin)}], vector
  <doc>-<page>.jpg   rendered pages the chat shows under the highlight
  <doc>.pdf          the original PDF for download
"""
import json
import re
import shutil
import sys
from pathlib import Path

import pypdfium2 as pdfium

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "src" / "tripclaim" / "web" / "docs"
DOCS = [  # doc id, source markdown, pdf, language, kind, title
    ("policy_en", "sample_policy.md", "ClausePath_Sample_Policy_EN.pdf", "en", "policy", "Sample Travel Insurance Policy"),
    ("policy_ko", "sample_policy_ko.md", "ClausePath_Sample_Policy_KO.pdf", "ko", "policy", "샘플 여행자보험 약관"),
    ("faq_en", "faq_en.md", "ClausePath_FAQ_EN.pdf", "en", "faq", "Travel Delay Cover FAQ"),
    ("faq_ko", "faq_ko.md", "ClausePath_FAQ_KO.pdf", "ko", "faq", "항공기 지연 담보 FAQ"),
]
HEADER_PT, FOOTER_PT = 40, 57  # margins drawn by make_pdfs.py (header rule, footer label + page number)
EMBED_MODEL = "amazon.titan-embed-text-v2:0"


def plain(md):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", md).replace("|", " ").strip()


def chunks_from_markdown(md, doc, lang, kind):
    """Policy: one chunk per clause ([TD-3] + its (a)…(e) items / table). FAQ: one chunk per question and answer."""
    out, section, cur = [], "", None
    lines = [l.strip() for l in md.splitlines()]
    for i, s in enumerate(lines):
        if s.startswith("## "):
            section, cur = s[3:], None
            continue
        if not s or s.startswith("# ") or re.match(r"^\|\s*-", s):
            continue
        if kind == "policy":
            m = re.match(r"^\[([A-Z]+-\d+)\]", s)
            if m:
                cur = {"clause": m.group(1), "lines": [s]}
                out.append(cur)
            elif cur and (s.startswith("(") or s.startswith("|") or not s.startswith("[")):
                cur["lines"].append(s)
        else:
            if re.match(r"^\*\*.+\*\*$", s):
                cur = {"clause": None, "lines": [s]}
                out.append(cur)
            elif cur:
                cur["lines"].append(s)
        if cur is not None:
            cur["section"] = section
    for n, c in enumerate(out, 1):
        text = "\n".join(plain(l) for l in c["lines"])
        c.update(id=f"{doc}#{c['clause'] or n}", doc=doc, lang=lang, kind=kind, text=text,
                 cites=sorted(set(re.findall(r"\[([A-Z]+-\d+)\]", text))))
        del c["lines"]
    return out


def page_chars(pdf):
    """Every visible character of the document (headers/footers dropped) with its page and box."""
    seq = []
    for pno in range(len(pdf)):
        page = pdf[pno]
        w, h = page.get_size()
        tp = page.get_textpage()
        text = tp.get_text_range(0, tp.count_chars())
        for i, ch in enumerate(text):
            if ch.isspace():
                continue
            x0, y0, x1, y1 = tp.get_charbox(i)
            if y1 > h - HEADER_PT or y0 < FOOTER_PT:
                continue
            seq.append((ch, pno, (x0 / w, 1 - y1 / h, x1 / w, 1 - y0 / h)))
    return seq


def locate(seq, text):
    """Find the chunk's characters (whitespace ignored) and merge their boxes into one rectangle per line."""
    hay = "".join(c for c, _, _ in seq)
    needle = re.sub(r"\s+", "", text)
    start = hay.find(needle)
    if start < 0:
        raise ValueError(f"not found in PDF: {text[:60]!r}")
    lines = []
    for _, pno, (x0, y0, x1, y1) in seq[start:start + len(needle)]:
        cy = (y0 + y1) / 2  # group by vertical centre: punctuation sits lower than letters on the same line
        last = lines[-1] if lines else None
        if last and last["page"] == pno and abs(last["cy"] - cy) < 0.009:
            last.update(x0=min(last["x0"], x0), x1=max(last["x1"], x1), y0=min(last["y0"], y0), y1=max(last["y1"], y1))
        else:
            lines.append({"page": pno, "cy": cy, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    pages = {}
    for l in lines:
        pages.setdefault(l["page"], []).append([round(l["x0"] - 0.006, 4), round(l["y0"] - 0.004, 4),
                                                round(l["x1"] + 0.006, 4), round(l["y1"] + 0.004, 4)])
    return [{"page": p + 1, "rects": r} for p, r in pages.items()]


def embed(texts):
    import boto3
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    out = []
    for t in texts:
        res = client.invoke_model(modelId=EMBED_MODEL, body=json.dumps({"inputText": t, "dimensions": 512, "normalize": True}))
        out.append([round(v, 5) for v in json.loads(res["body"].read())["embedding"]])
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    chunks, docs = [], {}
    for doc, src, pdf_name, lang, kind, title in DOCS:
        pdf_path = HERE / "pdf" / pdf_name
        pdf = pdfium.PdfDocument(str(pdf_path))
        seq = page_chars(pdf)
        for c in chunks_from_markdown((HERE / src).read_text(), doc, lang, kind):
            c["locations"] = locate(seq, c["text"])
            chunks.append(c)
        for pno in range(len(pdf)):
            pdf[pno].render(scale=2).to_pil().convert("RGB").save(OUT / f"{doc}-{pno + 1}.jpg", quality=82)
        shutil.copy(pdf_path, OUT / f"{doc}.pdf")
        docs[doc] = {"title": title, "lang": lang, "kind": kind, "pages": len(pdf), "pdf": f"{doc}.pdf"}
    if "--no-embed" not in sys.argv:
        for c, v in zip(chunks, embed([f"{c['section']}\n{c['text']}" for c in chunks])):
            c["vector"] = v
    (OUT / "index.json").write_text(json.dumps({"model": EMBED_MODEL, "docs": docs, "chunks": chunks}, ensure_ascii=False))
    print(f"{len(chunks)} chunks from {len(docs)} documents -> {OUT}")


if __name__ == "__main__":
    main()
