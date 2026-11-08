"""Recover only debrief calls blocked before inference by a known adapter assert.

Completed dialogue responses are never regenerated. Actual model failures are
not eligible. The original errored task state is archived byte-for-byte.
"""
import json
from pathlib import Path
import shutil

from codex_cli_judge import save
from nanbeige_provider import EQAPI, MODEL


def recover(run, scenario_class):
    run=Path(run)
    manifest=run/'adapter-debrief-recovery.json'
    if not manifest.exists():return
    settings=json.loads(manifest.read_text())
    assert settings['kind']=='pre-inference-debrief-profile-assertion'
    archive=run/'adapter-recovery/original-scenarios';archive.mkdir(parents=True,exist_ok=True)
    api=EQAPI(run);api.index=len(list((run/'calls').iterdir()))
    recovered=[]
    for path in sorted((run/'scenarios').glob('*.json')):
        record=json.loads(path.read_text())
        if not (record.get('status')=='error' and record.get('scenario_run_error') is None
                and record.get('debrief_run_error')=='' and record.get('error')=='Debrief Run Error: '
                and record.get('debrief_response') is None
                and len(record.get('parsed_responses',[]))==len(record['prompts'])):
            continue
        target=archive/path.name
        if target.exists():raise RuntimeError('Recovery already attempted; refusing to retry model output')
        shutil.copyfile(path,target)
        task=scenario_class.from_dict(record)
        task.status='scenario_completed'
        api.case,api.stage=task.scenario_id,'debrief'
        print(f'Recovering previously uncalled adapter-blocked debrief {task.scenario_id}',flush=True)
        task.run_debrief({'test':api},None,None,MODEL)
        save(path,task.to_dict())
        recovered.append({'id':task.scenario_id,'status':task.status,'error':task.error})
        save(run/'adapter-recovery/progress.json',{'attempted_missing_debriefs':len(recovered),'rows':recovered,
             'dialogue_responses_regenerated':0,'previous_model_debrief_calls_retried':0})
