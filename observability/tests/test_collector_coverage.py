import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_run import parse_claude


class CoverageTests(unittest.TestCase):
    def test_empty_usage_cannot_claim_complete(self):
        self.assertFalse(parse_claude([])["coverage"]["usage_complete"])

    def test_missing_message_usage_is_explicit(self):
        rows = [(1, {"type": "assistant", "message": {"id": "a", "usage": {"output_tokens": 2}}}),
                (2, {"type": "assistant", "message": {"id": "b"}})]
        result = parse_claude(rows)
        self.assertFalse(result["coverage"]["usage_complete"])
        self.assertEqual(result["coverage"]["message_ids_without_usage"], ["b"])

    def test_partial_chunk_then_usage_is_one_message(self):
        rows = [(1, {"type": "assistant", "message": {"id": "a"}}),
                (2, {"type": "assistant", "message": {"id": "a", "usage": {"output_tokens": 2}}})]
        self.assertTrue(parse_claude(rows)["coverage"]["usage_complete"])


if __name__ == "__main__":
    unittest.main()
