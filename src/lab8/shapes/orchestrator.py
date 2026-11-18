"""Shape C — orchestrator plus read-only investigators. Student work.

1. Build a handoff with `lab8.handoff.make_handoff`. Allowed tools are read-only.
   Never put parent_secret, raw headers, or the full claim row into facts.
2. `lab8.runtime.run_readonly_investigators` runs those tools.
3. The orchestrator quotes from the investigator's order view and calls
   `durability.issue_refund`. Investigators have no write registry.
   Pass the full quote; the host subtracts earlier refunds on the same order.
   `lookup_refund_history` is a read-only tool you may hand to an investigator.
4. Trace the sanitized handoff, not the parent context.

An LLM summary of the parent context is not a sanitizer. Use the allowlist.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "orchestrator"
# Investigators only get read tools. make_handoff rejects anything else.
INVESTIGATOR_TOOLS = [
    "lookup_order",
    "lookup_policy",
    "lookup_refund_history",
    "compute_compensation",
]


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal: int, reason: str | None) -> RunResult:
        trace.add("decision", {"status": status, "proposal_amount": int(proposal), "reason": reason})
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    # 1. Orchestrator reads the claim through the view. The ticket is user data:
    #    it is never traced and never used to pick the order or the delay.
    budget.add_tool(tool_cost())
    claim = get_claim_view(conn, claim_id)
    if not claim["found"]:
        return done("failed", 0, "unknown_claim")
    trace.add("claim", {"claim_id": claim_id, "has_order_id": bool(claim["order_id"])})
    if not claim["order_id"]:
        return done("needs_info", 0, "missing_order_id")

    # 2. Handoff: empty facts (investigators read the order row themselves),
    #    read-only tools, sanitized by the allowlist in lab8.handoff.
    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="investigate_refund_eligibility",
        facts={},
        allowed_tools=INVESTIGATOR_TOOLS,
    )
    trace.add("handoff", safe_dump(handoff))

    # 3. Investigators run read tools only.
    investigated = run_readonly_investigators(conn, budget, handoff, principal_id=claim["principal_id"])
    tools = investigated["tools"]
    order_view = tools.get("lookup_order") or {}
    trace.add(
        "investigator",
        {
            "lookup_order": order_view,  # allowlisted ORDER_FIELDS only
            "refund_history": tools.get("lookup_refund_history"),
            "policy_count": len((tools.get("lookup_policy") or {}).get("policies") or []),
        },
    )
    if not order_view.get("visible"):
        return done("escalated", 0, order_view.get("reason"))

    # 4. Money comes from code. Do NOT subtract refund history: the host does that.
    quote = tools.get("compute_compensation") or {}
    proposal = int(quote.get("amount_vnd", 0))
    trace.add("quote", safe_dump(quote))
    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")  # model only drafts text

    if proposal <= 0:
        return done("denied", 0, quote.get("reason"))

    # 5. Only the orchestrator calls the host write gate, with the full quote.
    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise

    if outcome["ok"]:
        return done("resolved", proposal, None)
    return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])
