import importlib.util
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("performance_report", ROOT / "scripts/performance-report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def test_metadata_cannot_escape_embedded_json_and_missing_is_preserved(self):
        marker = '</script><script>globalThis.attacked=true</script>'
        data = {"schema_version": 1, "summary": marker, "comparisons": [{"id": "public-a", "label": marker, "metrics": {"latency_p50_ms": None}}]}
        html = report.render(data, (ROOT / "reports/performance-template.html").read_text())
        payload = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(json.loads(payload), data)
        self.assertNotIn(marker, html)
        self.assertEqual(html.count("</script>"), 2)

    def test_invalid_data_is_not_published_as_a_report(self):
        for data in ({"schema_version": 2, "comparisons": []}, {"schema_version": 1, "comparisons": [{"id": "a"}, {"id": "a"}]}):
            with self.assertRaises(ValueError):
                report.render(data, "__REPORT_DATA__")
        with self.assertRaises(ValueError):
            report.render({"schema_version": 1, "comparisons": [{"id": "a", "metrics": {"latency": float("nan")}}]}, "__REPORT_DATA__")


if __name__ == "__main__":
    unittest.main()
