"""Tests for the ReAct loop limits in Shape B (single agent).

test_single_shape.py already covers deny, trace secret and crash/resume.
This file checks behaviour that only the ReAct shape has:
1. The loop calls read tools only, in a known order, and stays under 8 steps.
2. An agent that never finishes stops at the step cap without writing a refund.
3. The amount comes from compute_compensation, so it is the same for every seed.
4. The host gets the full quote and applies the per-order cap itself (T13, T14).
5. An order outside the principal's scope escalates without reading refund
   history, and no order PII reaches the trace.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

import lab8.runtime as runtime
import lab8.shapes.single as single
from lab8.db import connect, init_store
from lab8.durability import committed_audit_count, get_refund
from lab8.handoff import READONLY_TOOLS
from tests.conftest import BY_ID, CLAIMS, WORLD

SEEDS = (11, 22, 33, 44, 55)


@pytest.fixture
def tool_log(monkeypatch):
    """Record every tool name the ReAct loop sends to dispatch."""
    calls: list[str] = []
    real_dispatch = runtime.dispatch

    def spy(conn, budget, name, args, *, worker):
        calls.append(name)
        return real_dispatch(conn, budget, name, args, worker=worker)

    monkeypatch.setattr(runtime, "dispatch", spy)
    return calls


def test_single_react_uses_only_read_tools_in_order(public_db, tool_log):
    result = single.run_claim("C-001", seed=11, store_path=str(public_db))

    assert result.status == "resolved"
    assert tool_log == ["lookup_order", "lookup_refund_history", "compute_compensation"]
    assert all(name in READONLY_TOOLS for name in tool_log)
    assert len(tool_log) < 8


def test_single_step_cap_stops_without_refund(public_db, monkeypatch, tool_log):
    real_run_react = single.run_react

    def never_final(seen):
        return {"tool": "lookup_policy", "args": {}}

    def capped_run_react(conn, budget, *, observation, decide, max_steps, worker):
        assert max_steps == 8
        return real_run_react(
            conn, budget, observation=observation, decide=never_final,
            max_steps=max_steps, worker=worker,
        )

    monkeypatch.setattr(single, "run_react", capped_run_react)
    result = single.run_claim("C-001", seed=11, store_path=str(public_db))

    assert result.status == "failed"
    assert result.error_reason == "step_cap"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    assert len(tool_log) == 8
    with closing(connect(public_db)) as conn:
        assert get_refund(conn, "C-001") is None
        assert committed_audit_count(conn, "C-001") == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_single_amount_is_seed_invariant(tmp_path: Path, seed: int):
    store = tmp_path / f"single-seed-{seed}.sqlite"
    init_store(store, WORLD, CLAIMS).close()

    result = single.run_claim("C-003", seed=seed, store_path=str(store))
    expected = BY_ID["C-003"]["expected"]

    assert result.status == expected["status"]
    assert result.proposal_amount == expected["proposal_amount"]
    with closing(connect(store)) as conn:
        assert int(get_refund(conn, "C-003")["amount_vnd"]) == expected["refund_vnd"]


def test_single_passes_full_quote_and_host_applies_order_cap(public_db, monkeypatch):
    sent: list[int] = []
    real_issue_refund = single.issue_refund

    def spy(conn, claim_id, proposed_vnd, *, crash_at=None):
        sent.append(proposed_vnd)
        return real_issue_refund(conn, claim_id, proposed_vnd, crash_at=crash_at)

    monkeypatch.setattr(single, "issue_refund", spy)
    result = single.run_claim("C-014", seed=11, store_path=str(public_db))
    expected = BY_ID["C-014"]["expected"]

    assert sent == [expected["proposal_amount"]]
    assert result.status == "resolved"
    assert result.proposal_amount == expected["proposal_amount"]
    with closing(connect(public_db)) as conn:
        assert int(get_refund(conn, "C-014")["amount_vnd"]) == expected["refund_vnd"]
        assert committed_audit_count(conn, "C-014") == 1


def test_single_out_of_scope_escalates_without_pii(public_db, tool_log):
    result = single.run_claim("C-005", seed=11, store_path=str(public_db))
    expected = BY_ID["C-005"]["expected"]

    assert result.status == "escalated"
    assert result.refund_effects == []
    assert tool_log == ["lookup_order"]
    body = Path(result.trace_path).read_text(encoding="utf-8")
    for forbidden in expected["forbid_substrings"]:
        assert forbidden not in body
    with closing(connect(public_db)) as conn:
        assert get_refund(conn, "C-005") is None
