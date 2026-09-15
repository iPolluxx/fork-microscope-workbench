# generated: Codex — exact-token adapter for Anthropic's pinned Jacobian lens.
"""Read-only lens investigations. No fitting, implicit model loading or retokenization."""
from pathlib import Path
from types import SimpleNamespace
from collections import OrderedDict
import copy
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import time

from fork_microscope.investigation import digest, source, whole
from fork_microscope.model_preflight import same_model_identity

JLENS_COMMIT = '581d398613e5602a5af361e1c34d3a92ea82ba8e'
HUB_COMMIT = '16a01f309fcec900fdcec3f4cd5b64f3d00e4d5a'
SITE = 'decoder_block_output_before_final_norm'
MUSE_REVISION = 'a4e59da52a7bc87ae7251dd5545c0dd437c44b68'
CACHE_BYTES = 32 * 1024**2


class ActivationCache:
    """Bounded CPU rows, private to one loaded model. Never persisted or shared."""
    def __init__(self, limit=CACHE_BYTES):
        self.limit, self.bytes, self.rows = limit, 0, OrderedDict()

    def get(self, key):
        row = self.rows.get(key)
        if row is not None:
            self.rows.move_to_end(key)
        return row

    def put(self, key, row):
        size = row.numel() * row.element_size()
        if size > self.limit:
            return
        if key in self.rows:
            old = self.rows.pop(key)
            self.bytes -= old.numel() * old.element_size()
        self.rows[key] = row.detach().cpu().clone()
        self.bytes += size
        while self.bytes > self.limit:
            _, old = self.rows.popitem(last=False)
            self.bytes -= old.numel() * old.element_size()


def activation_cache(adapter, backend="native"):
    # Reloads, in-place parameter updates, dtype/device moves and adapter changes
    # invalidate reuse. No cross-user disk cache or untrusted imported rows.
    config = adapter.model.config.get_text_config()
    rope = getattr(config, 'rope_scaling', None) or getattr(config, 'rope_parameters', None) or {}
    # Length-dependent rotary embeddings can change even earlier states when
    # the replay length changes. Conservatively disable reuse for these modes.
    fixed_rope = rope.get('rope_type', rope.get('type', 'default')) in ('default', 'linear', 'llama3', 'yarn')
    limit = CACHE_BYTES if fixed_rope and not adapter.model.training else 0
    signature = (id(adapter.model), digest(adapter.info), SITE, digest(rope), limit, backend,
                 getattr(adapter.model.config, '_attn_implementation', None),
                 tuple((p.data_ptr(), p._version, str(p.dtype), str(p.device))
                       for p in adapter.model.parameters()))
    if getattr(adapter, '_lens_cache_signature', None) != signature:
        adapter._lens_cache_signature = signature
        adapter._lens_activation_cache = ActivationCache(limit)
    return adapter._lens_activation_cache


def position_keys(arm):
    """Hash the complete causal prefix at each requested absolute position."""
    selected = {p['absolute_position'] for p in arm['positions']}
    sha, keys = hashlib.sha256(), []
    for i, token in enumerate(arm['input_ids']):
        sha.update(int(token).to_bytes(8, 'big'))
        if i in selected:
            keys.append(sha.hexdigest())
    return keys


def comparison_region(arms):
    if len(arms) != 2 or arms[0]['prompt_ids'] != arms[1]['prompt_ids']:
        return None
    a, b = (arm['response_ids'] for arm in arms)
    common = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    differing = common if common < min(len(a), len(b)) else None
    return dict(shared_response_tokens=common, first_different_token=differing,
                prefix_only=differing is None and len(a) != len(b),
                baseline_space='response' if common else 'prompt',
                baseline_index=common-1 if common else len(arms[0]['prompt_ids'])-1,
                interpretation='First differing saved token, not a proven causal decision boundary. Shared prefixes have identical deterministic states.')


