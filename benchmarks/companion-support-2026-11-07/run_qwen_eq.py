#!/usr/bin/env python3
"""Collect Qwen EQ responses with upstream scenario logic and recovery ownership."""
import ast
import csv
import datetime as dt
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor/eqbench3'))
from core.conversation import ScenarioTask

MODEL = os.environ.get('EVAL_MODEL', 'local-voice-qwen:latest')
BASE = os.environ.get('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')
SERVICE = os.environ.get('VOICE_SERVICE', 'local-voice-agent.service')
RUN = ROOT / 'runs/qwen-eq10'
RUNTIME = Path(os.environ['LLAMA_RUNTIME'])
WEIGHTS = Path(os.environ['QWEN_GGUF'])
CTX = 32768


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def post(path, payload, timeout=180):
    response = requests.post(BASE + path, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def token_count(messages):
    rendered = ''.join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages)
    rendered += '<|im_start|>assistant\n<think>\n\n</think>\n'
    env = dict(os.environ, LD_LIBRARY_PATH=str(RUNTIME))
    result = subprocess.run([str(RUNTIME / 'llama-tokenize'), '-m', str(WEIGHTS),
        '--stdin', '--ids', '--log-disable', '--no-bos'], input=rendered,
        capture_output=True, text=True, env=env, check=True, timeout=30)
    # Use this exact GGUF's vocabulary; add a conservative template safety margin.
    count = len(ast.literal_eval(result.stdout.strip()))
    return count, count + 256 + 16 * len(messages)


class LocalAPI:
    case = ''
    stage = ''
    index = 0

    def generate(self, model, messages, temperature=0.7, max_tokens=12000, min_p=0.1):
        self.index += 1
        label = f'{self.index:03d}-{self.case}-{self.stage}'
        raw_count, conservative_count = token_count(messages)
        if conservative_count + max_tokens > CTX:
            raise RuntimeError(f'Context preflight: {conservative_count}+{max_tokens}>{CTX}; no truncation')
        payload = {'model': model, 'messages': messages, 'stream': True,
            'think': False, 'keep_alive': '5m', 'options': {
                'num_ctx': CTX, 'num_predict': max_tokens, 'temperature': temperature,
                'min_p': min_p, 'top_p': 0.95, 'top_k': 20, 'presence_penalty': 0,
                'repeat_penalty': 1.0, 'seed': 42}}
        save(RUN / 'calls' / (label + '.request.json'), payload)
        start = time.perf_counter()
        first = None
        text_parts, thinking_parts = [], []
        done = None
        error = None
        try:
            with requests.post(BASE + '/api/chat', json=payload, stream=True,
                               timeout=(10, 180)) as response:
                response.raise_for_status()
                with (RUN / 'calls' / (label + '.response.jsonl')).open('w') as raw:
                    for line in response.iter_lines(chunk_size=1):
                        if not line:
                            continue
                        event = json.loads(line)
                        raw.write(json.dumps(event, ensure_ascii=False) + '\n')
                        raw.flush()
                        if event.get('error'):
                            raise RuntimeError(event['error'])
                        message = event.get('message') or {}
                        if message.get('content'):
                            if first is None:
                                first = time.perf_counter() - start
                            text_parts.append(message['content'])
                        if message.get('thinking'):
                            thinking_parts.append(message['thinking'])
                        if event.get('done'):
                            done = event
                        if time.perf_counter() - start > 600:
                            raise TimeoutError('600-second per-call cap exceeded')
            if not done:
                raise RuntimeError('Stream ended without a completed event')
            if done.get('done_reason') == 'length':
                raise RuntimeError('Generation reached output cap; incomplete response retained')
            if not ''.join(text_parts).strip():
                raise RuntimeError('Empty answer')
            if thinking_parts:
                raise RuntimeError('Unexpected hidden reasoning despite think=false')
            return ''.join(text_parts)
        except BaseException as exc:
            error = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            row = {'label': label, 'utc': now(), 'scenario_id': self.case,
                'stage': self.stage, 'seconds': time.perf_counter() - start,
                'first_answer_token_seconds': first, 'raw_prompt_token_estimate': raw_count,
                'conservative_prompt_tokens': conservative_count,
                'output_token_cap': max_tokens, 'prompt_eval_count': (done or {}).get('prompt_eval_count'),
                'eval_count': (done or {}).get('eval_count'), 'eval_duration_ns': (done or {}).get('eval_duration'),
                'finish_reason': (done or {}).get('done_reason'), 'error': error}
            with (RUN / 'calls.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
            print(json.dumps({'call': label, 'seconds': round(row['seconds'], 2),
                'answer_tokens': row['eval_count'], 'error': error}), flush=True)


class PersistedTask(ScenarioTask):
    def _save_progress(self, save_queue, run_key):
        save(RUN / 'scenarios' / f'{self.scenario_id}.json', self.to_dict())


def sample_resources(stop):
    with (RUN / 'resources.csv').open('w') as stream:
        writer = csv.writer(stream)
        writer.writerow(['utc', 'gpu_used_mib', 'gpu_free_mib', 'host_available_kib'])
        while not stop.is_set():
            try:
                result = subprocess.check_output(['nvidia-smi', '--query-gpu=memory.used,memory.free',
                    '--format=csv,noheader,nounits'], text=True).strip().split(',')
                available = next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                                 if line.startswith('MemAvailable:'))
                writer.writerow([now(), *(int(x.strip()) for x in result), int(available)])
                stream.flush()
            except Exception:
                logging.exception('Resource sample failed')
            stop.wait(2)


