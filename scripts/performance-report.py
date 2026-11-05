#!/usr/bin/env python3
"""Build a portable, offline HTML comparison from curated measured metadata."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def render(data, template):
    if data.get("schema_version") != 1 or not isinstance(data.get("comparisons"), list):
        raise ValueError("Expected schema_version=1 and comparisons array")
    ids = [row.get("id") for row in data["comparisons"]]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Comparison IDs must be unique nonempty strings")
    # JSON is inert data; escaping '<' prevents even an embedded closing-script
    # string from escaping its application/json element. UI uses textContent.
    payload = json.dumps(data, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    if template.count("__REPORT_DATA__") != 1:
        raise ValueError("Template must have exactly one data marker")
    return template.replace("__REPORT_DATA__", payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, help="Curated metadata-only comparison JSON")
    parser.add_argument("--output", type=Path, default=Path(".local/reports/performance.html"))
    args = parser.parse_args()
    data = json.loads(args.data.read_text())
    data.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    template = Path(__file__).resolve().parents[1] / "reports/performance-template.html"
    html = render(data, template.read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html)
    print(f"Wrote {args.output} ({len(data['comparisons'])} comparison cases)")


if __name__ == "__main__":
    main()
