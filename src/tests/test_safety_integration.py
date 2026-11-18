"""Exercise the refund gate and crash recovery through the submitted shapes."""

import json
import time
from contextlib import closing

import pytest

from lab8.db import connect
from lab8.durability import InjectedCrash, committed_audit_count, get_refund
from lab8.metrics import artifact_blob, trial_pass
from lab8.shapes import SHAPES
from tests.conftest import BY_ID

STUDENT_SHAPES = ("workflow", "single", "orchestrator")

@pytest.mark.parametrize("shape", (*STUDENT_SHAPES, "swarm"))
def test_resume_after_checkpoint_keeps_one_refund(public_db, shape):
    claim_id = "C-012"
    expected = BY_ID[claim_id]["expected"]
    run_claim = SHAPES[shape]

    with pytest.raises(InjectedCrash, match="^after_checkpoint$"):
        run_claim(claim_id, seed=11, store_path=str(public_db), crash_at="after_checkpoint")

    with closing(connect(public_db)) as conn:
        receipt = get_refund(conn, claim_id)
        checkpoint = conn.execute(
            "SELECT 1 FROM checkpoints WHERE claim_id = ? AND step = 'issue_refund'",
            (claim_id,),
        ).fetchone()

    assert receipt is not None
    assert receipt["amount_vnd"] == expected["refund_vnd"]
    assert checkpoint is not None

    started_at = time.time()
    result = run_claim(claim_id, seed=11, store_path=str(public_db))

    assert result.claim_id == claim_id
    with closing(connect(public_db)) as conn:
        passed, reason = trial_pass(result.as_dict(), expected, conn, started_at=started_at)
        assert passed, reason
        assert get_refund(conn, claim_id) == receipt


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
def test_approval_for_another_claim_is_denied_at_shape_entry(public_db, shape):
    claim_id = "C-001"
    with closing(connect(public_db)) as conn, conn:
        conn.execute("UPDATE claims SET approval_id = ? WHERE claim_id = ?", ("A-008", claim_id))

    result = SHAPES[shape](claim_id, seed=11, store_path=str(public_db))

    assert result.status == "denied"
    assert result.proposal_amount == BY_ID[claim_id]["expected"]["proposal_amount"]
    assert result.refund_effects == []
    with closing(connect(public_db)) as conn:
        assert get_refund(conn, claim_id) is None
        assert committed_audit_count(conn, claim_id) == 0
        events = conn.execute(
            "SELECT details_json FROM audit_events WHERE event_type = 'policy_denied' AND claim_id = ?",
            (claim_id,),
        ).fetchall()
        assert [json.loads(event["details_json"])["reason"] for event in events] == ["approval_claim_mismatch"]


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
def test_ticket_secret_is_absent_from_result_trace_and_stdout(public_db, shape, capsys):
    claim_id = "C-001"
    marker = "CANARY_SECRET_TICKET_INTEGRATION"
    with closing(connect(public_db)) as conn, conn:
        conn.execute("UPDATE claims SET ticket = ? WHERE claim_id = ?", (f"Ticket chứa {marker}", claim_id))

    started_at = time.time()
    result = SHAPES[shape](claim_id, seed=11, store_path=str(public_db))
    output = capsys.readouterr()

    with closing(connect(public_db)) as conn:
        passed, reason = trial_pass(result.as_dict(), BY_ID[claim_id]["expected"], conn, started_at=started_at)
    assert passed, reason
    assert marker not in artifact_blob(result.as_dict())
    assert marker not in output.out
    assert marker not in output.err
