#!/usr/bin/env python3
"""Deterministic shadow gate. No network, LLM invocation, or workflow steering.

Replay normalized event JSONL to see which events merit a bounded review.
seen_candidates counts queue reservations in this replay, not actual model calls.
Live integration must durably reserve/acknowledge jobs and add usage accounting.
"""
import argparse
import hashlib
import json
from pathlib import Path


def decide(event, state, policy):
    """Return a decision; update only the caller-owned shadow replay state."""
    reason = None
    run = event.get("run_id")
    kind = event.get("kind")
    if not run or not isinstance(event.get("timestamp_s"), (int, float)):
        reason = "invalid_event"
    elif event.get("role") == "workflow_observer" or event.get("origin") == "observer":
        reason = "observer_feedback_excluded"
    elif kind not in policy["triggers"]:
        reason = "non_actionable_event"
    elif not event.get("evidence_refs") or not event.get("evidence_digest"):
        reason = "missing_evidence"
    elif (event.get("terminal") or state.get(run, {}).get("terminal_seen")) and kind != "run_completed":
        reason = "terminal_run"
    elif event.get("pending_action"):
        reason = "action_already_pending"
    elif kind == "run_completed" and not event.get("terminal"):
        reason = "not_terminal"
    elif kind == "repeated_failure" and event.get("same_failure_count", 0) < policy["minimum_repeated_failures"]:
        reason = "failure_not_repeated"
    elif kind == "no_progress" and (
        not isinstance(event.get("healthy_tool_running"), bool)
        or not isinstance(event.get("waiting_for_user"), bool)
        or not isinstance(event.get("observation_age_seconds"), (int, float))
        or not 0 <= event["observation_age_seconds"] <= policy["maximum_observation_age_seconds"]
    ):
        reason = "missing_or_stale_observation"
    elif kind == "no_progress" and (
        not event.get("expected_progress")
        or event.get("silence_seconds", 0) < policy["minimum_no_progress_seconds"]
        or event.get("healthy_tool_running")
        or event.get("waiting_for_user")
    ):
        reason = "silence_is_not_failure"
    if run and event.get("terminal") and reason in (None, "terminal_run"):
        run_state = state.setdefault(run, {"keys": [], "candidate_count": 0, "last_review_s": None, "postmortem_seen": False})
        run_state["terminal_seen"] = True
    if reason:
        return {"run_id": run, "kind": kind, "decision": "suppress", "reason": reason, "llm_invoked": False}

    run_state = state.setdefault(run, {"keys": [], "candidate_count": 0, "last_review_s": None, "postmortem_seen": False})
    key = hashlib.sha256(json.dumps([run, kind, event["evidence_digest"]], separators=(",", ":")).encode()).hexdigest()
    if key in run_state["keys"] or (kind == "run_completed" and run_state["postmortem_seen"]):
        reason = "duplicate_evidence"
    elif run_state["candidate_count"] >= policy["observer"]["max_calls_per_run"]:
        reason = "review_quota_exhausted"
    elif (kind != "run_completed" and run_state["last_review_s"] is not None
          and event["timestamp_s"] - run_state["last_review_s"] < policy["observer"]["cooldown_seconds"]):
        reason = "cooldown"
    if reason:
        return {"run_id": run, "kind": kind, "decision": "suppress", "reason": reason, "llm_invoked": False}
    run_state["keys"].append(key)
    run_state["candidate_count"] += 1
    run_state["last_review_s"] = event["timestamp_s"]
    run_state["postmortem_seen"] |= kind == "run_completed"
    return {"run_id": run, "kind": kind, "decision": "review_candidate", "requires_user_confirmation": True, "invocation_authorized": False, "reason": "new_actionable_evidence", "evidence_refs": event["evidence_refs"], "dedup_key": key, "llm_invoked": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("events", type=Path)
    p.add_argument("--policy", type=Path, default=Path(__file__).with_name("observer-policy.json"))
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    policy = json.loads(args.policy.read_text())
    if policy["mode"] != "shadow":
        p.error("Only shadow replay is implemented; no live enforcement or LLM dispatch.")
    decisions, state = [], {}
    for number, line in enumerate(args.events.read_text().splitlines(), 1):
        if line.strip():
            event = json.loads(line)
            decisions.append({"source_line": number, **decide(event, state, policy)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"mode": "shadow", "llm_invocations": 0, "decisions": decisions, "replay_state": state}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"events": len(decisions), "review_candidates": sum(d["decision"] == "review_candidate" for d in decisions), "llm_invocations": 0}))


if __name__ == "__main__":
    main()