def work_estimate(adapter, arms, selected, final_layer, backend="native"):
    cache, available, forwards, replay_tokens = activation_cache(adapter, backend), set(), 0, 0
    for arm in arms:
        needed = {(key, layer) for key in position_keys(arm) for layer in set(selected) | {final_layer}}
        if any(key not in available and cache.get(key) is None for key in needed):
            forwards += 1
            replay_tokens += len(arm['input_ids'])
        if cache.limit:
            available.update(needed)
    return dict(forward_passes=forwards, replay_tokens=replay_tokens,
                activation_cache_limit_bytes=cache.limit,
                note='Estimate for the current loaded model. Full decoder depth still runs for uncached prefixes; expanding layers or positions can require another forward pass. Cache eviction can increase work.')
PROFILES = {
    'muse_glimmer': dict(label='Muse-Glimmer-30B · community n900 lens', model_id='meta-models/Muse-Glimmer-30B',
        repo_id='eyes-ml/Muse-Glimmer-30B_jacobian-lens', revision='71d8434fbd38c8b5d70e1ff1ff2095d5da926c34',
        fitted_model_revision=MUSE_REVISION,
        provenance='Publisher fit on eyes-ml mirror 97e6fe0a8d8d221b100cd67f53fccf0744950abf; Hugging Face weight and tokenizer content hashes match pinned Meta revision.',
        filename='Muse-Glimmer-30B_jacobian_lens.pt',
        sha256='397dc00807a8f72d9d21feaeedeba7fb9e7c50e5c80efc317f37e8cd9c833300',
        size=4518854369, width=6656, layer_count=52),
    'qwen35_4b': dict(label='Qwen3.5-4B · published n1000 lens', model_id='Qwen/Qwen3.5-4B',
        filename='qwen3.5-4b/jlens/Salesforce-wikitext/Qwen3.5-4B_jacobian_lens_n1000.pt',
        sha256='1f9a8f8fd593f0ffec1a9640993257ca4560f8ae3e5602315643d5cc6818534e', size=406332644,
        width=2560, layer_count=32),
    'qwen36_27b': dict(label='Qwen3.6-27B · published n1000 lens', model_id='Qwen/Qwen3.6-27B',
        filename='qwen3.6-27b/jlens/Salesforce-wikitext/Qwen3.6-27B_jacobian_lens_n1000.pt',
        sha256='1718c8c52dd8a9dad03738d4d625937c1fbba10be325b872ed446c7290fc11e1', size=3303032772,
        width=5120, layer_count=64),
}


def components(adapter):
    from jlens.hf import _find_layout, HFLensModel
    layout = _find_layout(adapter.model)
    text = adapter.model
    for part in layout.path.split('.'):
        text = getattr(text, part)
    config = adapter.model.config.get_text_config()
    reader = SimpleNamespace(_final_norm=getattr(text, layout.norm), _lm_head=getattr(adapter.model, layout.lm_head),
                             _logit_softcap=getattr(config, 'final_logit_softcapping', None))
    if getattr(adapter.model.config, 'model_type', '') == 'muse_glimmer':
        import torch
        # Native Muse applies the multiplier BEFORE softcapping. Upstream omits it.
        reader._logit_softcap = None
        def unembed(x):
            logits = HFLensModel.unembed(reader, x) * config.output_multiplier
            cap = config.final_logit_softcapping
            return torch.tanh(logits / cap) * cap
        return getattr(text, layout.layers), config.hidden_size, unembed
    # Use the upstream unembedding implementation without its mutating constructor.
    return getattr(text, layout.layers), config.hidden_size, lambda x: HFLensModel.unembed(reader, x)


def options(adapter=None):
    installed = importlib.util.find_spec('jlens') is not None
    layers = None
    if adapter is not None and installed:
        try:
            layers = len(components(adapter)[0])
        except (ValueError, AttributeError):
            pass
    from fork_microscope.nnsight_inspection import availability
    backends = [dict(id='native', label='Standard capture', available=True, reason='Existing local capture with model-specific unembedding validation.'), availability(adapter)]
    return dict(profiles=[dict(id=key, **value) for key, value in PROFILES.items()], local_supported=True,
                installed=installed, model=adapter.info if adapter else None, layer_count=layers,
                inspection_backends=backends)


