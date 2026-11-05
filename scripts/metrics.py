#!/usr/bin/env python3
"""Offline analysis of metadata-only cascade JSONL; no services or models required."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import glob
import json
import math
from pathlib import Path
import statistics
import sys


def utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def percentile(values, fraction):
    ordered = sorted(values)
    at = (len(ordered) - 1) * fraction
    lower = int(at)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (at - lower)


def files_for(inputs):
    paths = set()
    for value in inputs:
        path = Path(value).expanduser()
        matches = path.glob("*.jsonl*") if path.is_dir() else map(Path, glob.glob(str(path)))
        paths.update(item.resolve() for item in matches if item.is_file())
    return sorted(paths)


def read_records(paths, *, session=None, since=None):
    records, counts = [], Counter()
    for path in paths:
        with path.open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                counts["lines"] += 1
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("not an object")
                except (ValueError, UnicodeError):
                    counts["malformed_lines"] += 1
                    continue
                if record.get("schema_version") != 1:
                    counts["unsupported_schema"] += 1
                    continue
                # Spans are a separate representation of the same work, not new samples.
                if record.get("record_type") not in {"stage", "turn", "session"}:
                    counts["excluded_records"] += 1
                    continue
                if session and record.get("session_id") != session:
                    continue
                if since:
                    try:
                        if utc(record["timestamp"]) < since:
                            continue
                    except (KeyError, ValueError, TypeError, AttributeError):
                        counts["invalid_timestamps"] += 1
                        continue
                if not isinstance(record.get("metrics", {}), dict):
                    counts["malformed_lines"] += 1
                    continue
                records.append(record)
    return records, counts


def summarize(records, counts, file_count):
    grouped = defaultdict(list)
    for record in records:
        key = tuple(str(record.get(name) or "-") for name in ("record_type", "stage", "model")) + tuple(
            str((record.get("configuration") or {}).get(name) or "unknown")
            for name in ("stt_backend", "stt_mode", "stt_threads"))
        grouped[key].append(record)
    groups = []
    for key, rows in sorted(grouped.items()):
        statuses = Counter(str(row.get("status") or "unknown") for row in rows)
        by_status = []
        for status in sorted(statuses):
            selected = [row for row in rows if str(row.get("status") or "unknown") == status]
            names = sorted({name for row in selected for name in row.get("metrics", {})})
            metrics = {}
            for name in names:
                values = [row.get("metrics", {}).get(name) for row in selected]
                valid = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0]
                missing = sum(value is None for value in values)
                metrics[name] = {"count": len(valid), "missing": missing, "invalid": len(values) - missing - len(valid),
                    "min": min(valid) if valid else None, "p50": percentile(valid, 0.5) if valid else None,
                    "p95": percentile(valid, 0.95) if valid else None, "max": max(valid) if valid else None,
                    "mean": statistics.mean(valid) if valid else None}
            by_status.append({"status": status, "records": len(selected), "metrics": metrics})
        groups.append(dict(zip(("record_type", "stage", "model", "stt_backend", "stt_mode", "stt_threads"), key), records=len(rows), status_counts=dict(statuses), by_status=by_status))
    return {"files": file_count, "records": len(records), "read_counts": dict(counts),
        "percentile_method": "linear interpolation; small sample p95 is descriptive only", "groups": groups}


def write_csv(records, output):
    columns = ["timestamp", "session_id", "turn_id", "request_id", "trace_id", "span_id", "record_type", "stage", "model", "status", "stt_backend", "stt_mode", "stt_threads", "metric", "value"]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for record in records:
        base = {name: record.get(name) for name in columns[:-2]}
        base.update({name: (record.get("configuration") or {}).get(name) for name in ("stt_backend", "stt_mode", "stt_threads")})
        for name, value in sorted(record.get("metrics", {}).items()):
            if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)):
                writer.writerow({**base, "metric": name, "value": value})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=[str(Path(__file__).resolve().parents[1] / ".local/metrics")], help="JSONL paths, directories or quoted globs, including rotated files")
    parser.add_argument("--format", choices=("text", "json", "csv"), default="text")
    parser.add_argument("--session", help="Only this session UUID")
    parser.add_argument("--since", type=utc, help="UTC ISO timestamp, e.g. 2026-11-05T00:00:00Z")
    args = parser.parse_args()
    paths = files_for(args.paths)
    if not paths:
        parser.error("No JSONL files found; run a voice session or supply the configured metrics directory")
    records, counts = read_records(paths, session=args.session, since=args.since)
    result = summarize(records, counts, len(paths))
    if args.format == "json":
        print(json.dumps(result, indent=2, allow_nan=False))
    elif args.format == "csv":
        write_csv(records, sys.stdout)
    else:
        print(f"{result['records']} records from {result['files']} files; read counts: {dict(counts)}")
        print("Each request/turn is one sample; spans excluded. Percentiles separated by outcome. Units are in metric names.")
        for group in result["groups"]:
            print(f"\n{group['record_type']} / {group['stage']} / {group['model']} / {group['stt_backend']}:{group['stt_mode']}:{group['stt_threads']}: {group['status_counts']}")
            for status in group["by_status"]:
                for name, metric in status["metrics"].items():
                    numbers = " ".join(f"{key}={metric[key]:.3f}" for key in ("p50", "p95", "max") if metric[key] is not None)
                    print(f"  {status['status']} {name}: n={metric['count']} missing={metric['missing']} invalid={metric['invalid']} {numbers}")


if __name__ == "__main__":
    main()
