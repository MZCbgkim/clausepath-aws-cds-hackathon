"""Policy wording loaded as clauses with citable IDs ([TD-1], [CL-4], ...)."""
import re
from functools import lru_cache
from importlib import resources


@lru_cache
def policy_text(lang="en"):
    name = "sample_policy_ko.md" if lang == "ko" else "sample_policy.md"
    return resources.files("tripclaim.prompts").joinpath(name).read_text()


@lru_cache
def clauses(lang="en"):
    """{clause_id: text}. A clause runs from its [ID] marker to the next marker or heading."""
    out, current = {}, None
    for line in policy_text(lang).splitlines():
        m = re.match(r"\[([A-Z]+-\d+)\]\s*(.*)", line.strip())
        if m:
            current = m.group(1)
            out[current] = m.group(2)
        elif line.startswith("#"):
            current = None
        elif current and line.strip():
            out[current] += "\n" + line.strip()
    return out


def normalize_ids(ids):
    """Model output sometimes keeps brackets or adds sub-letters: "[TD-4](a)" -> "TD-4"."""
    found = []
    for raw in ids:
        m = re.search(r"([A-Z]+-\d+)", raw or "")
        if m and m.group(1) in clauses() and m.group(1) not in found:
            found.append(m.group(1))
    return found


SYNONYMS = {"paid": "pay", "payment": "pay", "money": "pay", "refund": "pay", "reimburse": "pay",
            "hotel": "accommodation", "room": "accommodation", "food": "meals", "dinner": "meals",
            "lunch": "meals", "breakfast": "meals", "taxi": "transport", "train": "transport",
            "bus": "transport", "sim": "data", "documents": "documents", "letter": "confirmation",
            "typhoon": "weather", "storm": "weather", "late": "delay", "delayed": "delay"}
STOP = {"the", "and", "can", "for", "will", "get", "what", "when", "how", "are", "you", "does", "with", "this", "that", "have"}


def search(query, k=4):
    """Keyword search over clauses, for the agent's search_policy tool."""
    words = {SYNONYMS.get(w, w) for w in re.findall(r"[a-z]{3,}", query.lower())} - STOP
    scored = []
    for cid, text in clauses().items():
        body = text.lower()
        score = sum(body.count(w) for w in words) + (5 if cid.lower() in query.lower() else 0)
        if score:
            scored.append((score, cid))
    return [{"id": cid, "text": clauses()[cid]} for _, cid in sorted(scored, reverse=True)[:k]]
