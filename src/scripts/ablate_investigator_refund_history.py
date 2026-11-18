"""Measure the effect of one optional investigator read using the public harness."""

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from lab8.benchmark import run_benchmark
from lab8.config_loader import repo_path
from lab8.db import load_json, load_jsonl
from lab8.shapes import orchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    world = load_json(repo_path("data", "public_world_state.json"))
    claims = load_jsonl(repo_path("data", "public_claims.jsonl"))
    seeds = [11, 22, 33, 44, 55]
    shape = "orchestrator"
    tool = "lookup_refund_history"
    tools_with_history = orchestrator.INVESTIGATOR_TOOLS
    tools_without_history = [name for name in tools_with_history if name != tool]
    if tool not in tools_with_history:
        raise SystemExit(f"The baseline no longer includes {tool}.")

    report = {"intervention": tool, "arms": {}}
    for arm, tools in (("with_history", tools_with_history), ("without_history", tools_without_history)):
        with patch.object(orchestrator, "INVESTIGATOR_TOOLS", tools):
            result = run_benchmark(world=world, claims=claims, shapes=[shape], seeds=seeds)
        report["controlled_variables"] = result["controlled_variables"]
        report["arms"][arm] = {
            "investigator_tools": tools,
            "summary": result["by_shape"][shape],
            "example_trial": result["trials"][0],
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
