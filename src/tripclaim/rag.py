"""Retrieval over the policy and FAQ PDFs, with the exact place in each PDF so the chat can highlight it.

Index: web/docs/index.json, built by policy/build_index.py (chunks, PDF locations, Titan v2 embeddings).
"""
import json
import math
import re
from functools import lru_cache
from importlib import resources

DOCS = resources.files("tripclaim.web").joinpath("docs")
LANG_BONUS, CLAUSE_BONUS = 0.08, 0.2


@lru_cache
def index():
    return json.loads(DOCS.joinpath("index.json").read_text())


@lru_cache
def version():
    """Changes whenever the documents or the clause ontology change: URLs carry it, so browsers can cache them forever."""
    import hashlib
    h = hashlib.sha256(DOCS.joinpath("index.json").read_bytes())
    h.update(DOCS.joinpath("ontology.json").read_bytes())
    return h.hexdigest()[:10]


@lru_cache
def ontology():
    return json.loads(DOCS.joinpath("ontology.json").read_text())


def title(c):
    """Short name of a clause ("항공기 지연으로 인한 보험기간 자동 연장") or the FAQ question."""
    named = ontology().get("titles", {}).get(c.get("clause") or "", {})
    if c["lang"] in named:
        return named[c["lang"]]
    first = c["text"].splitlines()[0]
    if c["kind"] == "faq":
        return first
    body = re.sub(r"^\[[A-Z]+-\d+\]\s*", "", first)
    m = re.match(r"(.{2,40}?)[.:：]", body)
    return m.group(1) if m else (body[:24] + "…" if len(body) > 24 else body)


@lru_cache
def clause_name(clause, lang):
    """"TD-3" -> "보상하는 비용의 종류" in the traveler's language (falls back to the code)."""
    c = by_id().get(f"policy_{lang}#{clause}") or by_id().get(f"policy_en#{clause}")
    return title(c) if c else clause


def atlas(lang):
    """The clause graph for one language: nodes (with where each sits in its PDF) and typed edges.
    Edges: the authored ontology, explicit [ID] mentions inside clauses, and FAQ answers explaining clauses."""
    ont = ontology()
    chunks = [c for c in index()["chunks"] if c["lang"] == lang]
    pid = {c["clause"]: c["id"] for c in chunks if c["kind"] == "policy"}
    edges, seen = [], set()

    def add(a, b, kind):
        if a and b and a != b and (a, b) not in seen and (b, a) not in seen:
            seen.add((a, b))
            edges.append([a, b, kind])
    for a, b, kind in ont["relations"]:
        add(pid.get(a), pid.get(b), kind)
    for c in chunks:
        for ref in c.get("cites", []):
            add(c["id"], pid.get(ref), "explains" if c["kind"] == "faq" else "cites")
    nodes = []
    for c in chunks:
        doc = index()["docs"][c["doc"]]
        nodes.append({"id": c["id"], "clause": c.get("clause"), "kind": c["kind"], "title": title(c), "document": doc["title"],
                      "pdf": f"/docs/{doc['pdf']}?v={version()}#page={c['locations'][0]['page']}",
                      # the clause as text, so a phone can show it at reading size above the page image
                      "text": re.sub(r"^\[[A-Z]+-\d+\]\s*", "", c.get("text") or "") if c["kind"] == "policy" else None,
                      "pages": [{"page": loc["page"], "image": f"/docs/{c['doc']}-{loc['page']}.jpg?v={version()}",
                                 "rects": loc["rects"]} for loc in c["locations"]]})
    types = {k: {"label": v.get(lang, v["en"]), "color": v["color"]} for k, v in ont["types"].items()}
    return {"lang": lang, "version": version(), "types": types, "nodes": nodes, "edges": edges}


@lru_cache
def by_id():
    return {c["id"]: c for c in index()["chunks"]}


def bedrock_embedder(region):
    import boto3
    client = boto3.client("bedrock-runtime", region_name=region)

    def embed(text):
        res = client.invoke_model(modelId=index()["model"],
                                  body=json.dumps({"inputText": text, "dimensions": 512, "normalize": True}))
        return json.loads(res["body"].read())["embedding"]
    return embed


def _tokens(text):
    """Words for Latin text, character bigrams for Hangul (no Korean tokenizer needed)."""
    text = text.lower()
    words = re.findall(r"[a-z0-9]+", text)
    for run in re.findall(r"[가-힣]+", text):
        words += [run[i:i + 2] for i in range(max(1, len(run) - 1))]
    return set(words)


def search(query, locale="en", k=4, embed=None, kinds=None):
    """Top-k chunks. Semantic when an embedder is given (falls back to keyword overlap), boosted for the
    traveler's language and for clause IDs named in the query."""
    named = set(re.findall(r"\b([A-Z]+-\d+)\b", query.upper()))
    qvec = None
    if embed:
        try:
            qvec = embed(query)
        except Exception:  # Bedrock unavailable: keyword retrieval still answers
            qvec = None
    qtok = _tokens(query)
    scored = []
    for c in index()["chunks"]:
        if kinds and c["kind"] not in kinds:
            continue
        if qvec and c.get("vector"):
            score = sum(a * b for a, b in zip(qvec, c["vector"]))
        else:
            ctok = _tokens(c["section"] + " " + c["text"])
            score = len(qtok & ctok) / math.sqrt(len(ctok) + 1)
        score += LANG_BONUS if c["lang"] == locale else 0
        score += CLAUSE_BONUS if c.get("clause") in named else 0
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [summary(c, round(s, 3)) for s, c in scored[:k]]


def summary(c, score=None):
    doc = index()["docs"][c["doc"]]
    out = {"id": c["id"], "document": doc["title"], "kind": c["kind"], "language": c["lang"],
           "page": c["locations"][0]["page"], "section": c["section"], "clause": c.get("clause"), "text": c["text"]}
    if score is not None:
        out["score"] = score
    return out


def evidence(chunk_id, base_url=""):
    """What the chat needs to draw the panel: page images, highlight boxes, PDF link."""
    c = by_id().get(chunk_id)
    if not c:
        return None
    doc = index()["docs"][c["doc"]]
    return {"id": c["id"], "document": doc["title"], "clause": c.get("clause"), "section": c["section"], "title": title(c),
            "pdf": f"{base_url}/docs/{doc['pdf']}?v={version()}#page={c['locations'][0]['page']}",
            "pages": [{"page": loc["page"], "image": f"{base_url}/docs/{c['doc']}-{loc['page']}.jpg?v={version()}", "rects": loc["rects"]}
                      for loc in c["locations"]]}
