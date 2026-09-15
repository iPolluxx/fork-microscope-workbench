"""Atomic prompt-set storage and immutable sequential-batch snapshots."""
from copy import deepcopy
import json
from pathlib import Path
import re
import threading
import time
import uuid
from fork_microscope.outcome_readout import validate_answers
from fork_microscope.sampling import pass_plan

ID = re.compile(r'^[a-f0-9]{32}$')
PROMPT_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
SCAN_FIELDS = {'stride','samples','cont_max','temperature','top_k','threshold','seed','tuning'}


def safe_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid workspace ID.')
    return value


def prompt(value):
    fields = {'id','title','prompt','answers','mode','max_tokens','seed'}
    if type(value) is not dict or set(value) != fields:
        raise ValueError('Each prompt needs id, title, prompt, answers, mode, max_tokens and seed.')
    if not isinstance(value['id'], str) or not PROMPT_ID.fullmatch(value['id']):
        raise ValueError('Invalid prompt ID.')
    for key, limit in [('title',120),('prompt',16000)]:
        if not isinstance(value[key], str) or not 1 <= len(value[key].strip()) <= limit:
            raise ValueError(f'Enter {key} text up to {limit} characters.')
    if value['mode'] not in ('chat','base'): raise ValueError('Choose chat or base mode.')
    for key, lo, hi in [('max_tokens',8,4096),('seed',0,2**31-1)]:
        if type(value[key]) is not int or not lo <= value[key] <= hi:
            raise ValueError(f'Invalid {key}.')
    return dict(value, answers=validate_answers(value['answers']))


def scan_config(scan, last):
    if type(scan) is not dict or set(scan) != SCAN_FIELDS:
        raise ValueError('Unexpected or missing batch scan settings.')
    # Explicit endpoints ensure short and off-grid traces are sampled too.
    stride = scan['stride']
    if type(stride) is not int or not 1 <= stride <= 128: raise ValueError('Stride must be 1–128.')
    if type(last) is not int or not 1 <= last <= 4095: raise ValueError('Trace needs 2–4096 tokens.')
    positions = list(range(0, last + 1, stride))
    if positions[-1] != last: positions.append(last)
    spec = dict(id='scan',label='Scan',start=0,end=last,stride=stride,offset=0,
        samples=scan['samples'],seed=scan['seed'],positions=positions)
    config = {k:scan[k] for k in ('cont_max','temperature','top_k','threshold','tuning')}
    config.update(dense=False,reference_samples=20,passes=[spec])
    pass_plan(config,last)
    return config


class WorkspaceStore:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        for name in ('sets','batches'): (self.root/name).mkdir(parents=True,exist_ok=True)

    def _read(self, kind, id):
        try: return json.loads((self.root/kind/(safe_id(id)+'.json')).read_text())
        except (OSError,json.JSONDecodeError) as exc: raise ValueError('Saved workspace record is unavailable.') from exc

    def _write(self, kind, value):
        path=self.root/kind/(safe_id(value['id'])+'.json')
        temp=path.with_suffix('.tmp')
        encoded=json.dumps(value,allow_nan=False)
        if len(encoded.encode())>2*1024*1024: raise ValueError('Workspace record exceeds 2 MB.')
        temp.write_text(encoded,encoding='utf-8');temp.replace(path)

    def list(self, kind):
        if kind not in ('sets','batches'): raise ValueError('Unknown workspace collection.')
        with self.lock:
            values=[]
            for file in (self.root/kind).glob('*.json'):
                try:
                    value=self._read(kind,file.stem)
                    if value.get('schema_version') != 1: continue
                    values.append(value)
                except (ValueError,TypeError,AttributeError): continue
            return sorted(values,key=lambda v:v.get('updated_at',0),reverse=True)

    def save_set(self, data):
        if type(data) is not dict or set(data)-{'id'} != {'name','prompts'}:
            raise ValueError('Provide a set name and prompts, with an optional saved ID.')
        if not isinstance(data['name'],str) or not 1<=len(data['name'].strip())<=120:
            raise ValueError('Enter a set name up to 120 characters.')
        if type(data['prompts']) is not list or not 1<=len(data['prompts'])<=50:
            raise ValueError('A set contains 1–50 prompts.')
        prompts=[prompt(p) for p in data['prompts']]
        if len({p['id'] for p in prompts})!=len(prompts): raise ValueError('Prompt IDs must be unique within a set.')
        with self.lock:
            previous=self._read('sets',data['id']) if data.get('id') else None
            if previous is None and len(self.list('sets'))>=100: raise ValueError('This workspace supports up to 100 prompt sets.')
            value=dict(schema_version=1,id=previous['id'] if previous else uuid.uuid4().hex,
                name=data['name'].strip(),revision=(previous['revision']+1) if previous else 1,
                updated_at=time.time(),prompts=prompts)
            self._write('sets',value)
            return deepcopy(value)

    def delete_set(self,id):
        with self.lock:
            self._read('sets',id)
            (self.root/'sets'/(safe_id(id)+'.json')).unlink()
        return dict(deleted=id)

    def prepare_batch(self,data,model):
        if type(data) is not dict or set(data)!={'set_id','prompt_ids','scan'}:
            raise ValueError('Select a saved set, prompt IDs and scan settings.')
        ids=data['prompt_ids']
        if type(ids) is not list or not ids or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)):
            raise ValueError('Select unique saved prompt IDs.')
        scan_config(data['scan'],1)
        with self.lock:
            source=self._read('sets',data['set_id']); indexed={p['id']:p for p in source['prompts']}
            if any(id not in indexed for id in ids): raise ValueError('A selected prompt is no longer in this set. Reload it.')
            return deepcopy(dict(schema_version=1,id=uuid.uuid4().hex,set_id=source['id'],
                set_name=source['name'],set_revision=source['revision'],model=model,
                scan=data['scan'],created_at=time.time(),updated_at=time.time(),state='pending',
                items=[dict(prompt_id=id,title=indexed[id]['title'],prompt_snapshot=indexed[id],state='pending') for id in ids]))

    def save_batch(self,value):
        with self.lock:
            value['updated_at']=time.time();self._write('batches',value)

    def recover(self):
        for batch in self.list('batches'):
            if batch['state'] in ('pending','running'):
                batch['state']='interrupted';batch['error']='Worker restarted. Completed evidence is retained; unfinished generation was not resumed.'
                for item in batch['items']:
                    if item['state'] in ('pending','running'): item['state']='interrupted'
                self.save_batch(batch)