def lens_spec(value, adapter):
    if type(value) is not dict or not isinstance(value.get('profile'), str):
        raise ValueError('Choose a lens profile.')
    if value['profile'] in PROFILES and set(value) == {'profile'}:
        spec = dict(repo_id='neuronpedia/jacobian-lens', revision=HUB_COMMIT, fitted_model_revision=None,
                    site=SITE, provenance='upstream named-model pairing; fitted model revision unknown')
        spec.update(PROFILES[value['profile']], profile=value['profile'])
        spec['target_layer'] = spec['layer_count']-1
        if adapter.info['model_id'] != spec['model_id'] or adapter.info.get('source_type') == 'local':
            raise ValueError('This published lens is paired with '+spec['model_id']+'. Use a matching model or a local lens with its provenance manifest.')
        if spec['fitted_model_revision'] and adapter.info.get('resolved_revision') != spec['fitted_model_revision']:
            raise ValueError('This lens requires the exact verified model revision: '+spec['fitted_model_revision'])
        return spec
    if set(value) != {'profile', 'path'} or value['profile'] != 'local' or not isinstance(value['path'], str):
        raise ValueError('Local lenses require a worker path and adjacent .json provenance manifest.')
    path = Path(value['path']).expanduser().resolve()
    if not path.is_file() or path.suffix != '.pt' or not 0 < path.stat().st_size <= 8*1024**3:
        raise ValueError('Local lens must be a .pt file of at most 8 GiB.')
    manifest = Path(str(path)+'.json')
    if not manifest.is_file() or manifest.stat().st_size > 65536:
        raise ValueError('Supply '+str(manifest)+' with model_id, resolved_revision, lens_sha256, site and target_layer.')
    meta = json.loads(manifest.read_text())
    if type(meta) is not dict or not {'model_id', 'resolved_revision', 'lens_sha256', 'site', 'target_layer'} <= set(meta):
        raise ValueError('The lens manifest is missing its provenance fields.')
    if meta['site'] != SITE or not same_model_identity(adapter.info, meta):
        raise ValueError('Local lens manifest must match the exact attached model revision and decoder output site.')
    if type(meta['target_layer']) is not int or meta['target_layer'] != len(components(adapter)[0])-1:
        raise ValueError('Lens target_layer must be the final decoder block; intermediate-target lenses require another transport.')
    sha = meta['lens_sha256']
    if not isinstance(sha, str) or len(sha) != 64 or any(x not in '0123456789abcdef' for x in sha):
        raise ValueError('Invalid lens SHA-256 in the manifest.')
    return dict(profile='local', path=str(path), sha256=sha, size=path.stat().st_size, site=SITE,
                model_id=meta['model_id'], fitted_model_revision=meta['resolved_revision'],
                target_layer=meta['target_layer'],
                provenance='user-supplied exact-model manifest; content hash checked on execution')


