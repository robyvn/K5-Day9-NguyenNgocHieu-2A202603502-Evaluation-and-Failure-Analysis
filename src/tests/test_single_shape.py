"""Student-written tests for the single-agent shape (Shape B).

Covers:
1. Honest quoting: C-002 ticket claims a 5-day delay but the order row has 2 days,
   so the shape must deny (not trust the ticket).
2. The trace contains no parent secret / header material.
3. Crash at all three durability points, then resume, still yields one refund.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab8.db import connect, init_store
from lab8.durability import InjectedCrash, committed_audit_count, get_refund
from lab8.shapes.single import run_claim
from tests.conftest import CLAIMS, WORLD

CRASH_POINTS = ("before_effect", "after_effect_before_checkpoint", "after_checkpoint")


def _fresh_store(tmp_path: Path, name: str) -> str:
    store = tmp_path / name
    init_store(store, WORLD, CLAIMS).close()
    return str(store)


def test_single_denies_when_order_row_below_threshold(tmp_path: Path):
    store = _fresh_store(tmp_path, "single-c002.sqlite")
    result = run_claim("C-002", seed=11, store_path=store)
    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []


def test_single_trace_has_no_parent_secret(tmp_path: Path):
    store = _fresh_store(tmp_path, "single-trace.sqlite")
    result = run_claim("C-001", seed=7, store_path=store)
    body = Path(result.trace_path).read_text(encoding="utf-8")
    assert "CANARY_SECRET_" not in body
    assert "parent_secret" not in body
    assert "raw_headers" not in body


@pytest.mark.parametrize("point", CRASH_POINTS)
def test_single_crash_then_resume_yields_single_refund(tmp_path: Path, point: str):
    store = _fresh_store(tmp_path, f"single-{point}.sqlite")

    with pytest.raises(InjectedCrash):
        run_claim("C-001", seed=11, store_path=store, crash_at=point)

    result = run_claim("C-001", seed=11, store_path=store, crash_at=None)
    assert result.status == "resolved"

    conn = connect(store)
    try:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-001'"
        ).fetchone()["n"]
        effect = get_refund(conn, "C-001")
        audits = committed_audit_count(conn, "C-001")
    finally:
        conn.close()
    assert rows == 1
    assert effect is not None
    assert int(effect["amount_vnd"]) == result.proposal_amount
    assert audits == 1
