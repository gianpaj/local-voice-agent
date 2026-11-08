"""Local llama.cpp transport for the matched Nanbeige IQ2_M pilot."""
import json
from pathlib import Path
import time

import requests

from codex_cli_judge import save
from candidate_failure import CandidateFailure

BASE = 'http://127.0.0.1:8083'
MODEL = 'nanbeige-iq2m'


def post(endpoint, payload):
    r = requests.post(BASE + endpoint, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def generate(messages, tools, directory, *, cap, temperature, deadline, min_p=0.1):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {'model':MODEL, 'messages':messages, 'stream':True,
        'stream_options':{'include_usage':True},
        'temperature':temperature, 'top_p':0.95, 'top_k':20, 'min_p':min_p,
        'presence_penalty':0, 'repeat_penalty':1.0, 'seed':42, 'max_tokens':cap,
        'chat_template_kwargs':{'enable_thinking':False,'preserve_thinking':False,'tool_call_format':'xml'}}
    if tools:
        payload.update(tools=tools, tool_choice='auto')
    rendered = post('/apply-template', payload)['prompt']
    (directory/'rendered-prompt.txt').write_text(rendered)
    count = len(post('/tokenize', {'content':rendered,'add_special':False,'parse_special':True})['tokens'])
    save(directory/'preflight.json', {'prompt_tokens':count,'output_cap':cap,'context':32768,'margin':128})
    if count + cap + 128 > 32768:
        raise CandidateFailure(f'Context preflight {count}+{cap}+128>32768; no truncation')
    save(directory/'request.json', payload)
    started, first = time.monotonic(), None
    parts, reasoning, calls, usage, timings, finish = [], [], {}, None, None, None
    error = None
    try:
        with requests.post(BASE+'/v1/chat/completions',json=payload,stream=True,timeout=(10,180)) as response:
            response.raise_for_status()
            with (directory/'response.jsonl').open('w') as raw:
                for line in response.iter_lines(chunk_size=1):
                    if not line or not line.startswith(b'data: '):
                        continue
                    text = line[6:]
                    if text == b'[DONE]':
                        break
                    event = json.loads(text)
                    raw.write(json.dumps(event,ensure_ascii=False)+'\n');raw.flush()
                    if event.get('error'):
                        raise RuntimeError(str(event['error']))
                    usage = event.get('usage') or usage
                    timings = event.get('timings') or timings
                    for choice in event.get('choices',[]):
                        delta = choice.get('delta',{})
                        if delta.get('content'):
                            if first is None:first=time.monotonic()-started
                            parts.append(delta['content'])
                        if delta.get('reasoning_content'):
                            reasoning.append(delta['reasoning_content'])
                        for call in delta.get('tool_calls') or []:
                            entry=calls.setdefault(call.get('index',0),{'id':'','type':'function','function':{'name':'','arguments':''}})
                            if call.get('id'):entry['id']=call['id']
                            function=call.get('function',{})
                            entry['function']['name']+=function.get('name','')
                            entry['function']['arguments']+=function.get('arguments','')
                        finish=choice.get('finish_reason') or finish
                    if time.monotonic()-started>deadline:
                        raise CandidateFailure(f'{deadline}-second candidate call deadline')
        if reasoning:
            raise CandidateFailure('Unexpected reasoning output in the matched non-thinking profile')
        if finish not in ('stop','tool_calls') or not usage:
            raise CandidateFailure(f'Incomplete generation ({finish}); retained, not silently retried')
        output=''.join(parts)
        parsed_calls=list(calls.values())
        for call in parsed_calls:
            json.loads(call['function']['arguments'])
        if not output and not parsed_calls:
            raise CandidateFailure('Empty candidate reply')
        return {'content':output,'tool_calls':parsed_calls,'usage':usage,'timings':timings}
    except BaseException as exc:
        error=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        save(directory/'timing.json', {'wall_seconds':time.monotonic()-started,
            'first_content_seconds':first,'usage':usage,'timings':timings,
            'finish_reason':finish,'reasoning_output_present':bool(reasoning),
            'rendered_prompt_tokens':count,'error':error})


class EQAPI:
    def __init__(self, run):
        self.run,self.index,self.case,self.stage=Path(run),0,'',''

    def generate(self, model, messages, temperature=0.7,max_tokens=12000,min_p=0.1):
        assert max_tokens==12000
        assert (temperature,min_p) in ((0.7,0.1),(0.5,None))
        self.index+=1
        path=self.run/'calls'/f'{self.index:03d}-{self.case}-{self.stage}'
        answer=generate(messages,[],path,cap=max_tokens,temperature=temperature,deadline=600,
                        min_p=0.0 if min_p is None else min_p)
        if answer['tool_calls']:raise RuntimeError('Unexpected tool call in EQ response')
        return answer['content']


def retail_provider(run):
    import run_qwen_retail as retail
    from tau2.data_model.message import AssistantMessage,ToolCall
    from tau2.utils import llm_utils

    retail.RUN=Path(run)
    class Provider(retail.Provider):
        def call(self, model,messages,tools=None,tool_choice=None,call_name=None,**kwargs):
            if model==retail.JUDGE:
                return super().call(model,messages,tools,tool_choice,call_name,**kwargs)
            if model!=MODEL:raise RuntimeError(f'Unapproved provider: {model}')
            self.index+=1
            directory=Path(run)/'calls'/f'{self.index:04d}-{self.task_id}-{call_name or "generation"}'
            rendered=llm_utils.to_litellm_messages(messages)
            schemas=[tool.openai_schema for tool in tools] if tools else []
            answer=generate(rendered,schemas,directory,cap=1024,temperature=0.5,deadline=180)
            timing=json.loads((directory/'timing.json').read_text())
            usage=answer['usage']
            reply=AssistantMessage(role='assistant',content=answer['content'] or None,
                tool_calls=[ToolCall(id=c['id'],name=c['function']['name'],
                    arguments=json.loads(c['function']['arguments'])) for c in answer['tool_calls']] or None,
                usage={'prompt_tokens':usage['prompt_tokens'],'completion_tokens':usage['completion_tokens']},
                cost=0.0,generation_time_seconds=timing['wall_seconds'])
            save(directory/'tau-response.json',reply.model_dump(mode='json'))
            return reply
    return Provider()
