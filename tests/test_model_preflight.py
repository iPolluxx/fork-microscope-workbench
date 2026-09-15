"""Metadata-only eligibility, memory blockers and content-based local identities."""
from types import SimpleNamespace
import pytest
from fork_microscope import model_preflight as mp


def request(**kw):
    return dict(model_id='test/model', revision='main', device='auto', batch_size=1, **kw)


@pytest.fixture
def metadata(monkeypatch):
    import transformers
    from huggingface_hub import HfApi
    config = SimpleNamespace(model_type='llama', _commit_hash='a' * 40, max_position_embeddings=4096)
    tokenizer = SimpleNamespace(chat_template='chat', eos_token_id=2, pad_token_id=0)
    calls = []
    def config_load(model_id, **kwargs):
        calls.append(('config', model_id, kwargs)); return config
    def tokenizer_load(model_id, **kwargs):
        calls.append(('tokenizer', model_id, kwargs)); return tokenizer
    monkeypatch.setattr(transformers.AutoConfig, 'from_pretrained', config_load)
    monkeypatch.setattr(transformers.AutoTokenizer, 'from_pretrained', tokenizer_load)
    monkeypatch.setattr(transformers.AutoModelForCausalLM, 'from_pretrained', lambda *a, **k: pytest.fail('Preflight must not load weights'))
    monkeypatch.setattr(mp, 'native_loader', lambda c: 'AutoModelForCausalLM')
    monkeypatch.setattr(HfApi, 'model_info', lambda *a, **k: SimpleNamespace(safetensors=SimpleNamespace(total=1_000_000_000)))
    return config, tokenizer, calls


def test_preflight_uses_pinned_metadata_without_weights(metadata):
    report = mp.preflight_model(request(), dict(cuda_available=True, gpu_memory_gb=24))
    assert report['can_load'] and report['loader'] == 'AutoModelForCausalLM'
    assert report['parameters'] == 1_000_000_000
    assert report['memory']['weights_gb'] == 1.86
    assert report['verification'] == 'metadata_only'
    assert metadata[2][1][2]['revision'] == 'a' * 40
    assert all(call[2]['trust_remote_code'] is False for call in metadata[2])


def test_no_cuda_and_insufficient_weights_block(metadata):
    payload = request(); payload['device'] = 'cuda'
    report = mp.preflight_model(payload, dict(cuda_available=False))
    assert not report['can_load'] and 'no CUDA' in report['blockers'][0]
    payload['device'] = 'cpu'
    report = mp.preflight_model(payload, dict(cuda_available=False, system_memory_gb=2))
    assert not report['can_load']
    assert any('Weights alone' in text for text in report['blockers'])


def test_template_absence_allows_completion_not_chat(metadata):
    metadata[1].chat_template = None
    report = mp.preflight_model(request(), dict(cuda_available=True))
    assert report['can_load'] and not report['capabilities']['chat']
    assert any('Base / completion' in text for text in report['warnings'])


def test_quantization_and_unsupported_architecture_fail(metadata, monkeypatch):
    metadata[0].quantization_config = {'quant_method': 'awq'}
    monkeypatch.setattr(mp, 'native_loader', lambda config: None)
    report = mp.preflight_model(request(), dict(cuda_available=True))
    assert not report['can_load']
    assert any('Pre-quantized' in text for text in report['blockers'])
    assert any('architecture' in text for text in report['blockers'])


def test_private_access_failure_does_not_echo_exception(metadata, monkeypatch):
    from transformers import AutoConfig
    def fail(*a, **kw):
        raise RuntimeError('request contained secret-token')
    monkeypatch.setattr(AutoConfig, 'from_pretrained', fail)
    report = mp.preflight_model(request(), dict(cuda_available=True))
    assert not report['can_load']
    assert 'secret-token' not in str(report)
    assert 'worker-side' in report['blockers'][0]


def test_missing_commit_cannot_claim_pinned_identity(metadata):
    metadata[0]._commit_hash = None
    report = mp.preflight_model(request(), dict(cuda_available=True))
    assert not report['can_load']
    assert any('immutable commit' in text for text in report['blockers'])


def model_files(tmp_path):
    (tmp_path / 'config.json').write_text('{"model_type":"llama"}')
    (tmp_path / 'tokenizer.json').write_text('{"vocab":{}}')
    (tmp_path / 'model.safetensors').write_bytes(b'model-contents')
    return tmp_path


def test_local_identity_tracks_contents_not_mtime_or_path(tmp_path):
    folder = model_files(tmp_path)
    first = mp.local_model_identity(folder)
    (folder / 'model.safetensors').touch()
    assert mp.local_model_identity(folder) == first
    (folder / 'tokenizer.json').write_text('{"vocab":{"changed":1}}')
    assert mp.local_model_identity(folder) != first
    (folder / 'model.safetensors').write_bytes(b'MODEL-CONTENTS')
    assert mp.local_model_identity(folder) != first


