import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gate import decide


class GateTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / "observer-policy.json").read_text())
        self.state = {}
        self.event = dict(run_id="run1", kind="no_progress", timestamp_s=1000,
                          evidence_refs=["event:1"], evidence_digest="a",
                          silence_seconds=600, expected_progress=True,
                          healthy_tool_running=False, waiting_for_user=False,
                          observation_age_seconds=0)

    def run_event(self, **changes):
        return decide({**self.event, **changes}, self.state, self.policy)

    def test_unchanged_heartbeats_never_wake_model(self):
        for i in range(100):
            result = self.run_event(kind="heartbeat", timestamp_s=i)
            self.assertEqual(result["decision"], "suppress")
        self.assertFalse(self.state)

    def test_event_cannot_authorize_astra(self):
        result = self.run_event(approved=True, user_confirmed=True)
        self.assertEqual(result['decision'], 'review_candidate')
        self.assertTrue(result['requires_user_confirmation'])
        self.assertFalse(result['invocation_authorized'])
        self.assertFalse(result['llm_invoked'])

    def test_duplicate_ignores_new_timestamp(self):
        self.assertEqual(self.run_event()["decision"], "review_candidate")
        self.assertEqual(self.run_event(timestamp_s=5000)["reason"], "duplicate_evidence")

    def test_silence_during_healthy_work_is_not_stall(self):
        self.assertEqual(self.run_event(healthy_tool_running=True)["reason"], "silence_is_not_failure")
        self.assertEqual(self.run_event(waiting_for_user=True)["reason"], "silence_is_not_failure")

    def test_observer_cannot_trigger_itself(self):
        self.assertEqual(self.run_event(origin="observer")["reason"], "observer_feedback_excluded")

    def test_excluded_terminal_cannot_poison_run(self):
        self.run_event(origin="observer", terminal=True)
        self.run_event(terminal=True, evidence_refs=[])
        self.assertEqual(self.run_event()["decision"], "review_candidate")

    def test_unknown_or_stale_tool_state_is_not_stall(self):
        self.assertEqual(self.run_event(healthy_tool_running=None)["reason"], "missing_or_stale_observation")
        self.assertEqual(self.run_event(observation_age_seconds=61)["reason"], "missing_or_stale_observation")

    def test_cooldown_and_run_quota(self):
        self.run_event()
        self.assertEqual(self.run_event(evidence_digest="b", timestamp_s=1001)["reason"], "cooldown")
        self.assertEqual(self.run_event(evidence_digest="b", timestamp_s=1900)["decision"], "review_candidate")
        self.assertEqual(self.run_event(evidence_digest="c", timestamp_s=9000)["reason"], "review_quota_exhausted")

    def test_postmortem_once_and_terminal_stops_other_events(self):
        self.assertEqual(self.run_event(terminal=True)["reason"], "terminal_run")
        self.assertEqual(self.run_event(kind="run_completed", terminal=True)["decision"], "review_candidate")
        self.assertEqual(self.run_event(kind="run_completed", terminal=True, evidence_digest="b")["reason"], "duplicate_evidence")
        self.assertEqual(self.run_event(timestamp_s=9999, evidence_digest="c")["reason"], "terminal_run")

    def test_missing_evidence_and_existing_action(self):
        self.assertEqual(self.run_event(evidence_refs=[])["reason"], "missing_evidence")
        self.assertEqual(self.run_event(pending_action=True)["reason"], "action_already_pending")


if __name__ == "__main__":
    unittest.main()
