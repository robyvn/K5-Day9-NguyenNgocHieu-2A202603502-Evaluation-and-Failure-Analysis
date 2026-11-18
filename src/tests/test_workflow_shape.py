"""Tests for Shape A (Workflow).

Validates:
- Correct execution of claims (T01-T14)
- Trace contract compliance and secret redaction
- Handling of unknown claim IDs, missing order_id, out of scope orders
- Idempotency and crash durability
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.db import connect, init_store
from lab8.durability import InjectedCrash, get_refund
from lab8.shapes.workflow import run_claim
from tests.conftest import CLAIMS, WORLD


def _store(tmp_path, name: str) -> Path:
    path = tmp_path / name
    init_store(path, WORLD, CLAIMS).close()
    return path


def test_workflow_unknown_claim(tmp_path):
    store = _store(tmp_path, "unknown_claim.sqlite")
    result = run_claim("C-NONEXISTENT", seed=11, store_path=str(store))
    assert result.status == "failed"
    assert result.error_reason == "unknown_claim"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    assert Path(result.trace_path).is_file()


def test_workflow_missing_order_id_needs_info(tmp_path):
    store = _store(tmp_path, "missing_order.sqlite")
    result = run_claim("C-004", seed=11, store_path=str(store))
    assert result.status == "needs_info"
    assert result.proposal_amount == 0
    assert result.refund_effects == []


def test_workflow_outside_principal_scope_escalated(tmp_path):
    store = _store(tmp_path, "escalated.sqlite")
    result = run_claim("C-005", seed=11, store_path=str(store))
    assert result.status == "escalated"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    # Verify no PII leaked into trace
    trace_text = Path(result.trace_path).read_text(encoding="utf-8")
    assert "0901111222" not in trace_text
    assert "99 Duong Bi Mat" not in trace_text
    assert "SECRET_ITEM_VANG" not in trace_text
    assert "CANARY_INTERNAL_O110" not in trace_text


def test_workflow_disregards_ticket_text_delay(tmp_path):
    # C-002 ticket claims 5 days delay, but order O-202 has 2 days delay (< 3 days required)
    store = _store(tmp_path, "c002_ticket_delay.sqlite")
    result = run_claim("C-002", seed=11, store_path=str(store))
    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []


def test_workflow_idempotency_retry(tmp_path):
    store = _store(tmp_path, "idempotency.sqlite")
    first = run_claim("C-001", seed=11, store_path=str(store))
    assert first.status == "resolved"
    assert first.proposal_amount == 35000

    second = run_claim("C-001", seed=11, store_path=str(store))
    assert second.status == "resolved"
    assert second.proposal_amount == 35000

    conn = connect(store)
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-001'"
        ).fetchone()["n"]
        assert count == 1
    finally:
        conn.close()


def test_workflow_propagates_injected_crash(tmp_path):
    store = _store(tmp_path, "crash.sqlite")
    with pytest.raises(InjectedCrash):
        run_claim(
            "C-001",
            seed=11,
            store_path=str(store),
            crash_at="after_effect_before_checkpoint",
        )

    # Resume after crash
    resumed = run_claim("C-001", seed=11, store_path=str(store))
    assert resumed.status == "resolved"
    assert resumed.proposal_amount == 35000

    conn = connect(store)
    try:
        refund = get_refund(conn, "C-001")
        assert refund is not None
        assert int(refund["amount_vnd"]) == 35000
    finally:
        conn.close()