def test_local_preflight_no_weight_hash_required(metadata, tmp_path, monkeypatch):
    model_files(tmp_path)
    monkeypatch.setattr(mp, 'local_model_identity', lambda *a, **k: pytest.fail('Inspect should not read all weight bytes'))
    payload = request(); payload['model_id'] = str(tmp_path); payload['revision'] = 'local'
    report = mp.preflight_model(payload, dict(cuda_available=True))
    assert report['can_load'] and report['source_type'] == 'local'
    assert report['resolved_revision'] is None
    assert metadata[2][0][2]['revision'] is None
    assert metadata[2][1][2]['revision'] is None


def test_missing_local_or_chat_url_is_rejected(tmp_path):
    for model_id in [str(tmp_path / 'missing'), 'https://api.example.com/v1', 'http://localhost:11434']:
        with pytest.raises(ValueError):
            mp.model_location(model_id)
    with pytest.raises(ValueError, match='safetensors'):
        mp.local_model_identity(tmp_path)


def test_input_bounds_are_enforced():
    for key, value in [('batch_size', True), ('batch_size', 129), ('device', 'cuda:12'), ('revision', '')]:
        payload = request(); payload[key] = value
        with pytest.raises(ValueError):
            mp.preflight_model(payload, {})
    payload = request(); payload['token'] = 'not-allowed'
    with pytest.raises(ValueError):
        mp.preflight_model(payload, {})


def test_local_identity_portability_requires_complete_matching_hash():
    left = dict(model_id='/old/model', resolved_revision='local-sha256:'+'a'*64, source_type='local')
    right = dict(left, model_id='/new/mounted-model')
    assert mp.same_model_identity(left, right)
    assert not mp.same_model_identity(left, dict(right, resolved_revision='local-sha256:'+'b'*64))
    assert not mp.same_model_identity(left, dict(right, resolved_revision='local-sha256:abc'))
    assert not mp.same_model_identity(left, dict(right, source_type='hub'))
    assert not mp.same_model_identity(dict(left, resolved_revision='main'), dict(right, resolved_revision='main'))
    assert not mp.same_model_identity(dict(left, resolved_revision=None), dict(right, resolved_revision=None))


def test_hub_identity_still_requires_both_repository_and_revision():
    left = dict(model_id='organization/model', resolved_revision='a'*40, source_type='hub')
    assert mp.same_model_identity(left, dict(left))
    assert not mp.same_model_identity(left, dict(left, model_id='different/model'))
    assert not mp.same_model_identity(left, dict(left, resolved_revision='b'*40))
    assert not mp.same_model_identity({}, {})


def test_local_identity_includes_external_chat_templates(tmp_path):
    model_files(tmp_path)
    (tmp_path/'chat_template.jinja').write_text('{{ messages }}')
    original=mp.local_model_identity(tmp_path)
    (tmp_path/'chat_template.jinja').write_text('changed prompt template')
    assert mp.local_model_identity(tmp_path)!=original


# generated: outer Qwen configs occur in both HF mappings; only the wrapper
# accepts the nested text_config without altering saved checkpoint structure.
def test_native_qwen_wrapper_and_text_only_loader_selection():
    from transformers import Qwen3_5Config, Qwen3_5TextConfig, LlamaConfig
    assert mp.native_loader(Qwen3_5Config()) == 'AutoModelForImageTextToText'
    assert mp.native_loader(Qwen3_5TextConfig()) == 'AutoModelForCausalLM'
    assert mp.native_loader(LlamaConfig()) == 'AutoModelForCausalLM'
    assert mp.native_loader(SimpleNamespace(model_type='unsupported')) is None


# generated: verify both released lens demo IDs using their shared native
# architecture without fetching tokenizer assets, model weights or Hub metadata.
@pytest.mark.parametrize('model_id', ['Qwen/Qwen3.5-4B', 'Qwen/Qwen3.6-27B'])
def test_qwen_preflight_uses_text_tokenizer_with_conditional_wrapper(monkeypatch, model_id):
    import transformers
    from huggingface_hub import HfApi
    config = transformers.Qwen3_5Config(text_config={'max_position_embeddings': 262144})
    config._commit_hash = 'b' * 40
    calls = []
    monkeypatch.setattr(transformers.AutoConfig, 'from_pretrained', lambda *a, **k: config)
    def tokenizer_load(source, **kwargs):
        calls.append((source, kwargs))
        return SimpleNamespace(chat_template='chat', eos_token_id=2, pad_token_id=0)
    monkeypatch.setattr(transformers.AutoTokenizer, 'from_pretrained', tokenizer_load)
    monkeypatch.setattr(transformers.AutoProcessor, 'from_pretrained', lambda *a, **k: pytest.fail('Text input does not need an image processor'))
    for loader in (transformers.AutoModelForCausalLM, transformers.AutoModelForImageTextToText):
        monkeypatch.setattr(loader, 'from_pretrained', lambda *a, **k: pytest.fail('Preflight must not load weights'))
    monkeypatch.setattr(HfApi, 'model_info', lambda *a, **k: SimpleNamespace(safetensors=None))
    payload = request(); payload['model_id'] = model_id
    report = mp.preflight_model(payload, {'cuda_available': True})
    assert report['can_load']
    assert report['loader'] == 'AutoModelForImageTextToText'
    assert report['context_limit'] == 262144
    assert calls == [(model_id, {'revision': 'b' * 40, 'trust_remote_code': False})]
    assert any('text input only' in warning for warning in report['warnings'])