def restore():
    result = {'started_at': now()}
    try:
        post('/api/generate', {'model': MODEL, 'keep_alive': 0}, 60)
        post('/api/generate', {'model': MODEL, 'stream': False,
             'keep_alive': '5m', 'options': {'num_ctx': 4096}}, 120)
        result['qwen_default_context_reloaded'] = True
    except Exception as exc:
        result['reload_error'] = str(exc)
    finally:
        p = subprocess.run(['systemctl', '--user', 'thaw', SERVICE], capture_output=True, text=True)
        result['thaw_exit_code'] = p.returncode
    try:
        result['ollama_models'] = requests.get(BASE + '/api/ps', timeout=5).json()['models']
        h = requests.get('http://127.0.0.1:3000/api/health', timeout=5).json()
        result['voice_health'] = {k: h.get(k) for k in ['ready', 'busy', 'phase']}
    except Exception as exc:
        result['health_error'] = str(exc)
    result['finished_at'] = now()
    save(RUN / 'restore.json', result)


def main():
    if RUN.exists():
        raise SystemExit('Existing run: refusing to overwrite or silently retry candidate failures')
    (RUN / 'calls').mkdir(parents=True)
    (RUN / 'scenarios').mkdir()
    fixture_path = ROOT / 'fixtures/eq10.json'
    fixture = json.loads(fixture_path.read_text())
    manifest = {'started_at': now(), 'model': MODEL, 'context': CTX, 'think': False,
        'official_full_suite_score': False, 'scenarios': fixture['selected_ids'],
        'fixture_sha256': hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        'stage': 'response_collection', 'judge': None, 'retries': 0,
        'sampling': {'temperature': .7, 'min_p': .1, 'top_p': .95, 'top_k': 20,
            'presence_penalty': 0, 'repeat_penalty': 1.0, 'seed': 42},
        'generation_cap': 12000, 'per_call_deadline_seconds': 600}
    save(RUN / 'manifest.json', manifest)
    save(RUN / 'model-show.json', post('/api/show', {'model': MODEL}))
    health = requests.get('http://127.0.0.1:3000/api/health', timeout=5).json()
    if health.get('busy'):
        raise SystemExit('Active voice call detected; no service or model changed')
    def interrupt(signum, frame):
        raise InterruptedError(f'Signal {signum}')
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    stop = threading.Event()
    monitor = threading.Thread(target=sample_resources, args=(stop,), daemon=True)
    api = LocalAPI()
    completed = []
    try:
        subprocess.run(['systemctl', '--user', 'freeze', SERVICE], check=True)
        monitor.start()
        post('/api/generate', {'model': MODEL, 'keep_alive': 0}, 60)
        warm = post('/api/chat', {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Reply exactly READY.'}],
            'think': False, 'stream': False, 'keep_alive': '5m',
            'options': {'num_ctx': CTX, 'num_predict': 8, 'temperature': 0}}, 180)
        save(RUN / 'warmup.json', warm)
        ps = requests.get(BASE + '/api/ps', timeout=5).json()
        save(RUN / 'placement.json', ps)
        model = next(m for m in ps['models'] if m['name'] == MODEL)
        if model.get('context_length') != CTX or model['size_vram'] < .98 * model['size']:
            raise RuntimeError('32K full-GPU placement not verified; refusing silent offload/context change')
        for case in fixture['scenarios']:
            api.case, api.stage = case['id'], 'dialogue'
            task = PersistedTask(case['id'], case['prompts'], fixture['debrief_prompt'], 0,
                'qwen35-9b-q4km-eq10', fixture['master_prompt'])
            task.run_scenario({'test': api}, None, None, MODEL)
            if task.status == 'scenario_completed':
                api.stage = 'debrief'
                task.run_debrief({'test': api}, None, None, MODEL)
            completed.append({'id': case['id'], 'title': case['title'], 'status': task.status, 'error': task.error})
            save(RUN / 'progress.json', {'completed': len(completed), 'expected': 10,
                'responses_complete': sum(r['status'] == 'completed' for r in completed), 'rows': completed})
            print(json.dumps({'scenarios_finished': len(completed), 'expected': 10,
                'responses_complete': sum(r['status'] == 'completed' for r in completed)}), flush=True)
    except BaseException as exc:
        save(RUN / 'controller-error.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        stop.set()
        if monitor.is_alive():
            monitor.join(timeout=5)
        restore()
        manifest.update(finished_at=now(), completed_scenarios=len(completed))
        save(RUN / 'manifest.json', manifest)


if __name__ == '__main__':
    main()