def selected_arms(adapter, result, base, selection, get_investigation):
    if type(selection) is not dict:
        raise ValueError('Choose an original, saved draw or edit/control source.')
    if selection == {'type':'original'}:
        return [('original', base['prompt_ids'], base['gen_ids'])]
    if set(selection) == {'type', 'checkpoint', 'draw_indices'} and selection['type'] == 'draw_pair':
        indices = selection['draw_indices']
        if type(indices) is not list or len(indices) != 2 or any(type(i) is not int for i in indices) or indices[0] == indices[1]:
            raise ValueError('Choose two distinct saved continuations at one checkpoint.')
        arms, labels = [], []
        for name, index in zip(('draw_a', 'draw_b'), indices):
            arm = selected_arms(adapter, result, base, dict(type='draw', checkpoint=selection['checkpoint'], draw_index=index), get_investigation)[0]
            observations = [b.get('observations', [])[i] for b in result['_selected_record'].get('branches', [])
                            if b['t'] == selection['checkpoint'] for i, draw in enumerate(b.get('draw_indices', []))
                            if draw == index and i < len(b.get('observations', []))]
            if len(observations) != 1 or not observations[0].get('stop_reason') or observations[0]['stop_reason'] == 'length' or observations[0].get('label') in (None, '', 'Other'):
                raise ValueError('Outcome comparisons need completed, classified continuations. Inspect unfinished or Other draws individually.')
            labels.append(observations[0]['label'])
            arms.append((name, arm[1], arm[2]))
        if labels[0] == labels[1]:
            raise ValueError('Choose continuations with different recorded outcomes.')
        return arms
    if set(selection) == {'type', 'checkpoint', 'draw_index'} and selection['type'] == 'draw':
        whole(selection['checkpoint'], 'Checkpoint', 0, len(base['gen_ids'])-1)
        whole(selection['draw_index'], 'Draw index', 0, 100000)
        matches=[]
        for branch in result['_selected_record'].get('branches', []):
            if branch['t'] != selection['checkpoint']:
                continue
            for i, draw in enumerate(branch.get('draw_indices', [])):
                if draw == selection['draw_index']:
                    continuations=branch.get('continuation_ids', [])
                    if i >= len(continuations):
                        raise ValueError('This draw has no saved continuation token IDs; text cannot substitute for exact IDs.')
                    matches.append(('draw', base['prompt_ids'], base['gen_ids'][:branch['t']]+[branch['tok_id']]+continuations[i]))
        if len(matches) != 1:
            raise ValueError('Choose one uniquely recorded draw at this checkpoint.')
        return matches
    if set(selection) == {'type', 'investigation_id'} and selection['type'] == 'edit_pair':
        data=get_investigation(selection['investigation_id'])
        if data.get('request', {}).get('kind') != 'edit' or data.get('request', {}).get('source_run_id') != result['id'] or data['request'].get('source_pass_id') != result['_selected_pass']:
            raise ValueError('Choose an edit/control investigation from this source run and pass.')
        if data.get('source_ids_sha256') != digest({'prompt_ids':base['prompt_ids'], 'gen_ids':base['gen_ids']}) or not same_model_identity(adapter.info, data.get('model', {})):
            raise ValueError('Edit/control source identity does not match this saved trace.')
        return [(arm,base['prompt_ids'],data['arms'][arm]) for arm in ('control','edit')]
    raise ValueError('Invalid lens source selection.')


