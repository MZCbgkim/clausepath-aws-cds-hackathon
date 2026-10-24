"""The two evaluated LLM skills: document extraction and coverage assessment (eval/ measures both)."""
import json
from functools import lru_cache
from importlib import resources

from . import policy

PROMPTS = resources.files("tripclaim.prompts")


@lru_cache
def _read(name):
    return PROMPTS.joinpath(name).read_text()


def extract_document(llm, *, image_bytes=None, image_format="jpeg", text=None, pdf_bytes=None):
    """One document (photo, PDF or email text) -> structured fields. Never judges the claim."""
    if pdf_bytes is not None:
        content = [{"type": "document", "bytes": pdf_bytes, "format": "pdf"},
                   {"type": "text", "text": "Extract this document."}]
    elif image_bytes is not None:
        content = [{"type": "image", "bytes": image_bytes, "format": image_format},
                   {"type": "text", "text": "Extract this document."}]
    else:
        content = [{"type": "text", "text": "Forwarded email:\n\n" + (text or "")}]
    return llm.structured(_read("extract.md"), content, json.loads(_read("extract.schema.json")), name="document")


def assess_coverage(llm, case):
    """case: today, traveler, trip, event, documents_received, message -> status, cited clauses, checklist, reply."""
    system = _read("coverage.md") + policy.policy_text()
    out = llm.structured(system, [{"type": "text", "text": json.dumps(case)}],
                         json.loads(_read("coverage.schema.json")), name="coverage")
    out["cited_clauses"] = policy.normalize_ids(out.get("cited_clauses", []))
    return out
