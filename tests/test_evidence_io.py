"""Evidence portability, integrity checks, and mutation boundaries."""
import copy
import json
import pytest

from fork_microscope.evidence_io import import_export
from fork_microscope.live_service import LiveService


@pytest.fixture
def archive():
    from test_sampling import mixture_record, CONFIG
    raw = mixture_record()
    raw['base'].update(gen_ids=list(range(13)), prompt_ids=[42], base_text='saved trace', token_texts=['x'] * 13)
    for branch in raw['branches']:
        branch['continuation_ids'] = [[1] for _ in branch['answers']]
        for label, obs in zip(branch['answers'], branch['observations']):
            obs['label'] = label
    settings = copy.deepcopy(CONFIG)
    settings['passes'][0]['end'] = 12
    return dict(id='a' * 32, schema_version=2, sampling_design='position_mixture_v1',
                model={'model_id': 'test/model', 'resolved_revision': 'test-revision'},
                created=1.0, base_config={'prompt': 'test', 'answers': ['A', 'B', 'C', 'D'], 'mode': 'chat', 'max_tokens': 13, 'seed': 0}, settings=settings,
                base={'length': 13, 'text': 'saved trace', 'tokens': ['x'] * 13},
                categories=raw['categories'], records={'pass_1': raw},
                passes=[{'id': 'pass_1', 'curve': {'positions': [0, 4, 8, 12],
                    'weighted': [[.6, .4, 0, 0, 0]] * 4}}])


def test_import_roundtrip_keeps_trace_and_records_and_is_idempotent(tmp_path, monkeypatch, archive):
    from fork_microscope import live_service
    monkeypatch.setattr(live_service, 'RUNS', tmp_path)
    before = copy.deepcopy(archive)
    service = LiveService()
    assert service.import_result(archive) == {'id': archive['id'], 'already_present': False}
    restored = service.result(archive['id'], raw=True)
    assert restored['records'].pop('import-info')['fit_recomputed'] is False
    assert restored == archive == before
    assert service.results()[0]['id'] == archive['id']
    assert service.import_result(archive)['already_present']
    assert service.job['status'] == 'idle' and service.model is None


def test_collision_never_overwrites_existing_evidence(tmp_path, archive):
    import_export(archive, tmp_path)
    original = (tmp_path / archive['id'] / 'result.json').read_bytes()
    archive['created'] = 2
    with pytest.raises(ValueError, match='already exists'):
        import_export(archive, tmp_path)
    assert (tmp_path / archive['id'] / 'result.json').read_bytes() == original


@pytest.mark.parametrize('name', ['../escape', 'manifest', 'result', 'dense', 'replay-verification'])
def test_import_cannot_choose_paths_or_overwrite_metadata(tmp_path, archive, name):
    archive['passes'][0]['id'] = name
    archive['records'][name] = archive['records'].pop('pass_1')
    with pytest.raises(ValueError):
        import_export(archive, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('change', ['graph', 'raw', 'base', 'nonfinite', 'missing', 'observation', 'settings', 'token_text'])
def test_import_rejects_broken_graph_or_evidence_before_any_write(tmp_path, archive, change):
    if change == 'graph': archive['passes'][0]['curve']['weighted'][0] = [.4, .6, 0, 0, 0]
    if change == 'raw': archive['records']['pass_1']['branches'][0]['draw_indices'][0] = 2
    if change == 'base': archive['base']['text'] = 'different trace'
    if change == 'nonfinite': archive['settings']['threshold'] = float('nan')
    if change == 'missing': archive.pop('records')
    if change == 'observation': archive['records']['pass_1']['branches'][0]['observations'][0]['label'] = 'Other'
    if change == 'settings': archive['settings'] = {}
    if change == 'token_text': archive['base']['tokens'][0] = {'unexpected': 'object'}
    with pytest.raises(ValueError):
        import_export(archive, tmp_path)
    assert not list(tmp_path.iterdir())


def test_broken_archive_does_not_hide_other_completed_runs(tmp_path, monkeypatch, archive):
    from fork_microscope import live_service
    monkeypatch.setattr(live_service, 'RUNS', tmp_path)
    import_export(archive, tmp_path)
    broken = tmp_path / ('b' * 32)
    broken.mkdir()
    (broken / 'result.json').write_text('{')
    service = LiveService()
    assert len(service.results()) == 1
    with pytest.raises(ValueError, match='could not be read'):
        service.result('b' * 32)


def test_stale_refinement_cannot_stop_a_different_job():
    service = LiveService()
    service.job = {'id': 'new-job', 'status': 'running'}
    with pytest.raises(ValueError, match='No other job was stopped'):
        service.cancel('old-job')
    assert not service.stop.is_set()
    assert service.cancel('new-job')['stopping'] and service.stop.is_set()


def test_hardware_guard_rejects_cuda_and_preflight_memory_before_loading(monkeypatch):
    from fork_microscope import model_preflight
    service=LiveService()
    service._runtime={'cuda_available':False,'gpu_name':None,'system_memory_gb':16}
    payload=dict(model_id='any-native-model',revision='main',device='cuda',batch_size=1)
    with pytest.raises(ValueError,match='No CUDA GPU'):service.start('load',payload)
    monkeypatch.setattr(model_preflight,'preflight_model',lambda p,r:dict(can_load=False,blockers=['Weights exceed worker memory.']))
    service._execute('load',payload|{'device':'auto'})
    assert service.job['status']=='error' and service.model is None
    assert 'Weights exceed' in service.job['phase']


def test_http_import_is_same_origin_and_starts_no_model(tmp_path, monkeypatch, archive):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from fork_microscope import live_service
    from fork_microscope import microscope_server
    monkeypatch.setattr(live_service, 'RUNS', tmp_path)
    service = LiveService()
    monkeypatch.setattr(microscope_server, 'LIVE', service)
    server = ThreadingHTTPServer(('127.0.0.1', 0), microscope_server.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/api/live/import'
    body = json.dumps(archive).encode()
    try:
        bad = Request(url, data=body, headers={'Content-Type': 'application/json', 'Origin': 'https://elsewhere.example'})
        with pytest.raises(HTTPError) as error:
            urlopen(bad, timeout=10)
        assert error.value.code == 401 and not list(tmp_path.iterdir())
        good = Request(url, data=body, headers={'Content-Type': 'application/json'})
        with urlopen(good, timeout=10) as response:
            assert response.status == 200 and json.load(response)['id'] == archive['id']
        assert service.model is None and service.job['status'] == 'idle'
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
