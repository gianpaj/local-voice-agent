"""Experimental summaries must not silently turn into transcript exports."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("assemble", Path(__file__).with_name("assemble-performance-data.py"))
assemble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assemble)


class AssemblePrivacyTests(unittest.TestCase):
    def test_rust_sample_excludes_unknown_text_paths_and_settings(self):
        sample = assemble.safe_rust_sample({
            "fixture_id":"public-fixture", "fixture_sha256":"a"*64,
            "stt_model_adapter_ms":100, "transcript":"DO_NOT_PERSIST",
            "error":"DO_NOT_PERSIST", "path":"DO_NOT_PERSIST",
            "model_settings":{"llm":{"system_prompt":"DO_NOT_PERSIST", "api_key":"DO_NOT_PERSIST"}},
            "resource_observations":{"gpu_total_sampled_peak_mib":9000, "private_path":"DO_NOT_PERSIST"},
        })
        self.assertNotIn("DO_NOT_PERSIST", json.dumps(sample))
        self.assertEqual(sample["stt_model_adapter_ms"],100)
        self.assertIsNone(sample["model_settings"]["llm"]["system_prompt"])
        self.assertIsNone(sample["tts_model_first_pcm_ms"])


if __name__ == "__main__":
    unittest.main()
