#!/usr/bin/env python3
"""Own the matched EQ+retail Nanbeige run, CLI judging, and voice recovery."""
import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import threading
import time

import requests

from codex_cli_judge import save
import run_qwen_eq as ownership
import nanbeige_provider as provider
from candidate_failure import CandidateFailure

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'runs/nanbeige-iq2m'
RUNTIME=Path(os.environ['LLAMA_RUNTIME'])
CUDA=Path(os.environ['LLAMA_CUDA_LIB'])
WEIGHTS=Path(os.environ['NANBEIGE_GGUF'])


def phase(value,**extra):
    save(RUN/'progress.json',{'phase':value,'updated_at':ownership.now(),**extra})
    print(value,extra,flush=True)


def monitor(stop):
    with (RUN/'resources.csv').open('w') as handle:
        writer=csv.writer(handle);writer.writerow(['utc','gpu_used_mib','gpu_free_mib','host_available_kib'])
        while not stop.is_set():
            values=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,memory.free','--format=csv,noheader,nounits'],text=True).strip().split(',')
            available=next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))
            writer.writerow([ownership.now(),*[int(x) for x in values],int(available)]);handle.flush()
            stop.wait(2)


def baseline_check():
    expected=json.loads((ROOT/'fixtures/retail10.json').read_text())['selected_task_ids']
    cases=[]
    for sid in expected:
        p=ROOT/'runs/qwen-retail10/tasks'/f'{sid}.json'
        failure=ROOT/'runs/qwen-retail10/tasks'/f'{sid}.failure.json'
        if not p.exists() and not failure.exists():raise RuntimeError(f'Qwen baseline task {sid} unfinished')
        cases.append(json.loads((p if p.exists() else failure).read_text()))
    health=requests.get('http://127.0.0.1:3000/api/health',timeout=5).json()
    if not health.get('ready') or health.get('busy'):raise RuntimeError('Qwen voice recovery not ready/idle; no replacement performed')
    ps=requests.get('http://127.0.0.1:11434/api/ps',timeout=5).json()
    qwen=next(m for m in ps['models'] if m['name']=='local-voice-qwen:latest')
    assert qwen['context_length']==4096
    save(ROOT/'runs/qwen-retail10/post-run-verification.json',{
        'checked_at':ownership.now(),'voice':{k:health.get(k) for k in ('ready','busy','phase')},
        'qwen_context':qwen['context_length'],'service':subprocess.check_output(['systemctl','--user','show',ownership.SERVICE,'--property=ActiveState,SubState,FreezerState'],text=True)})
    save(RUN/'qwen-baseline.json',{'finished':len(cases),'passed':sum((c.get('reward_info') or {}).get('reward',c.get('reward'))==1 for c in cases),
                                 'eq_rubric':json.loads((ROOT/'runs/qwen-eq10/codex-judge/summary.json').read_text())})


def run_eq():
    target=RUN/'eq10';(target/'scenarios').mkdir(parents=True);(target/'calls').mkdir()
    ownership.RUN=target
    fixture=json.loads((ROOT/'fixtures/eq10.json').read_text())
    api=provider.EQAPI(target)
    rows=[]
    phase('eq_collection',finished=0,expected=10)
    for case in fixture['scenarios']:
        api.case,api.stage=case['id'],'dialogue'
        task=ownership.PersistedTask(case['id'],case['prompts'],fixture['debrief_prompt'],0,
            'nanbeige-iq2m-eq10',fixture['master_prompt'])
        task.run_scenario({'test':api},None,None,provider.MODEL)
        if task.status=='scenario_completed':
            api.stage='debrief';task.run_debrief({'test':api},None,None,provider.MODEL)
        save(target/'scenarios'/f'{case["id"]}.json',task.to_dict())
        rows.append({'id':case['id'],'status':task.status,'error':task.error})
        save(target/'progress.json',{'finished':len(rows),'expected':10,'rows':rows})
        phase('eq_collection',finished=len(rows),expected=10)
    phase('eq_judging',collected=len(rows))
    # Judge only complete cases; retain errors/unscored coverage explicitly.
    subprocess.run([sys.executable,str(ROOT/'grade_qwen_eq.py'),'--run',str(target),'--allow-incomplete'],check=True)


