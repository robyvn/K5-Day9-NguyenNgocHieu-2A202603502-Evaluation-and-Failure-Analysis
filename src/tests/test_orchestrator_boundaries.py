"""Boundary tests for Shape C (orchestrator), complementing test_orchestrator_shape.py.

test_orchestrator_shape.py already covers: handoff rejects issue_refund, no CANARY in one
trace, and crash/resume at the three durability points. This file adds:
- The investigator tool list is read-only and the traced handoff is allowlisted with empty facts.
- No secret, PII or raw ticket text in the trace, on every public claim.
- The orchestrator passes the FULL quote and the host applies the per-order cap (T13, T14).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.db import connect, init_store
from lab8.handoff import ALLOWED_FIELDS, READONLY_TOOLS
from lab8.shapes.orchestrator import INVESTIGATOR_TOOLS, run_claim
from tests.conftest import BY_ID, CLAIMS, WORLD

SENSITIVE_KEY_PARTS = ("secret", "phone", "address", "note", "item", "header")


def _store(tmp_path, name: str) -> Path:
    path = tmp_path / name
    init_store(path, WORLD, CLAIMS).close()
    return path


def _forbidden_strings() -> set[str]:
    """Every string value in the public fixtures stored under a sensitive key."""
    found: set[str] = {"CANARY_SECRET_"}

    def walk(node, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and node and any(p in key.lower() for p in SENSITIVE_KEY_PARTS):
            found.add(node)

    walk(WORLD)
    walk(CLAIMS)
    return found


def test_forbidden_list_is_not_empty():
    # Guard: if the fixture keys change, the leak scan below must not silently scan nothing.
    assert len(_forbidden_strings()) > 10


def test_investigator_tool_list_is_read_only():
    assert set(INVESTIGATOR_TOOLS) <= READONLY_TOOLS


def test_trace_records_sanitized_handoff_only(tmp_path):
    store = _store(tmp_path, "handoff.sqlite")
    result = run_claim("C-001", seed=11, store_path=str(store))
    assert result.status == "resolved"

    body = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert body["run_id"] == result.run_id
    handoffs = [e["payload"] for e in body["events"] if e["kind"] == "handoff"]
    assert len(handoffs) == 1
    handoff = handoffs[0]
    assert set(handoff) <= ALLOWED_FIELDS
    assert handoff["facts"] == {}
    assert set(handoff["allowed_tools"]) <= READONLY_TOOLS


@pytest.mark.parametrize("claim_id", sorted(BY_ID))
def test_trace_has_no_secret_pii_or_ticket(tmp_path, claim_id):
    store = _store(tmp_path, f"leak-{claim_id}.sqlite")
    result = run_claim(claim_id, seed=11, store_path=str(store))
    text = Path(result.trace_path).read_text(encoding="utf-8")
    leaked = sorted(s for s in _forbidden_strings() if s in text)
    assert leaked == [], f"{claim_id} trace leaked {leaked}"
    ticket = BY_ID[claim_id].get("ticket")
    if ticket:
        assert ticket not in text, "ticket text is user data and must not be traced"


def test_full_quote_when_order_already_fully_refunded(tmp_path):
    # T13: O-505 already got 35,000 for shipping from an earlier claim.
    store = _store(tmp_path, "t13.sqlite")
    result = run_claim("C-013", seed=11, store_path=str(store))
    assert result.status == "denied"
    assert result.proposal_amount == 35000
    assert result.refund_effects == []
    conn = connect(store)
    try:
        denied = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE claim_id = 'C-013' AND event_type = 'policy_denied'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert denied >= 1


def test_full_quote_host_pays_only_difference(tmp_path):
    # T14: O-515 got 30,000 for shipping, now damage verified (entitlement 150,000).
    store = _store(tmp_path, "t14.sqlite")
    result = run_claim("C-014", seed=11, store_path=str(store))
    assert result.status == "resolved"
    assert result.proposal_amount == 150000
    assert [int(e["amount_vnd"]) for e in result.refund_effects] == [120000]
