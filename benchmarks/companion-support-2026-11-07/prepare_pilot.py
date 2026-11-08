#!/usr/bin/env python3
"""Freeze a declared, unchanged-fixture subset before candidate inference."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
EQ = ROOT / 'vendor/eqbench3'
sys.path.insert(0, str(EQ))
from core.benchmark import parse_scenario_prompts

IDS = ['1', '2', '3', '6', '9', '13', '131', '133', '137', '302']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    source = EQ / 'data/scenario_prompts.txt'
    scenarios = parse_scenario_prompts(str(source))
    titles = {}
    for line in source.read_text().splitlines():
        match = re.match(r'^########\s*(\S+)\s*\|\s*(.*?)\s*\|\s*(.*)', line)
        if match:
            titles[match[1]] = [match[2], match[3]]
    chosen = [{'id': sid, 'category': titles[sid][0], 'title': titles[sid][1],
               'prompts': scenarios[sid]} for sid in IDS]
    fixture = {
        'benchmark': 'EQ-Bench 3 custom ten-scenario pilot',
        'upstream_commit': 'affc5a000bbdb965b807e09ca7875f8530fa8f56',
        'selection': 'Declared coverage selection before inference, not random or representative',
        'selected_ids': IDS, 'scenarios': chosen,
        'master_prompt': (EQ / 'data/scenario_master_prompt.txt').read_text(),
        'debrief_prompt': (EQ / 'data/debrief_prompt.txt').read_text(),
        'source_sha256': {str(p.relative_to(EQ)): digest(p) for p in [source,
            EQ / 'data/scenario_master_prompt.txt', EQ / 'data/debrief_prompt.txt',
            EQ / 'core/conversation.py', EQ / 'data/rubric_scoring_criteria.txt',
            EQ / 'data/rubric_scoring_prompt.txt']},
    }
    target = ROOT / 'fixtures/eq10.json'
    target.parent.mkdir(exist_ok=True)
    encoded = json.dumps(fixture, indent=2, ensure_ascii=False) + '\n'
    if target.exists() and target.read_text() != encoded:
        raise SystemExit('Refusing to replace an existing different selection')
    target.write_text(encoded)
    print(json.dumps({'eq_scenarios': len(chosen),
        'scenario_turns': sum(len(s['prompts']) for s in chosen),
        'debrief_calls': len(chosen), 'fixture_sha256': digest(target)}))


if __name__ == '__main__':
    main()