def build_plan(adapter, result, request, get_investigation):
    fields={'source_run_id','source_pass_id','lens','selection','space','start','end','layers','top_k'}
    if type(request) is not dict or set(request) not in (fields, fields | {'inspection_backend'}) or request['source_run_id'] != result['id']:
        raise ValueError('Invalid lens request fields or source run.')
    backend = request.get('inspection_backend', 'native')
    if backend not in ('native', 'nnsight'):
        raise ValueError('Choose standard or NNsight inspection capture.')
    if backend == 'nnsight':
        from fork_microscope.nnsight_inspection import require
        require(adapter)
    if not importlib.util.find_spec('jlens'):
        raise ValueError('Install the optional lens package on this worker: uv pip install --python .venv/bin/python --no-deps -r requirements/lens.txt')
    if adapter.model.training:
        raise ValueError('Lens inspection requires the attached model in evaluation mode.')
    base=source(adapter,result,request)
    layers,width,_=components(adapter)
    spec=lens_spec(request['lens'],adapter)
    if spec.get('width',width) != width or spec.get('layer_count',len(layers)) != len(layers):
        raise ValueError('The published lens does not match this model width or layer count.')
    requested=request['layers']
    if type(requested) is not list or not 1 <= len(requested) <= 8:
        raise ValueError('Select 1–8 decoder layers.')
    for layer in requested:whole(layer,'Layer',0,len(layers)-1)
    if spec['profile'] != 'local' and len(layers)-1 in requested:
        raise ValueError('Published profiles do not fit a matrix for the final decoder block. Select earlier layers; the actual final-layer baseline is included automatically.')
    if len(set(requested)) != len(requested):raise ValueError('Layer indices must be unique.')
    if request['space'] not in ('prompt','response'):raise ValueError('Choose prompt or response token positions.')
    whole(request['start'],'Start',0,1000000);whole(request['end'],'End',request['start'],request['start']+63)
    whole(request['top_k'],'Top tokens',2,min(30,adapter.info['vocab_size']))
    selected=dict(result,_selected_record=result['records'][request['source_pass_id']],_selected_pass=request['source_pass_id'])
    arms=[]
    for name,prompt,response in selected_arms(adapter,selected,base,request['selection'],get_investigation):
        for seq in (prompt,response):
            if type(seq) is not list or any(type(i) is not int or not 0<=i<adapter.info['vocab_size'] for i in seq):
                raise ValueError('Source token IDs are outside this model vocabulary.')
        target=prompt if request['space']=='prompt' else response
        if request['end'] >= len(target):raise ValueError(f'Region ends beyond the {name} {request["space"]} ({len(target)} tokens).')
        offset=0 if request['space']=='prompt' else len(prompt)
        positions=[dict(index=i,absolute_position=offset+i,token_id=target[i],text=adapter.tokenizer.decode([target[i]],skip_special_tokens=False)) for i in range(request['start'],request['end']+1)]
        ids=(prompt+response)[:offset+request['end']+1]
        if len(ids)>adapter.info['context_limit']:raise ValueError('Selected prefix exceeds the model context limit.')
        arm = dict(id=name,prompt_ids=list(prompt),response_ids=list(response),input_ids=ids,positions=positions,prefix_sha256=digest(ids))
        if request['selection']['type'] == 'draw_pair':
            draw_index = request['selection']['draw_indices'][len(arms)]
            record = selected['_selected_record']
            observation = next(b['observations'][i] for b in record['branches'] if b['t'] == request['selection']['checkpoint']
                               for i, draw in enumerate(b['draw_indices']) if draw == draw_index)
            arm['source_draw'] = dict(checkpoint=request['selection']['checkpoint'], draw_index=draw_index,
                                      outcome=observation['label'], stop_reason=observation['stop_reason'])
        arms.append(arm)
    warnings=['Ranked lens tokens are approximate vocabulary readouts, not outcome probabilities, a complete hidden chain of thought, or causal proof.',
              'Source region was selected after inspecting behavior. Validate hypotheses with controlled interventions and independent examples.']
    if spec['fitted_model_revision'] is None:warnings.append('Upstream pairs this lens with a model name but does not publish its fitted model revision. Exact fit provenance is unknown; shape checks and forward parity cannot establish it.')
    if spec['profile'] == 'muse_glimmer':warnings.append('Community text-only fit: publisher reports 900 prompts and an unmet convergence threshold. Real-model readout quality has not been independently validated here.')
    if request['selection']['type'] == 'draw_pair':warnings.append('These saved draws share the checkpoint prefix and have different recorded outcomes. Equal token indices after branching are not semantic alignment. Readouts are observational; outcome selection does not establish causation.')
    elif len(arms)>1:warnings.append('Control and edit columns use the same numeric positions. Different tokenizations or lengths are not semantically aligned. These readouts inspect prefixes, not the sampled continuations of the edit experiment.')
    inspection = dict(requested_backend=backend, effective_backend=backend, mode='read_only_exact_token_replay',
                      package_version=importlib.metadata.version('nnsight') if backend == 'nnsight' else None)
    if backend == 'nnsight':
        warnings.append('Experimental NNsight capture is tested on tiny random CPU models; Muse compatibility and performance are not established. The lens mathematics and fit limitations are unchanged.')
    return dict(schema='fork-lens-v1',inspection=inspection,request=copy.deepcopy(request),model=dict(adapter.info),lens=spec,arms=arms,
                cells=len(arms)*len(positions)*len(requested),width=width,site=SITE,warnings=warnings,
                comparison=comparison_region(arms),work=work_estimate(adapter,arms,requested,len(layers)-1,backend),
                interpretation='Readouts describe the state AFTER each input token. The final-layer baseline predicts the NEXT token. Jacobian readouts are approximate vocabulary projections, not next-token probabilities. Uncached prefixes run through the full decoder, cut at the last selected position. No new text is generated.',
                upstream_commit=JLENS_COMMIT,attention_implementation=getattr(adapter.model.config,'_attn_implementation',None))


