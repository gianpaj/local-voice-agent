"""Analysis must not hide failures, turn missing observations into zero, or double-count spans."""
import io
import json
from pathlib import Path
import tempfile
import unittest

import metrics


class MetricsAnalysisTests(unittest.TestCase):
    def test_cli_resident_and_legacy_are_separate_samples(self):
        rows = [{"record_type": "stage", "stage": "stt", "model": "same-model", "status": "ok", "metrics": {"total_ms": 300}}]
        rows += [{**rows[0], "configuration": {"stt_backend": "parakeet", "stt_mode": mode, "stt_threads": 4}} for mode in ("cli", "resident")]
        groups = metrics.summarize(rows, {}, 1)["groups"]
        self.assertEqual(len(groups), 3)
        self.assertEqual({g["stt_mode"] for g in groups}, {"unknown", "cli", "resident"})
        output = io.StringIO()
        metrics.write_csv(rows, output)
        self.assertIn("stt_backend,stt_mode,stt_threads", output.getvalue())
        self.assertIn("parakeet,resident,4", output.getvalue())

    def test_outcomes_and_grains_are_not_mixed(self):
        rows = [
            {"record_type": "stage", "stage": "tts", "model": "phonon", "status": "ok", "metrics": {"first_pcm_ms": value}}
            for value in (1, 10, 100)
        ] + [{"record_type": "stage", "stage": "tts", "model": "phonon", "status": "cancelled", "metrics": {"first_pcm_ms": None, "total_ms": 500}}]
        rows += [{"record_type": "turn", "status": "ok", "metrics": {"agent_ttfa_ms": 800}}]
        report = metrics.summarize(rows, {}, 1)
        stage = next(group for group in report["groups"] if group["record_type"] == "stage")
        self.assertEqual(stage["status_counts"], {"ok": 3, "cancelled": 1})
        outcomes = {row["status"]: row for row in stage["by_status"]}
        self.assertEqual(outcomes["ok"]["metrics"]["first_pcm_ms"]["p50"], 10)
        self.assertAlmostEqual(outcomes["ok"]["metrics"]["first_pcm_ms"]["p95"], 91)
        self.assertEqual(outcomes["cancelled"]["metrics"]["first_pcm_ms"]["missing"], 1)
        self.assertIsNone(outcomes["cancelled"]["metrics"]["first_pcm_ms"]["mean"])

    def test_rotations_partial_tail_and_spans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = {"schema_version": 1, "record_type": "turn", "timestamp": "2026-11-05T01:00:00Z", "session_id": "one", "metrics": {"agent_ttfa_ms": None}}
            (root / "cascade.jsonl.1").write_text(json.dumps(row) + "\n")
            (root / "cascade.jsonl").write_text(json.dumps({**row, "record_type": "span"}) + '\n{"partial":')
            paths = metrics.files_for([directory, str(root / "*.jsonl*")])
            self.assertEqual(len(paths), 2)
            records, counts = metrics.read_records(paths, session="one", since=metrics.utc("2026-11-05T00:00:00Z"))
            self.assertEqual(records, [row])
            self.assertEqual(counts["excluded_records"], 1)
            self.assertEqual(counts["malformed_lines"], 1)
            output = io.StringIO()
            metrics.write_csv(records, output)
            self.assertIn("agent_ttfa_ms,", output.getvalue())
            self.assertNotIn("agent_ttfa_ms,0", output.getvalue())

    def test_invalid_durations_are_counted_not_averaged(self):
        rows = [{"record_type": "turn", "status": "ok", "metrics": {"agent_ttfa_ms": value}} for value in (None, -1, float("nan"), True, 120)]
        result = metrics.summarize(rows, {}, 1)["groups"][0]["by_status"][0]["metrics"]["agent_ttfa_ms"]
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(result["invalid"], 3)
        self.assertEqual(result["p50"], 120)


if __name__ == "__main__":
    unittest.main()
