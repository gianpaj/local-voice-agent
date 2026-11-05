import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).with_name("benchmark-stt.py")
spec = importlib.util.spec_from_file_location("benchmark_stt", path)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class QualityScoreTests(unittest.TestCase):
    def test_reference_scoring_and_numeric_renderings(self):
        score = benchmark.score("Ask Michael for twenty three cars.", "Ask Michael for 23 cars.",
                                [["Michael"], ["twenty three", "23"]])
        self.assertEqual(score["word_errors"], 2)
        self.assertEqual(score["probe_matches"], 2)
        self.assertEqual(score["silence_false_positive"], 0)

    def test_failure_coverage_and_missing_latency_not_zero(self):
        rows = [{"backend": "whistle", "status": "timeout", "reference_words": 4, "score": None}]
        result = benchmark.summary(rows)[0]
        self.assertEqual(result["outcomes"], {"timeout": 1})
        self.assertEqual(result["speech_attempts_total"], 1)
        self.assertEqual(result["speech_attempts_scored"], 0)
        self.assertIsNone(result["wer_successful_only"])
        self.assertIsNone(result["latency_successful_speech_ms"])

    def test_silence_false_positive(self):
        self.assertEqual(benchmark.score("", "Invented speech")["silence_false_positive"], 1)
        self.assertEqual(benchmark.score("", "")["silence_false_positive"], 0)


if __name__ == "__main__":
    unittest.main()
