"""Document checklist from [CL-1], computed from what has been received (never stored by hand)."""

ITEMS = [
    ("airline_letter", "Airline confirmation of the cancellation or delay with flight, date and reason", "now"),
    ("original_eticket", "Original ticket showing the scheduled departure", "anytime"),
    ("replacement_boarding_pass", "Boarding pass for the replacement flight", "now"),
    ("expense_receipts", "Itemised receipts for meals, hotel, transport and calls", "now"),
    ("claim_form", "Signed claim form (secure claims page)", "after_return"),
    ("photo_id", "Copy of photo ID (secure claims page)", "after_return"),
]
LABELS = {k: label for k, label, _ in ITEMS}
# A document with one of these cannot stand for the item (someone else's letter, another flight): the item needs a check.
CHECK_FLAGS = {"name_mismatch", "flight_mismatch", "other_claim_document"}
REQUIRED = [k for k, _, _ in ITEMS]


def compute(documents, secure_submitted=False):
    """documents: stored docs with `extracted` and `flags`.
    Returns {item: {"done", "docs" (that satisfy it), "pending" (photos waiting for a retake), "check" (docs that need a check)}}.
    An item is done only by a document without a check flag; flagged ones are listed so nobody mistakes them for done."""
    state = {k: {"done": False, "docs": [], "pending": [], "check": [], "why": [], "when": when} for k, _, when in ITEMS}
    kind_item = {"airline_letter": "airline_letter", "eticket": "original_eticket", "boarding_pass": "replacement_boarding_pass"}

    def add(item, doc):
        why = {f["code"] for f in doc.get("flags", []) if f["code"] in CHECK_FLAGS}
        if why:
            state[item]["check"].append(doc["id"])
            state[item]["why"] = sorted(set(state[item]["why"]) | why)  # a list: the view is sent as JSON
            return
        state[item]["done"] = True
        state[item]["docs"].append(doc["id"])

    retakes = {}  # item -> [(doc id, merchant, total)] still waiting for a better photo

    def retaken(item, ex):
        """A readable photo of the same item, sent after a retake request, answers it: the same shop or total when the
        unreadable photo showed either, otherwise the next readable one (documents arrive in order)."""
        r = ex.get("receipt") or {}
        for i, (_, merchant, total) in enumerate(retakes.get(item, [])):
            if (not merchant and total is None) or (merchant and merchant == (r.get("merchant") or "").strip()) \
                    or (total is not None and total == r.get("total")):
                return retakes[item].pop(i)
        return None

    for d in documents:
        ex = d.get("extracted") or {}
        kind = ex.get("doc_type")
        retake = any(f["code"] == "retake" for f in d.get("flags", []))
        if retake:  # waiting for a better photo: shown against its item so "done" never hides it
            r = ex.get("receipt") or {}
            retakes.setdefault(kind_item.get(kind, "expense_receipts"), []).append(
                (d["id"], (r.get("merchant") or "").strip(), r.get("total")))
            continue
        retaken(kind_item.get(kind, "expense_receipts") if kind in kind_item or kind == "receipt" else None, ex)
        if kind == "airline_letter":
            add("airline_letter", d)
            letter = ex.get("letter") or {}
            if letter.get("booking_ref") and letter.get("scheduled_departure"):
                add("original_eticket", d)  # an airline letter that states booking ref + scheduled time serves as the ticket
        elif kind == "eticket":
            add("original_eticket", d)
        elif kind == "boarding_pass":
            add("replacement_boarding_pass", d)
        elif kind == "receipt":
            add("expense_receipts", d)
    for item, left in retakes.items():
        state[item]["pending"] = [doc_id for doc_id, _, _ in left]
    if secure_submitted:
        for item in ("claim_form", "photo_id"):
            state[item]["done"] = True
    return state


def missing(state):
    return [k for k in REQUIRED if not state[k]["done"]]


def describe_missing(state, locale="en"):
    from .i18n import label
    return [label(locale, k) for k in missing(state)]