def run_retail():
    from loguru import logger
    logger.remove();logger.add(sys.stderr,level='ERROR')
    from tau2.data_model.simulation import TextRunConfig
    from tau2.runner.batch import run_single_task
    from tau2.runner.helpers import select_run_tasks
    from tau2.runner.presets import load_cell_language
    from tau2.utils import llm_utils
    import tau2.evaluator.evaluator_nl_assertions as nl
    import run_qwen_retail as retail
    target=RUN/'retail10';(target/'tasks').mkdir(parents=True);(target/'calls').mkdir()
    baseline=json.loads((ROOT/'runs/qwen-retail10/protocol.json').read_text())
    config_data=copy.deepcopy(baseline['config']);config_data['llm_agent']=provider.MODEL
    config=TextRunConfig.model_validate(config_data)
    loaded=load_cell_language('en','retail','text');tasks=select_run_tasks(config,loaded)
    assert [t.id for t in tasks]==json.loads((ROOT/'fixtures/retail10.json').read_text())['selected_task_ids']
    save(target/'protocol.json',{'config':config.model_dump(mode='json'),
        'matched_qwen_protocol':True,'backend':'llama.cpp b12064 CUDA',
        'kv_cache':'Q4_0','non_thinking':True,'tool_call_format':'xml'})
    adapter=provider.retail_provider(target);llm_utils._generate_once=adapter.call
    nl.DEFAULT_LLM_NL_ASSERTIONS=retail.JUDGE
    nl.DEFAULT_LLM_NL_ASSERTIONS_ARGS={'reasoning_effort':'xhigh'}
    rows=[]
    for task in tasks:
        adapter.task_id=task.id;save(target/'tasks'/f'{task.id}.input.json',task.model_dump(mode='json'))
        try:
            result=run_single_task(config,task,seed=300,save_dir=None,auto_review=False)
        except CandidateFailure as exc:
            save(target/'tasks'/f'{task.id}.failure.json',{'task_id':task.id,
                'status':'candidate_budget_or_format_failure','reward':0.0,'candidate_budget_failure':True,
                'trajectory_complete':False,'upstream_outcome_evaluation_completed':False,'reason':str(exc)})
            rows.append({'id':task.id,'reward':0.0,'termination':'candidate_budget_or_format_failure'})
        else:
            save(target/'tasks'/f'{task.id}.json',result.model_dump(mode='json'))
            rows.append({'id':task.id,'reward':result.reward_info.reward,'termination':result.termination_reason.value})
        save(target/'progress.json',{'finished':len(rows),'expected':10,'rows':rows})
        phase('retail',finished=len(rows),expected=10,passed=sum(x['reward']==1 for x in rows))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--after-qwen-pid',type=int);args=parser.parse_args()
    if RUN.exists():raise RuntimeError('Existing run: refusing to overwrite/retry outcomes')
    RUN.mkdir(parents=True)
    if args.after_qwen_pid and Path(f'/proc/{args.after_qwen_pid}').exists():
        cmd=Path(f'/proc/{args.after_qwen_pid}/cmdline').read_bytes()
        assert b'run_qwen_retail.py' in cmd
        phase('waiting_for_qwen_baseline',pid=args.after_qwen_pid)
        if hasattr(os,'pidfd_open'):
            fd=os.pidfd_open(args.after_qwen_pid)
            try:
                if not select.select([fd],[],[],1800)[0]:raise TimeoutError('Qwen baseline still running after wait budget')
            finally:os.close(fd)
        else:
            subprocess.run(['tail',f'--pid={args.after_qwen_pid}','-f','/dev/null'],timeout=1800,check=True)
    baseline_check()
    retail_fixture=json.loads((ROOT/'fixtures/retail10.json').read_text())
    for relative, expected in retail_fixture['source_sha256'].items():
        assert hashlib.sha256((ROOT/'vendor/tau2-bench'/relative).read_bytes()).hexdigest()==expected
    manifest=json.loads((ROOT/'fixtures/nanbeige-iq2m.json').read_text())
    assert WEIGHTS.stat().st_size==manifest['bytes']
    with WEIGHTS.open('rb') as handle:actual=hashlib.file_digest(handle,'sha256').hexdigest()
    assert actual==manifest['sha256']
    save(RUN/'weights-verification.json',{**manifest,'actual_sha256':actual,'verified':True})
    save(RUN/'protocol.json',{'candidate':'Nanbeige4.3-33B IQ2_M','context':32768,'non_thinking':True,
        'sampling':'Per-stage Qwen settings unchanged','eq_output_cap':12000,'retail_output_cap':1024,
        'eq_deadline':600,'retail_deadline':180,'judge':'Codex CLI gpt-6.1-sol / xhigh',
        'runtime':'llama.cpp b12064 / 51fe42c5d CUDA','cache':'Q4_0 K/V',
        'differences':['llama.cpp rather than Ollama; native model-specific chat template',
            'Q4_0 KV cache to fit 16GB; Qwen cache/runtime is different',
            'Tokenizer counts differ; fixed limits measured in each model own vocabulary'],
        'eq_fixture_sha256':hashlib.sha256((ROOT/'fixtures/eq10.json').read_bytes()).hexdigest(),
        'retail_fixture_sha256':hashlib.sha256((ROOT/'fixtures/retail10.json').read_bytes()).hexdigest(),
        'official_leaderboard_score':False})
    server=None;frozen=False;stop=threading.Event();thread=threading.Thread(target=monitor,args=(stop,),daemon=True)
    def interrupt(signum,frame):raise InterruptedError(f'Signal {signum}')
    signal.signal(signal.SIGINT,interrupt);signal.signal(signal.SIGTERM,interrupt)
    try:
        fresh=requests.get('http://127.0.0.1:3000/api/health',timeout=5).json()
        if fresh.get('busy'):raise RuntimeError('New active voice call; no service replacement performed')
        subprocess.run(['systemctl','--user','freeze',ownership.SERVICE],check=True);frozen=True
        ownership.post('/api/generate',{'model':'local-voice-qwen:latest','keep_alive':0},60)
        thread.start();phase('loading_cuda')
        env=dict(os.environ,LD_LIBRARY_PATH=f'{RUNTIME}:{CUDA}'+(':'+os.environ['LD_LIBRARY_PATH'] if os.environ.get('LD_LIBRARY_PATH') else ''))
        command=[str(RUNTIME/'llama-server'),'--model',str(WEIGHTS),'--alias',provider.MODEL,
            '--host','127.0.0.1','--port','8083','--n-gpu-layers','99','--ctx-size','32768',
            '--parallel','1','--fit','off','--cache-type-k','q4_0','--cache-type-v','q4_0',
            '--flash-attn','on','--batch-size','128','--ubatch-size','128','--threads','6',
            '--jinja','--verbosity','4','--reasoning','off','--no-reasoning-preserve','--reasoning-format','deepseek',
            '--chat-template-kwargs','{"preserve_thinking":false,"tool_call_format":"xml"}']
        save(RUN/'server-command.json',{'argv':command})
        with (RUN/'server.log').open('w') as log:
            server=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
        for _ in range(180):
            if server.poll() is not None:raise RuntimeError('Nanbeige CUDA server exited during load')
            try:
                health=requests.get(provider.BASE+'/health',timeout=2)
                if health.status_code==200:break
            except requests.RequestException:pass
            stop.wait(1)
        else:raise TimeoutError('CUDA server not ready')
        text=(RUN/'server.log').read_text()
        matches=re.findall(r'offloaded (\d+)/(\d+) layers',text)
        assert matches and matches[-1][0]==matches[-1][1],'Full-GPU placement not confirmed'
        save(RUN/'server-props.json',requests.get(provider.BASE+'/props',timeout=10).json())
        smoke=provider.generate([{'role':'user','content':'Reply with READY only.'}],[],RUN/'smoke-text',cap=32,temperature=0.5,deadline=180)
        assert smoke['content'].strip() and not smoke['tool_calls']
        tools=[{'type':'function','function':{'name':'echo','description':'Return the integer value.',
            'parameters':{'type':'object','properties':{'value':{'type':'integer'}},'required':['value']}}}]
        smoke_tool=provider.generate([{'role':'user','content':'Call echo with value 7. Do not answer in text.'}],tools,RUN/'smoke-tool',cap=128,temperature=0.5,deadline=180)
        assert smoke_tool['tool_calls'] and smoke_tool['tool_calls'][0]['function']['name']=='echo'
        assert json.loads(smoke_tool['tool_calls'][0]['function']['arguments'])=={'value':7}
        phase('smoke_passed',non_thinking=True,full_gpu=True)
        run_eq();phase('retail',finished=0,expected=10);run_retail();phase('completed')
    except BaseException as exc:
        save(RUN/'error.json',{'error':f'{type(exc).__name__}: {exc}','at':ownership.now()})
        phase('error',error=type(exc).__name__)
        raise
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:server.wait(timeout=30)
            except subprocess.TimeoutExpired:server.kill();server.wait()
        stop.set()
        if thread.is_alive():thread.join(timeout=5)
        if frozen:
            ownership.RUN=RUN;ownership.restore()


if __name__=='__main__':
    try:main()
    except BaseException as exc:
        if RUN.exists() and not (RUN/'error.json').exists():
            save(RUN/'error.json',{'error':f'{type(exc).__name__}: {exc}','at':ownership.now()})
            phase('error',error=type(exc).__name__)
        raise
