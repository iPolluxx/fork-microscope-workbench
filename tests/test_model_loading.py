"""Deployment selection, asynchronous load reporting, and snapshot consistency."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('autoload', ROOT / 'docker/autoload.py')
autoload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(autoload)


def test_default_is_empty_and_autoload_requires_opt_in():
    assert autoload.load_config({}) is None
    assert not autoload.enabled({})
    assert not autoload.enabled({'AUTO_LOAD_MUSE': '0'})
    assert autoload.enabled({'AUTO_LOAD_MODEL': '1', 'AUTO_LOAD_MUSE': '0'})
    assert autoload.enabled({'AUTO_LOAD_MUSE': '1'})
    with pytest.raises(ValueError, match='0 or 1'):
        autoload.enabled({'AUTO_LOAD_MODEL': 'yes'})


def test_profile_selection_and_explicit_runtime_overrides():
    config = autoload.load_config({'FORK_MODEL_PROFILE': 'configs/cpu-smoke.json'})
    assert config['model_id'] == 'HuggingFaceTB/SmolLM2-135M-Instruct'
    assert config['device'] == 'cpu'
    config = autoload.load_config({'FORK_MODEL_ID': '/workspace/my-model',
        'FORK_MODEL_REVISION': 'local', 'FORK_MODEL_DEVICE': 'cpu', 'FORK_MODEL_BATCH_SIZE': '2'})
    assert config == dict(model_id='/workspace/my-model', revision='local', device='cpu', batch_size=2)


def test_cannot_inherit_another_models_revision():
    with pytest.raises(ValueError, match='requires FORK_MODEL_REVISION'):
        autoload.load_config({'FORK_MODEL_PROFILE': 'configs/muse-smoke.json', 'FORK_MODEL_ID': 'different/model'})


@pytest.mark.parametrize('overrides', [
    {'FORK_MODEL_DEVICE': 'cuda:9'}, {'FORK_MODEL_BATCH_SIZE': '0'},
    {'FORK_MODEL_BATCH_SIZE': '129'}, {'FORK_MODEL_BATCH_SIZE': '1.5'},
    {'FORK_MODEL_REVISION': '  '},
])
def test_invalid_deployment_settings_fail_before_http(overrides):
    with pytest.raises(ValueError):
        autoload.load_config({'FORK_MODEL_ID': 'test/model', **overrides})


def test_profile_supports_load_only_and_rejects_unexpected_fields(tmp_path):
    payload = autoload.load_config({'FORK_MODEL_ID': 'test/model'})
    path = tmp_path / 'model.json'
    path.write_text(json.dumps(payload))
    assert autoload.load_config({'FORK_MODEL_PROFILE': str(path)}) == payload
    payload['token'] = 'not-accepted-in-model-profile'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='only'):
        autoload.load_config({'FORK_MODEL_PROFILE': str(path)})


def test_autoload_waits_for_actual_model_ready(monkeypatch, capsys):
    monkeypatch.setattr(autoload, 'load_config', lambda: {'model_id': 'test/model'})
    monkeypatch.setattr(autoload, 'enabled', lambda: True)
    monkeypatch.setattr('sys.argv', ['autoload'])
    monkeypatch.setattr(autoload.time, 'sleep', lambda seconds: None)
    responses = iter([{}, {'job_id': 'job'},
        {'job': {'id': 'job', 'status': 'running', 'phase': 'Weights'}},
        {'job': {'id': 'job', 'status': 'complete', 'phase': 'Load complete'}, 'model': {'model_id': 'test/model'}}])
    calls = []
    def request(path, payload=None):
        calls.append((path, payload))
        return next(responses)
    monkeypatch.setattr(autoload, 'request_json', request)
    autoload.main()
    assert [x[0] for x in calls] == ['/api/live/status', '/api/live/load', '/api/live/status', '/api/live/status']
    assert 'Model ready:' in capsys.readouterr().out


def test_autoload_reports_job_error_not_success(monkeypatch):
    monkeypatch.setattr(autoload, 'load_config', lambda: {})
    monkeypatch.setattr(autoload, 'enabled', lambda: True)
    monkeypatch.setattr('sys.argv', ['autoload'])
    responses = iter([{}, {'job_id': 'job'},
        {'job': {'id': 'job', 'status': 'error', 'phase': 'CUDA out of memory'}}])
    monkeypatch.setattr(autoload, 'request_json', lambda *a: next(responses))
    with pytest.raises(RuntimeError, match='CUDA out of memory'):
        autoload.main()


@pytest.mark.parametrize('is_muse', [False, True])
@pytest.mark.parametrize('local', [False, True])
def test_adapter_pins_tokenizer_and_weights_to_resolved_config(monkeypatch, is_muse, local, tmp_path):
    from fork_microscope import live_model as lm
    calls = []
    config = SimpleNamespace(model_type='muse_glimmer' if is_muse else 'other', _commit_hash='a' * 40,
        vocab_size=8, max_position_embeddings=256)
    tok = SimpleNamespace(eos_token_id=2, pad_token_id=0, chat_template='template',
        get_vocab=lambda: {'<|eot|>': 2, '<|eom|>': 3})
    model = SimpleNamespace(config=config, generation_config=SimpleNamespace(eos_token_id=2),
        parameters=lambda: [], eval=lambda: model)
    def factory(name, value):
        def load(model_id, **kwargs):
            calls.append((name, model_id, kwargs))
            return value
        return load
    monkeypatch.setattr(lm.AutoConfig, 'from_pretrained', factory('config', config))
    monkeypatch.setattr(lm.AutoTokenizer, 'from_pretrained', factory('tokenizer', tok))
    monkeypatch.setattr(lm.AutoProcessor, 'from_pretrained', factory('processor', SimpleNamespace(tokenizer=tok)))
    cached = []
    monkeypatch.setattr(lm, 'cache_weights', lambda model_id, revision, *a: cached.append((model_id, revision)))
    # generated: this test uses lightweight stand-ins; mapping coverage is tested separately.
    monkeypatch.setattr(lm, 'native_loader', lambda config: 'AutoModelForImageTextToText' if is_muse else 'AutoModelForCausalLM')
    loader = lm.AutoModelForImageTextToText if is_muse else lm.AutoModelForCausalLM
    monkeypatch.setattr(loader, 'from_pretrained', factory('weights', model))
    phases = []
    model_id = 'test/model'
    if local:
        (tmp_path / 'config.json').write_text('{}')
        (tmp_path / 'model.safetensors').write_bytes(b'fake-weights')
        model_id = str(tmp_path)
    attached = lm.AttachedModel(model_id, 'main', 'cpu', 1, progress=phases.append)
    assert calls[0][2]['revision'] == (None if local else 'main')
    assert all(call[2]['revision'] == (None if local else 'a' * 40) for call in calls[1:])
    assert calls[-1][2]['config'] is config
    assert calls[-1][2]['dtype'] == lm.torch.float32
    assert calls[-1][2]['trust_remote_code'] is False
    assert calls[-1][2]['use_safetensors'] is True
    assert calls[-1][2]['device_map'] == {'': 'cpu'}
    assert attached.info['requested_revision'] == 'main'
    assert attached.info['resolved_revision'] == (lm.local_model_identity(model_id) if local else 'a' * 40)
    assert attached.info['source_type'] == ('local' if local else 'hub')
    assert attached.info['loading']['total_seconds'] >= attached.info['loading']['weights_seconds'] >= 0
    assert cached == ([] if local else [('test/model','a'*40)])
    assert phases[-1] == 'Validating tokenizer and completion markers…'


def test_disabled_autoload_never_requests_a_model(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['autoload'])
    monkeypatch.setattr(autoload, 'load_config', lambda: {})
    monkeypatch.setattr(autoload, 'enabled', lambda: False)
    monkeypatch.setattr(autoload, 'request_json', lambda *a: pytest.fail('Disabled autoload must not call dashboard.'))
    autoload.main()
    assert 'disabled' in capsys.readouterr().out


def test_explicit_generic_model_uses_main_without_personalized_profile():
    assert autoload.load_config({'FORK_MODEL_ID': 'test/model'}) == dict(model_id='test/model', revision='main', device='auto', batch_size=1)


def test_local_source_hash_mismatch_fails_before_weight_load(monkeypatch, tmp_path):
    from fork_microscope import live_model as lm
    (tmp_path / 'config.json').write_text('{}')
    (tmp_path / 'model.safetensors').write_bytes(b'changed-weights')
    monkeypatch.setattr(lm.AutoConfig, 'from_pretrained', lambda *a, **k: pytest.fail('Mismatched local contents must fail before model loading'))
    with pytest.raises(ValueError, match='differ from the saved source identity'):
        lm.AttachedModel(str(tmp_path), 'local-sha256:' + '0' * 64, 'cpu', 1)


def test_weight_download_uses_only_pinned_native_shards_and_reports_bytes(tmp_path,monkeypatch):
    from fork_microscope import live_model as lm
    index=tmp_path/'index.json';index.write_text(json.dumps({'weight_map':{'a':'part-1.safetensors','b':'part-2.safetensors'}}))
    monkeypatch.setattr(lm,'hf_hub_download',lambda *a,**k:str(index))
    events=[]
    def snapshot(model_id,revision,allow_patterns,tqdm_class):
        assert (model_id,revision)==('test/model','a'*40)
        assert allow_patterns==['part-1.safetensors','part-2.safetensors']
        with tqdm_class(total=1024,unit='B',desc='Reconstructing',disable=False) as bar:bar.update(256)
    monkeypatch.setattr(lm,'snapshot_download',snapshot)
    lm.cache_weights('test/model','a'*40,events.append)
    assert events[0]['completed']==256 and events[0]['total']==1024 and events[0]['unit']=='bytes'


def test_disabled_download_bar_keeps_cancellation_without_fake_progress(tmp_path, monkeypatch):
    from fork_microscope import live_model as lm
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'weight_map': {'a': 'part.safetensors'}}))
    monkeypatch.setattr(lm, 'hf_hub_download', lambda *a, **k: str(index))
    events, checks = [], []
    def snapshot(model_id, revision, allow_patterns, tqdm_class):
        with tqdm_class(total=1024, unit='B', disable=True) as bar:
            before = len(checks)
            bar.update(256)
            assert len(checks) == before + 1
    monkeypatch.setattr(lm, 'snapshot_download', snapshot)
    lm.cache_weights('test/model', 'a' * 40, events.append, lambda: checks.append(True))
    assert events == []


def test_weight_download_authentication_error_is_not_treated_as_single_file(monkeypatch):
    from fork_microscope import live_model as lm
    from huggingface_hub.errors import GatedRepoError
    import httpx
    def denied(*a,**k):raise GatedRepoError('Access required',response=httpx.Response(403,request=httpx.Request('GET','https://example.invalid')))
    monkeypatch.setattr(lm,'hf_hub_download',denied)
    monkeypatch.setattr(lm,'snapshot_download',lambda *a,**k:pytest.fail('Must preserve access error'))
    with pytest.raises(GatedRepoError):lm.cache_weights('test/model','a'*40)


def test_generation_telemetry_preserves_samples_and_removes_hook(tmp_path,monkeypatch):
    attached,_=_tiny_qwen_adapter(tmp_path,monkeypatch)
    events=[]
    branch=SimpleNamespace(tok_id=4,prefix_ids=[4,5,6])
    expected=attached.draw_branch(branch,3,5,1.,17,lambda:None)
    attached.activity=events.append
    actual=attached.draw_branch(branch,3,5,1.,17,lambda:None)
    assert actual==expected and events and events[0]['kind']=='generation'
    assert not attached.model._forward_hooks
    def stop():raise RuntimeError('Cancelled')
    with pytest.raises(RuntimeError,match='Cancelled'):
        with attached.decoding_progress(5,stop):attached.model(lm_tensor([4,5,6]))
    assert not attached.model._forward_hooks


def lm_tensor(ids):
    import torch
    return torch.tensor([ids])


def test_autoload_worker_token_is_only_a_request_header(monkeypatch):
    import io
    monkeypatch.setenv('FORK_WORKER_TOKEN', 'private-worker-token')
    observed = []
    def response(req, timeout):
        observed.append(req)
        return io.BytesIO(b'{"ready":true}')
    monkeypatch.setattr(autoload.urllib.request, 'urlopen', response)
    assert autoload.request_json('/api/live/status')['ready']
    assert observed[0].get_header('Authorization') == 'Bearer private-worker-token'
    assert 'private-worker-token' not in observed[0].full_url


# generated: a small real hybrid Qwen wrapper exercises the production local
# loader, teacher forcing and continuation path without downloading model weights.
def _tiny_qwen_adapter(tmp_path, monkeypatch):
    import torch
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import Qwen3_5Config, Qwen3_5ForConditionalGeneration, PreTrainedTokenizerFast
    from fork_microscope import live_model as lm
    config = Qwen3_5Config(
        text_config=dict(vocab_size=32, hidden_size=32, intermediate_size=64,
            num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=1,
            head_dim=16, linear_conv_kernel_dim=4, linear_key_head_dim=8,
            linear_value_head_dim=8, linear_num_key_heads=2, linear_num_value_heads=2,
            max_position_embeddings=128, layer_types=['linear_attention', 'full_attention'],
            eos_token_id=2, pad_token_id=0,
            rope_parameters=dict(rope_type='default', rope_theta=10000.,
                partial_rotary_factor=.5, mrope_section=[1, 1, 2])),
        vision_config=dict(depth=1, hidden_size=16, intermediate_size=32, num_heads=2,
            out_hidden_size=32, num_position_embeddings=16, patch_size=2,
            temporal_patch_size=1, spatial_merge_size=2),
        image_token_id=28, video_token_id=29, vision_start_token_id=30, vision_end_token_id=31)
    vocab = {f't{i}': i for i in range(32)}
    tokenizer = Tokenizer(models.WordLevel(vocab, unk_token='t3'))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=tokenizer, pad_token='t0',
        bos_token='t1', eos_token='t2', unk_token='t3',
        chat_template="{% for message in messages %}{{ message['content'] }}{% endfor %}")
    tokenizer.save_pretrained(tmp_path)
    with torch.random.fork_rng():
        torch.manual_seed(19)
        original = Qwen3_5ForConditionalGeneration(config).eval()
    original.save_pretrained(tmp_path, safe_serialization=True)
    monkeypatch.setattr(lm.AutoModelForCausalLM, 'from_pretrained', lambda *a, **k: pytest.fail('Qwen outer config needs the conditional wrapper'))
    monkeypatch.setattr(lm.AutoProcessor, 'from_pretrained', lambda *a, **k: pytest.fail('Text-only requests must use the tokenizer'))
    attached = lm.AttachedModel(str(tmp_path), 'local', 'cpu', 4)
    return attached, original


# generated: save/load parity and unmodified exact-prefix generation on a native
# conditional wrapper with both recurrent and full-attention decoder blocks.
def test_tiny_qwen_wrapper_loads_and_preserves_exact_generation_prefixes(tmp_path, monkeypatch):
    import torch
    attached, original = _tiny_qwen_adapter(tmp_path, monkeypatch)
    assert attached.info['architecture'] == 'Qwen3_5ForConditionalGeneration'
    assert attached.info['loader'] == 'AutoModelForImageTextToText'
    assert attached.info['input_modalities'] == ['text']
    assert attached.info['context_limit'] == 128
    assert attached.eos_ids == [2]
    prompt = attached.prompt_text('t4 t5 t6', 'chat')
    assert prompt == [4, 5, 6]
    ids = torch.tensor([prompt])
    with torch.no_grad():
        expected = original(ids, use_cache=False).logits
        actual = attached.model(ids, use_cache=False).logits
    torch.testing.assert_close(actual, expected)
    calls = []
    generate = attached.model.generate
    def capture(input_ids, **kwargs):
        calls.append((input_ids.clone(), kwargs))
        return generate(input_ids, **kwargs)
    monkeypatch.setattr(attached.model, 'generate', capture)
    base = attached.base_path(prompt, max_tokens=3, top_k=4)
    assert base.prompt_ids == prompt
    assert len(base.gen_ids) == len(base.topk_ids) == len(base.topk_logprobs)
    assert calls[0][0].tolist() == [prompt]
    assert base.topk_ids[0][0] == int(expected[0, -1].argmax())
    prefixes = [prompt, prompt + [7]]
    samples, _ = attached.resample(prefixes, n=2, max_tokens=3, temperature=1., seed=17)
    assert calls[1][0].tolist() == [[0, 4, 5, 6], [4, 5, 6, 7]]
    assert calls[1][1]['attention_mask'].tolist() == [[0, 1, 1, 1], [1, 1, 1, 1]]
    assert [len(group) for group in samples] == [2, 2]
    assert all(len(ids) <= 3 for group in samples for ids in group)
    assert all(0 <= token < 32 and token != 2 for group in samples for ids in group for token in ids)


# generated: full lens pipeline on the real nested Qwen hybrid decoder, using a
# local synthetic lens rather than any downloaded model or production lens.
def test_tiny_qwen_lens_matches_independent_transport_without_mutation(tmp_path, monkeypatch):
    import copy
    import hashlib
    import torch
    jlens = pytest.importorskip('jlens')
    from fork_microscope import lens_integration as li
    model_path = tmp_path / 'model'; model_path.mkdir()
    attached, _ = _tiny_qwen_adapter(model_path, monkeypatch)
    base = attached.base_path([4, 5, 6], max_tokens=3, top_k=4)
    assert base.gen_ids
    width = attached.model.config.text_config.hidden_size
    matrix = torch.eye(width); matrix[0, 1] = 3.; matrix[4, 7] = -2.
    path = tmp_path / 'tiny-lens.pt'
    jlens.JacobianLens({0: matrix, 1: torch.eye(width)}, n_prompts=10, d_model=width).save(str(path))
    Path(str(path) + '.json').write_text(json.dumps(dict(
        model_id=attached.info['model_id'], resolved_revision=attached.info['resolved_revision'],
        lens_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), site=li.SITE, target_layer=1)))
    result = dict(id='a' * 32, model=dict(attached.info), passes=[{'id': 'p'}],
        records={'p': {'base': dict(prompt_ids=base.prompt_ids, gen_ids=base.gen_ids,
            base_text=attached.decode(base.gen_ids))}})
    request = dict(source_run_id=result['id'], source_pass_id='p',
        lens={'profile': 'local', 'path': str(path)}, selection={'type': 'original'},
        space='response', start=0, end=len(base.gen_ids)-1, layers=[0, 1], top_k=8)
    plan = li.build_plan(attached, result, request, lambda _: None)
    plan['id'] = 'b' * 32
    full_ids = torch.tensor([base.prompt_ids + base.gen_ids])
    decoder = attached.model.model.language_model
    captured = []
    hook = decoder.layers[0].register_forward_hook(lambda m, args, output: captured.append(output.detach().clone()))
    with torch.no_grad():
        before = attached.model(full_ids, use_cache=False).logits.clone()
    hook.remove()
    before_tokenizer = attached.tokenizer.backend_tokenizer.to_str()
    before_template = attached.tokenizer.chat_template
    before_generation = copy.deepcopy(attached.model.generation_config.to_dict())
    before_flags = [p.requires_grad for p in attached.model.parameters()]
    before_training = attached.model.training
    seen = []
    hook = attached.model.register_forward_pre_hook(lambda m, args: seen.append(args[0].tolist()))
    try:
        out = li.run(attached, plan, lambda: None, lambda *a: None, lambda _: None)
    finally:
        hook.remove()
    assert seen == [full_ids.tolist()]
    assert out['status'] == 'complete'
    assert out['parity'][0]['max_absolute_error'] < 1e-5
    assert len(out['cells']) == 2 * len(base.gen_ids)
    for cell in out['cells']:
        pos = cell['absolute_position']
        assert pos == len(base.prompt_ids) + cell['index']
        assert cell['token_id'] == base.gen_ids[cell['index']]
        if cell['layer'] == 0:
            with torch.no_grad():
                # Independent matrix-vector multiplication catches transport transposes.
                transformed = matrix @ captured[0][0, pos]
                expected = attached.model.lm_head(decoder.norm(transformed))
                scores, token_ids = expected.topk(request['top_k'])
            assert [t['token_id'] for t in cell['tokens']] == token_ids.tolist()
            torch.testing.assert_close(torch.tensor([t['score'] for t in cell['tokens']]), scores)
        else:
            assert cell['tokens'] == cell['logit_lens_tokens'] == cell['model_tokens']
    with torch.no_grad():
        torch.testing.assert_close(attached.model(full_ids, use_cache=False).logits, before, rtol=0, atol=0)
    assert attached.tokenizer.backend_tokenizer.to_str() == before_tokenizer
    assert attached.tokenizer.chat_template == before_template
    assert attached.model.generation_config.to_dict() == before_generation
    assert [p.requires_grad for p in attached.model.parameters()] == before_flags
    assert attached.model.training == before_training
    assert all(not layer._forward_hooks for layer in decoder.layers)