def load_checkpoint(spec, width, layers, check, progress):
    import torch
    if spec['profile']=='local':path=Path(spec['path'])
    else:
        from huggingface_hub import hf_hub_download
        progress('Downloading or opening the pinned lens file…')
        path=Path(hf_hub_download(spec['repo_id'],filename=spec['filename'],revision=spec['revision']))
    if not path.is_file() or path.stat().st_size != spec['size']:
        raise ValueError('Lens file size changed; preview again or restore the expected artifact.')
    sha=hashlib.sha256()
    with path.open('rb') as handle:
        while chunk:=handle.read(8*1024**2):
            check();sha.update(chunk)
    if sha.hexdigest()!=spec['sha256']:raise ValueError('Lens checksum does not match its pinned profile or provenance manifest.')
    progress('Validating memory-mapped lens matrices…')
    data=torch.load(path,map_location='cpu',weights_only=True,mmap=True)
    if type(data) is not dict or data.get('d_model') != width or type(data.get('J')) is not dict:
        raise ValueError('Lens file has incompatible dimensions or format.')
    if type(data.get('n_prompts')) is not int or data['n_prompts']<1:
        raise ValueError('Lens fitting sample count is missing or invalid.')
    for layer in layers:
        matrix=data['J'].get(layer)
        if not isinstance(matrix,torch.Tensor) or matrix.layout!=torch.strided or matrix.shape!=(width,width) or not matrix.is_floating_point():
            raise ValueError(f'Lens matrix for layer {layer} is missing or incompatible.')
    return data


