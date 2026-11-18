"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)
    Also: duplicated (item, seed) rows, a manifest that disagrees with the baseline's, a row value
    that is not a bool, metrics that do not match what the rows aggregate to (hand-edited
    numbers), and on the same eval-set version a changed seed list / eval set / bootstrap."""
    problems: list[str] = []
    problems += _report_problems("PR", pr, spec)
    problems += _report_problems("baseline", base, spec)

    # Same trials as the baseline: no dropped, duplicated, swapped or cherry-picked (item, seed).
    pm, bm = pr.get("manifest") or {}, base.get("manifest") or {}
    if pm.get("items") != bm.get("items") or list(pm.get("seeds") or []) != list(bm.get("seeds") or []):
        problems.append(f"manifest items/seeds {pm.get('items')}/{pm.get('seeds')} differ from the "
                        f"baseline's {bm.get('items')}/{bm.get('seeds')}")
    pr_keys, base_keys = _trial_keys(pr), _trial_keys(base)
    if set(pr_keys) != set(base_keys):
        missing, extra = set(base_keys) - set(pr_keys), set(pr_keys) - set(base_keys)
        problems.append(f"(item, seed) set differs from the baseline: {len(missing)} missing "
                        f"{_few(missing)}, {len(extra)} extra {_few(extra)}")

    # Slice tags come from the eval set; a PR may not move an item out of a slice.
    pr_tags, base_tags = _slice_tags(pr), _slice_tags(base)
    for item in sorted(set(pr_tags) & set(base_tags), key=str):
        if pr_tags[item] != base_tags[item]:
            problems.append(f"{item}: slice tags {sorted(pr_tags[item])} differ from the baseline's "
                            f"{sorted(base_tags[item])}")
    for item, tags in sorted(_inconsistent_tags(pr).items(), key=str):
        problems.append(f"{item}: slice tags differ between seeds of the same item {tags}")

    if base_spec is not None:
        problems += _ratchet(spec, base_spec)
    return problems


def _graded(spec: dict) -> list[str]:
    from lab9.graders import GRADERS
    return [m["id"] for m in spec.get("metrics") or [] if m["id"] in GRADERS]


def _trial_keys(rep: dict) -> list[tuple]:
    return [(r.get("item_id"), r.get("seed")) for r in rep.get("rows") or []]


def _few(keys: set) -> str:
    shown = sorted(keys, key=str)[:4]
    return "[" + ", ".join(f"{i}@{s}" for i, s in shown) + (" …]" if len(keys) > 4 else "]")


def _slice_tags(rep: dict) -> dict:
    return {r.get("item_id"): frozenset(r.get("slices") or []) for r in rep.get("rows") or []}


def _inconsistent_tags(rep: dict) -> dict:
    seen: dict = {}
    for r in rep.get("rows") or []:
        seen.setdefault(r.get("item_id"), set()).add(frozenset(r.get("slices") or []))
    return {i: [sorted(t) for t in s] for i, s in seen.items() if len(s) > 1}


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _report_problems(who: str, rep: dict, spec: dict) -> list[str]:
    """Checks that need only one report: is it internally consistent and complete?"""
    import math
    from lab9.eval import aggregate

    out: list[str] = []
    rows, manifest = rep.get("rows") or [], rep.get("manifest") or {}
    for k, v in (rep.get("metrics") or {}).items():
        if _is_number(v) and not math.isfinite(v):
            out.append(f"{who} metric {k} is {v} (a crashed or dropped grader is not a score)")
        elif v is not None and not _is_number(v):
            out.append(f"{who} metric {k} is {v!r}, not a number")
    seeds = list(manifest.get("seeds") or [])
    want = (manifest.get("items") or 0) * len(seeds)
    if len(rows) != want:
        out.append(f"{who} has {len(rows)} rows, manifest promises {manifest.get('items')} items x "
                   f"{len(seeds)} seeds = {want}")
    keys = _trial_keys(rep)
    if len(set(keys)) != len(keys):
        dup = {k for k in keys if keys.count(k) > 1}
        out.append(f"{who} has duplicated (item, seed) rows {_few(dup)}")
    stray = {s for _, s in keys} - set(seeds)
    if stray:
        out.append(f"{who} has rows for seeds {sorted(stray, key=str)} not in its manifest {seeds}")
    if rep.get("evaluator_failures"):
        out.append(f"{who} has {len(rep['evaluator_failures'])} evaluator failure(s), first: "
                   f"{rep['evaluator_failures'][0]}")
    graded = _graded(spec)
    for m in graded:
        bad = [r for r in rows if not isinstance(r.get(m), bool)]
        if bad:
            out.append(f"{who} metric {m}: {len(bad)} row(s) without a True/False grade "
                       f"(first {bad[0].get('item_id')}@{bad[0].get('seed')} = {bad[0].get(m)!r})")
    # The headline numbers must be what the rows say: re-aggregate and compare.
    if rows and not out:
        has_cost = all("cost" in r for r in rows)
        again = aggregate([dict(r, cost=float(r.get("cost") or 0.0)) for r in rows], graded,
                          k_values=spec.get("pass_k", []))
        for k, v in (rep.get("metrics") or {}).items():
            if k not in again or (k == "cost_per_success" and not has_cost):
                continue
            w = again[k]
            if (v is None) != (w is None) or (v is not None and abs(float(v) - float(w)) > 1e-6):
                out.append(f"{who} metric {k} = {v} but its rows aggregate to {w} (hand-edited report?)")
    return out


def _looser(old: dict, new: dict, where: str) -> list[str]:
    """Threshold fields of one metric or slice: the new value may only be as strict or stricter."""
    out = []
    for f, worse in (("max", lambda o, n: n > o), ("min", lambda o, n: n < o),
                     ("max_increase", lambda o, n: n > o)):
        if f in old and (not _is_number(new.get(f)) or worse(old[f], new[f])):
            out.append(f"{where}: {f} {old[f]} -> {new.get(f)}")
    if old.get("no_significant_drop") and not new.get("no_significant_drop"):
        out.append(f"{where}: no_significant_drop dropped")
    return out


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []                     # a new eval-set version starts a new ratchet
    out: list[str] = []
    new_metrics = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        n = new_metrics.get(m["id"])
        if n is None:
            out.append(f"ratchet: metric {m['id']} removed")
            continue
        out += [f"ratchet: {p}" for p in _looser(m.get("threshold") or {}, n.get("threshold") or {}, m["id"])]
        if m.get("blocking") and not n.get("blocking"):
            out.append(f"ratchet: {m['id']} no longer blocking")
    key = lambda s: (s.get("name"), s.get("metric"))
    new_slices = {key(s): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        where = f"slice:{s.get('name')}/{s.get('metric')}"
        n = new_slices.get(key(s))
        if n is None:
            out.append(f"ratchet: {where} removed")
            continue
        out += [f"ratchet: {p}" for p in _looser(s, n, where)]
        if s.get("blocking") and not n.get("blocking"):
            out.append(f"ratchet: {where} no longer blocking")
    for k in ("eval_set", "labels", "seeds", "bootstrap", "adversarial_pack"):
        if base_spec.get(k) != spec.get(k):
            out.append(f"ratchet: {k} changed on the same eval-set version ({base_spec.get(k)} -> {spec.get(k)})")
    if set(base_spec.get("pass_k") or []) - set(spec.get("pass_k") or []):
        out.append(f"ratchet: pass_k {base_spec.get('pass_k')} -> {spec.get('pass_k')}")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices") or []:
        name, metric = s["name"], s["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", f"{len(a)} item(s)", None
        if not a:
            verdict = "missing"          # an empty slice proves nothing: never a silent pass
        else:
            val = round(sum(a.values()) / len(a), 4)
            if ("min" in s and val < s["min"]) or ("max" in s and val > s["max"]):
                verdict = "fail"
            keys = sorted(set(a) & set(b))
            if s.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                                   confidence=bs.get("confidence", 0.95))
                note = f"{len(a)} item(s) Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and s.get("blocking"):
            blocked.append(sid)
        th = {k: s[k] for k in ("min", "max", "no_significant_drop") if k in s}
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if s.get("blocking") else "warn")
        lines.append(f"  {sid:<34} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
