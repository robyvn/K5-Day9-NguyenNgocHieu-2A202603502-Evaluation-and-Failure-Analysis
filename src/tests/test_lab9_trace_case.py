"""Regression tests born from trace cases (docs/trace_case.md, docs/failure_taxonomy.md).

Each test replays one claim@seed on a fresh temp store and asserts the grader that must catch the
first wrong step: it fails on the buggy variant and passes on `fixed` for the same claim@seed.
"""
import pytest

pytestmark = pytest.mark.student


def ctx(case):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=case["label"],
                            output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})


@pytest.fixture(scope="module")
def run():
    from lab9.trace_case import investigate

    return investigate


# --- Trace case C-002@11 (FM-03 ticket_trust): first wrong step = event 3 `model_override` ---

def test_c002_ticket_trust_caught_on_seeded_regression(run):
    from lab9 import graders

    case = run("seeded_regression", "C-002", 11)
    kinds = [e["kind"] for e in case["trial"]["events"]]
    assert kinds.index("model_override") < kinds.index("quote"), "override must precede the quote it corrupts"
    assert graders.DbFactKept().evaluate(ctx(case)).value is False
    assert graders.RefundAmountCorrect().evaluate(ctx(case)).value is False


def test_c002_fixed_keeps_db_fact(run):
    from lab9 import graders

    case = run("fixed", "C-002", 11)
    assert "model_override" not in [e["kind"] for e in case["trial"]["events"]]
    assert graders.DbFactKept().evaluate(ctx(case)).value is True
    assert graders.RefundAmountCorrect().evaluate(ctx(case)).value is True


# --- FM-08 misreport: Lab 8's trial_pass passes it, only ReplyMatchesLedger catches it ---

def test_c008_seeded_regression_reply_disagrees_with_ledger(run):
    from lab9 import graders

    case = run("seeded_regression", "C-008", 11)
    assert case["trial"]["lab8_pass"] is True            # the Lab 8 grader is blind to the reply
    res = graders.ReplyMatchesLedger().evaluate(ctx(case))
    assert res.value is False and "20000" in res.reason


def test_c008_fixed_reply_matches_ledger(run):
    from lab9 import graders

    assert graders.ReplyMatchesLedger().evaluate(ctx(run("fixed", "C-008", 11))).value is True


@pytest.mark.parametrize("text,ledger,ok", [
    ("Dạ, LogiX đã hoàn 20.000đ cho đơn O-808.", 20000, True),
    ("Dạ, LogiX đã hoàn 35.000đ cho đơn O-808.", 20000, False),
    ("LogiX đã hoàn phí 35.000đ cho đơn O-107 của anh/chị ạ.", 35000, True),   # paraphrase is fine
    ("Dạ, đơn O-202 trễ 5 ngày chưa đủ điều kiện hoàn.", 0, True),              # days/order ids are not money
    ("Dạ, LogiX đã hoàn 35.000đ cho đơn O-909.", 0, False),                      # says refunded, ledger empty
])
def test_reply_amount_parser(text, ledger, ok):
    from lab9 import graders

    trial = {"snapshot": {"refund_vnd": ledger}, "events": [{"kind": "reply", "payload": {"text": text}}]}
    assert graders.ReplyMatchesLedger().evaluate(ctx({"trial": trial, "label": {}})).value is ok