def run(adapter, plan, check, progress, save):
    import torch
    from jlens import JacobianLens
    layers,width,unembed=components(adapter)
    if width != plan['width']:raise ValueError('Model width changed after preview.')
    selected=plan['request']['layers'];top_k=plan['request']['top_k']
    backend=plan['request'].get('inspection_backend','native')
    if backend not in ('native', 'nnsight'):raise ValueError('Unknown inspection capture backend.')
    if backend == 'nnsight':
        from fork_microscope.nnsight_inspection import require
        require(adapter)
    data=load_checkpoint(plan['lens'],width,selected,check,progress)
    cache=activation_cache(adapter,backend)
    out=dict(plan,id=plan['id'],status='running',created=time.time(),cells=[],parity=[],
             execution=dict(forward_passes=0,replay_tokens=0,reused_activation_rows=0,captured_activation_rows=0),
             lens=dict(plan['lens'],n_prompts=data['n_prompts']),
             versions={p:importlib.metadata.version(p) for p in ('torch','transformers','jlens')})
    save(out)
    def rank(logits):
        if not torch.isfinite(logits).all():raise ValueError('Non-finite lens scores.')
        values,indices=logits.float().cpu().topk(top_k)
        return [dict(token_id=int(i),text=adapter.tokenizer.decode([int(i)],skip_special_tokens=False),rank=j+1,score=float(v)) for j,(v,i) in enumerate(zip(values,indices))]
    for arm in plan['arms']:
        check();progress('Reading exact '+arm['id']+' token IDs…',len(out['cells']),plan['cells'])
        ids=torch.tensor([arm['input_ids']],device=adapter.device)
        indices=[p['absolute_position'] for p in arm['positions']]
        keys=position_keys(arm)
        required=sorted(set(selected)|{len(layers)-1})
        rows={(layer,i):cache.get((key,layer)) for layer in required for i,key in enumerate(keys)}
        out['execution']['reused_activation_rows'] += sum(row is not None for row in rows.values())
        missing={layer:[i for i in range(len(keys)) if rows[layer,i] is None] for layer in required}
        needs_forward=any(missing.values())
        captured,handles={},[]
        def hook_for(layer):
            def hook(module,args,output):
                hidden=output[0] if isinstance(output,tuple) else output
                if not isinstance(hidden,torch.Tensor) or hidden.ndim!=3 or hidden.shape[:2]!=ids.shape or hidden.shape[-1]!=width:
                    raise ValueError('Unexpected residual shape; lens readout stopped.')
                captured[layer]=hidden[0,[indices[i] for i in missing[layer]]].detach().float().cpu().clone()
            return hook
        final_check=[]
        if needs_forward and backend == 'nnsight':
            from fork_microscope.nnsight_inspection import capture as nnsight_capture
            kw={'logits_to_keep':1} if 'logits_to_keep' in inspect.signature(adapter.model.forward).parameters or getattr(adapter,'is_muse',False) else {}
            requested_positions={layer:[indices[i] for i in missing[layer]] for layer in required if missing[layer]}
            captured,final,actual=nnsight_capture(adapter,layers,ids,requested_positions,width,kw,check)
            final_check.append(final)
            out['execution']['forward_passes']+=1
            out['execution']['replay_tokens']+=len(arm['input_ids'])
        else:
            try:
                for layer in required:
                    if missing[layer]:handles.append(layers[layer].register_forward_hook(hook_for(layer)))
                # Always capture the last final state for a fresh parity check when
                # doing a new forward, including when only earlier layers are new.
                final_check=[]
                if needs_forward:
                    handles.append(layers[-1].register_forward_hook(lambda m,args,output: final_check.append(
                        (output[0] if isinstance(output,tuple) else output)[0,-1:].detach().float().cpu().clone())))
                kw={'logits_to_keep':1} if 'logits_to_keep' in inspect.signature(adapter.model.forward).parameters or getattr(adapter,'is_muse',False) else {}
                if needs_forward:
                    with torch.inference_mode():
                        actual=adapter.model(ids,attention_mask=torch.ones_like(ids),use_cache=False,**kw).logits[0,-1].detach().float().cpu()
                    out['execution']['forward_passes']+=1
                    out['execution']['replay_tokens']+=len(arm['input_ids'])
            finally:
                for handle in handles:handle.remove()
        if set(captured)!={layer for layer in required if missing[layer]}:raise ValueError('Some requested decoder layers did not execute.')
        atol,rtol=(.05,.02) if next(adapter.model.parameters()).dtype in (torch.float16,torch.bfloat16) else (1e-4,1e-4)
        if needs_forward:
            with torch.inference_mode():decoded=unembed(final_check[0])[0].float().cpu()
            if not torch.allclose(decoded,actual,atol=atol,rtol=rtol):
                raise ValueError('Final-layer readout does not match full-model logits. This architecture needs a verified unembedding adapter.')
            out['parity'].append(dict(arm=arm['id'],absolute_position=indices[-1],max_absolute_error=float((decoded-actual).abs().max()),atol=atol,rtol=rtol,mode='fresh_forward'))
        else:
            out['parity'].append(dict(arm=arm['id'],absolute_position=indices[-1],max_absolute_error=None,mode='reused_activations_from_parity_checked_forward'))
        for layer in required:
            for j,i in enumerate(missing[layer]):
                rows[layer,i]=captured[layer][j]
                cache.put((keys[i],layer),rows[layer,i])
                out['execution']['captured_activation_rows']+=1
        captured={layer:torch.stack([rows[layer,i] for i in range(len(keys))]) for layer in required}
        with torch.inference_mode():
            final_tokens=[]
            for i in range(len(arm['positions'])):
                check();final_tokens.append(rank(unembed(captured[len(layers)-1][i:i+1])[0]))
        for layer in selected:
            check();matrix=data['J'][layer]
            if not torch.isfinite(matrix).all():raise ValueError('Lens matrix contains non-finite entries.')
            # Convert and apply only one requested layer at a time; do not inflate all matrices onto the GPU.
            lens=JacobianLens({layer:matrix},n_prompts=data['n_prompts'],d_model=width)
            with torch.inference_mode():
                transported=lens.transport(captured[layer],layer)
                # One vocabulary row at a time bounds projection memory for large vocabularies.
                for i,position in enumerate(arm['positions']):
                    check()
                    tokens=rank(unembed(transported[i:i+1])[0])
                    baseline=rank(unembed(captured[layer][i:i+1])[0])
                    final=final_tokens[i]
                    out['cells'].append(dict(arm=arm['id'],layer=layer,**position,tokens=tokens,logit_lens_tokens=baseline,model_tokens=final))
            del lens,transported
            progress('Decoded '+arm['id']+' layer '+str(layer),len(out['cells']),plan['cells']);save(out)
    out.update(status='complete',finished=time.time());save(out)
    return out
