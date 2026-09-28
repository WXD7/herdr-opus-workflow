#!/usr/bin/env python3
"""Build a bounded metadata-only Astra review packet; never invokes a model."""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("summary", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    summary = json.loads(args.summary.read_text())
    policy = json.loads(Path(__file__).with_name("observer-policy.json").read_text())
    claude = summary.get("claude") or {}
    packet = {
        "packet_version": 1,
        "run_id": summary["run_id"],
        "telemetry_run_id": summary.get("telemetry_run_id"),
        "telemetry_run_id_mapping": summary.get("telemetry_run_id_mapping"),
        # Summaries written before executor_kind existed only collected Codex lanes.
        "executor_kind": summary.get("executor_kind", "codex"),
        "observer_model": policy["observer"]["model"],
        "observer_role": "workflow_observer",
        "scope": summary["coverage"],
        "evidence_summary": str(args.summary.resolve()),
        "claude": {k: claude.get(k) for k in ["usage", "counts", "source", "coverage"]},
        "claude_threads": [{k: t.get(k) for k in ["session", "lane", "checkout", "parent", "status", "usage", "counts", "coverage", "observed", "source"]} for t in summary.get("claude_threads", [])],
        "codex_threads": [{k: t.get(k) for k in ["thread_id", "parent_thread_id", "usage", "counts", "coverage", "source"]} for t in summary["codex_threads"]],
        "spawn_edges": summary["spawn_edges"],
        "operations": summary["operations"],
        "unknowns": summary["unknowns"],
        "quality_note": "Recorded test evidence, not tests rerun by this collector. This packet alone does not establish general quality or optimization benefit.",
        "review_contract": str(Path(__file__).with_name("astra-review-contract.md").resolve()),
        "max_candidates": 3,
        "prohibited_inference": "Do not infer exact business/waste token percentages from tool counts. Do not convert subscription usage to an actual bill.",
    }
    text = json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
    if len(text) > policy["observer"]["max_packet_chars"]:
        p.error("Packet exceeds configured character limit; explicitly select fewer threads or evidence fields. Nothing was truncated silently.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(json.dumps({"packet_chars": len(text), "limit_chars": policy["observer"]["max_packet_chars"], "llm_calls": 0}))


if __name__ == "__main__":
    main()
