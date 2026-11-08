#!/usr/bin/env python3
"""Audit completed response collection without inventing semantic EQ scores."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent
RUN = ROOT / 'runs/qwen-eq10'


def percentile(values, p):
    values = sorted(values)
    index = (len(values) - 1) * p
    lo = int(index)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def main():
    progress = json.loads((RUN / 'progress.json').read_text())
    if progress['completed'] != 10:
        raise SystemExit('Collection incomplete; no final report written')
    manifest = json.loads((RUN / 'manifest.json').read_text())
    restore = json.loads((RUN / 'restore.json').read_text())
    calls = [json.loads(line) for line in (RUN / 'calls.jsonl').read_text().splitlines()]
    resources = list(csv.DictReader((RUN / 'resources.csv').open()))
    fixture = json.loads((ROOT / 'fixtures/eq10.json').read_text())
    rows = []
    transcripts = []
    for case in fixture['scenarios']:
        task = json.loads((RUN / 'scenarios' / (case['id'] + '.json')).read_text())
        parsed = task.get('parsed_responses') or []
        sections = len(parsed) == len(case['prompts']) and all(all(item.get(key, '').strip() for key in
            ['thinking_feeling', 'their_thinking_feeling', 'response']) for item in parsed)
        rows.append({'scenario_id': case['id'], 'title': case['title'],
            'status': task['status'], 'expected_dialogue_turns': len(case['prompts']),
            'collected_dialogue_turns': len(parsed), 'required_sections_present': sections,
            'debrief_present': bool((task.get('debrief_response') or '').strip()),
            'quality_score': None, 'quality_status': 'awaiting independent judging',
            'error': task.get('error')})
        transcripts.append({'scenario_id': case['id'], 'title': case['title'],
            'messages': task['conversation_history'], 'parsed_responses': parsed,
            'debrief_response': task.get('debrief_response'), 'status': task['status']})
    valid = [row for row in calls if not row.get('error')]
    first_content = [row['first_answer_token_seconds'] for row in valid
                     if row.get('first_answer_token_seconds') is not None]
    rates = [row['eval_count'] / (row['eval_duration_ns'] / 1e9) for row in valid
             if row.get('eval_duration_ns') and row.get('eval_count')]
    stats = {'model': 'Qwen3.5 9B Q4_K_M', 'context_tokens': 32768,
        'expected_scenarios': 10, 'finished_scenarios': len(rows),
        'complete_response_sets': sum(row['status'] == 'completed' for row in rows),
        'required_section_sets_present': sum(row['required_sections_present'] for row in rows),
        'calls': len(calls), 'expected_calls': 38, 'collection_errors': sum(bool(row.get('error')) for row in calls),
        'generated_tokens': sum(row.get('eval_count') or 0 for row in calls),
        'quality_score': None, 'quality_status': 'awaiting independent judging',
        'retail_tasks_finished': 0, 'retail_status': 'awaiting independent simulator/judge route',
        'official_full_suite_score': False, 'voice_latency_measured': False,
        'median_server_decode_tokens_per_second': round(statistics.median(rates), 3) if rates else None,
        'first_content_seconds_p50': round(statistics.median(first_content), 3) if first_content else None,
        'first_content_seconds_p95': round(percentile(first_content, .95), 3) if first_content else None,
        'first_content_note': 'Completion content includes introspective sections; not usable spoken reply or audio latency. Small-sample descriptive percentiles.',
        'call_seconds_p50': round(statistics.median([row['seconds'] for row in valid]), 3) if valid else None,
        'max_prompt_eval_tokens': max((row.get('prompt_eval_count') or 0 for row in calls), default=0),
        'peak_gpu_mib': max(int(row['gpu_used_mib']) for row in resources),
        'minimum_available_host_ram_gib': round(min(int(row['host_available_kib']) for row in resources) / 1024**2, 3),
        'started_at': manifest['started_at'], 'finished_at': manifest.get('finished_at'),
        'fixture_sha256': hashlib.sha256((ROOT / 'fixtures/eq10.json').read_bytes()).hexdigest(),
        'restored_ready': (restore.get('voice_health') or {}).get('ready'),
        'restored_busy': (restore.get('voice_health') or {}).get('busy'), 'rows': rows}
    for path, value in [('summary.json', stats), ('transcripts.json', transcripts)]:
        (RUN / path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    with (RUN / 'scenario-results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (RUN / 'call-metrics.csv').open('w', newline='') as stream:
        renamed = [{('first_content_token_seconds' if key == 'first_answer_token_seconds' else key): value
                    for key, value in row.items()} for row in calls]
        writer = csv.DictWriter(stream, fieldnames=list(renamed[0]))
        writer.writeheader()
        writer.writerows(renamed)
    lines = ['# Qwen EQ pilot: generation results', '',
        f"Response sets complete: **{stats['complete_response_sets']}/10**; API calls: {len(calls)}/38; collection errors: {stats['collection_errors']}.",
        '**EQ quality score: not assigned.** Independent rubric judging is still pending.',
        '**Retail outcome: not run.** The independent customer simulator/judge route remains unresolved.',
        'These are not ten passed EQ tasks and not an official leaderboard result.', '',
        f"Generated tokens: {stats['generated_tokens']}; median server decode: {stats['median_server_decode_tokens_per_second']} tokens/s.",
        f"First completion-content token: p50 {stats['first_content_seconds_p50']} s; p95 {stats['first_content_seconds_p95']} s.",
        'That content includes the required introspective sections. It is not the first useful spoken',
        'reply or end-to-end voice latency. Percentiles are descriptive for this small selected sample.',
        f"Median full-call duration: {stats['call_seconds_p50']} s; maximum observed input: {stats['max_prompt_eval_tokens']} tokens.",
        f"Peak aggregate GPU allocation: {stats['peak_gpu_mib']} MiB; minimum observed host available RAM: {stats['minimum_available_host_ram_gib']} GiB.", '',
        '## Per-scenario collection', '',
        '| ID | Scenario | Status | Sections | Debrief | Quality |',
        '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        lines.append(f"| {row['scenario_id']} | {row['title']} | {row['status']} | {row['required_sections_present']} | {row['debrief_present']} | Pending |")
    lines += ['', '## Interpretation', '',
        'The model produced complete structured transcripts on the selected cases. Format/content presence',
        'checks are not semantic empathy, helpfulness or policy-compliance grading. No comparison with',
        'Nanbeige is available yet, and no inference about TTS event/animal-sound capability follows.', '',
        'The upstream prompts and debriefs were preserved. The model used a separate 32K benchmark',
        'profile instead of the voice application’s 4K/128-token preset. No silent prompt truncation,',
        'candidate failure retries, spoken-audio measurements or paid evaluation requests were used.', '',
        'Original Qwen voice profile restoration is recorded in restore.json and independently checked',
        'after collection. Full raw streams remain local; reproducible fixtures, transcripts and metrics',
        'are suitable for the public learning record with the upstream license notices.']
    (RUN / 'RESULTS.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({k: stats[k] for k in ['complete_response_sets', 'calls', 'collection_errors',
        'generated_tokens', 'quality_status', 'peak_gpu_mib', 'restored_ready', 'restored_busy']}))


if __name__ == '__main__':
    main()
