"""A retake request is answered by the better photo that follows it, not by any other document."""
from tripclaim import checklist


def doc(i, kind="receipt", retake=False, merchant=None, total=None):
    ex = {"doc_type": kind, "receipt": {"merchant": merchant, "total": total, "datetime": "2026-10-16 22:00"}}
    return {"id": i, "extracted": ex, "flags": [{"code": "retake"}] if retake else []}


def test_unreadable_photo_is_answered_by_the_next_readable_one():
    s = checklist.compute([doc("dinner", merchant="Ramen", total=1880), doc("blur", retake=True), doc("hotel", merchant="Bayside", total=16500)])
    assert s["expense_receipts"]["pending"] == [] and s["expense_receipts"]["done"]


def test_a_partly_readable_photo_waits_for_the_same_shop_or_total():
    s = checklist.compute([doc("blur", retake=True, merchant="Bayside"), doc("taxi", merchant="Air Taxi", total=3200)])
    assert s["expense_receipts"]["pending"] == ["blur"]
    s = checklist.compute([doc("blur", retake=True, merchant="Bayside"), doc("hotel", merchant="Bayside", total=16500)])
    assert s["expense_receipts"]["pending"] == []


def test_a_photo_sent_before_the_retake_does_not_answer_it():
    s = checklist.compute([doc("hotel", merchant="Bayside", total=16500), doc("blur", retake=True)])
    assert s["expense_receipts"]["pending"] == ["blur"]


def test_a_boarding_pass_does_not_answer_a_receipt_retake():
    s = checklist.compute([doc("blur", retake=True), doc("bp", kind="boarding_pass")])
    assert s["expense_receipts"]["pending"] == ["blur"]
