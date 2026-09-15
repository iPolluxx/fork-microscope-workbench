"""CPU random-model checks; no pretrained weights, remote calls or paid compute."""
import copy
import importlib.util
import pytest
import torch
from fork_microscope import lens_integration as li
from fork_microscope import nnsight_inspection as ni
from test_lens_integration import setup, plan_run
from test_investigation import fixture
from test_workflow import config, archive, FakeService, finished
from fork_microscope.investigation_workflow import WorkflowManager, validate_config, lens_choice


def test_optional_availability_and_invalid_backend(monkeypatch, tmp_path):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None if name == 'nnsight' else real(name))
    assert ni.availability()['available'] is False
    assert 'requirements/nnsight.txt' in ni.availability()['reason']
    a, _, r, q = setup(tmp_path)
    assert [b['id'] for b in li.options(a)['inspection_backends']] == ['native', 'nnsight']
    q['inspection_backend'] = 'nnsight'
    with pytest.raises(ValueError, match='optional package'):
        li.build_plan(a, r, q, lambda _: None)
    q['inspection_backend'] = 'remote'
    with pytest.raises(ValueError, match='standard or NNsight'):
        li.build_plan(a, r, q, lambda _: None)
    q['inspection_backend'] = 'native'
    plan = li.build_plan(a, r, q, lambda _: None)
    assert plan['inspection']['effective_backend'] == 'native'


def test_pinned_version_guard(monkeypatch):
    monkeypatch.setattr(importlib.util, 'find_spec', lambda _: object())
    monkeypatch.setattr(importlib.metadata, 'version', lambda _: '0.0.0')
    assert not ni.availability()['available']
    assert 'tested capture API' in ni.availability()['reason']


def test_temporary_wrapper_restores_existing_hooks_on_constructor_failure():
    a, *_ = fixture()
    first = a.model.model.layers[0]
    marker = lambda module, args, output: output
    handle = first.register_forward_hook(marker)
    original = {id(m): dict(m.__dict__) for m in a.model.modules()}
    def broken(model):
        first.forward = lambda *args: None
        first.__nnsight_forward__ = object()
        first.__path__ = 'changed'
        first.register_forward_hook(lambda m, a, out: out)
        raise RuntimeError('wrapper failed')
    with pytest.raises(RuntimeError, match='wrapper failed'):
        with ni.temporary_wrapper(a.model, broken):
            pass
    assert list(first._forward_hooks.values()) == [marker]
    for m in a.model.modules():
        for key in ni._ATTRS:
            assert (key in m.__dict__) == (key in original[id(m)])
            if key in m.__dict__:
                assert m.__dict__[key] is original[id(m)][key]
    handle.remove()


def _runtime():
    if not ni.availability()['available']:
        pytest.skip('Optional NNsight 0.7.0 runtime unavailable; this is not a mocked compatibility test.')


def test_real_nnsight_exact_rows_native_parity_and_cleanup(tmp_path):
    _runtime()
    a, _, r, q = setup(tmp_path)
    before = {id(m): (dict(m._forward_hooks), dict(m.__dict__)) for m in a.model.modules()}
    flags = [p.requires_grad for p in a.model.parameters()]
    weights = [p.detach().clone() for p in a.model.parameters()]
    _, native, _ = plan_run(a, r, q)
    q['inspection_backend'] = 'nnsight'
    plan, traced, _ = plan_run(a, r, q)
    assert traced['cells'] == native['cells']
    assert traced['inspection']['package_version'] == ni.TESTED_VERSION
    assert traced['inspection']['effective_backend'] == 'nnsight'
    assert traced['execution']['forward_passes'] == 1  # cache cannot falsely certify a different backend
    assert traced['arms'][0]['input_ids'] == [1,2,3,4,5]
    assert traced['parity'][0]['max_absolute_error'] < 1e-5
    _, cached, _ = plan_run(a, r, q)
    assert cached['execution']['forward_passes'] == 0
    assert cached['cells'] == traced['cells']
    assert flags == [p.requires_grad for p in a.model.parameters()]
    assert all(torch.equal(old, p) for old, p in zip(weights, a.model.parameters()))
    for m in a.model.modules():
        old_hooks, old_attrs = before[id(m)]
        assert dict(m._forward_hooks) == old_hooks
        for key in ni._ATTRS:
            assert (key in m.__dict__) == (key in old_attrs)
    assert a.model.training is False


def test_real_nnsight_failure_and_cancellation_leave_model_usable():
    _runtime()
    a, *_ = fixture()
    layers, width, _ = li.components(a)
    ids = torch.tensor([[1,2,3]])
    with pytest.raises(Exception, match='residual shape'):
        ni.capture(a, layers, ids, {0:[1]}, width+1, {}, lambda:None)
    assert all(not m._forward_hooks and not hasattr(m, '__nnsight_forward__') for m in a.model.modules())
    count = 0
    def cancel():
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        ni.capture(a, layers, ids, {0:[1]}, width, {}, cancel)
    assert all(not m._forward_hooks and not hasattr(m, '__nnsight_forward__') for m in a.model.modules())
    with torch.inference_mode():
        assert a.model(ids).logits.shape == (1,3,32)


def test_real_nnsight_tuple_output_capture():
    _runtime()
    # Synthetic tuple-returning block: same HF convention, no vLLM tuple summing.
    class TupleBlock(torch.nn.Module):
        def forward(self, x):
            return x + 1, None
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embedding = torch.nn.Embedding(8,4)
            self.layers = torch.nn.ModuleList([TupleBlock()])
            self.head = torch.nn.Linear(4,8)
        def forward(self, ids, **kw):
            from types import SimpleNamespace
            hidden = self.layers[0](self.embedding(ids))[0]
            return SimpleNamespace(logits=self.head(hidden))
    from types import SimpleNamespace
    model = Model().eval()
    adapter = SimpleNamespace(model=model)
    ids = torch.tensor([[1,2]])
    captured, final, logits = ni.capture(adapter, model.layers, ids, {0:[0,1]}, 4, {}, lambda:None)
    torch.testing.assert_close(captured[0], model.embedding(ids)[0]+1)
    assert final.shape == (1,4) and logits.shape == (8,)


def test_workflow_optional_backend_syntax_and_forwarding(config):
    config['lens'] = dict(profile='muse_glimmer',layers=[12],before=1,after=2,top_k=5)
    old = validate_config(config)
    assert 'inspection_backend' not in old['lens']  # preserve retry identity for old clients
    for backend in ('native','nnsight'):
        config['lens']['inspection_backend'] = backend
        assert validate_config(config)['lens']['inspection_backend'] == backend
    config['lens']['inspection_backend'] = 'ndif'
    with pytest.raises(ValueError, match='inspection_backend'):
        validate_config(config)
    config['lens']['inspection_backend'] = 'nnsight'
    run = {'id':'a'*32, 'passes':[{'id':'p'}], 'records':{'p':{
        'base':{'gen_ids':[1,2,3]}, 'branches':[
            {'t':1,'tok_id':4,'draw_indices':[0], 'continuation_ids':[[5]],
             'observations':[{'stop_reason':'eos','label':'A'}]},
            {'t':1,'tok_id':6,'draw_indices':[1], 'continuation_ids':[[7]],
             'observations':[{'stop_reason':'eos','label':'B'}]},
        ]}}}
    request, why = lens_choice(run, config['lens'])
    assert request['inspection_backend'] == 'nnsight'
    assert request['selection']['draw_indices'] == [0,1]
    assert why['first_difference'] == 1


def test_workflow_missing_nnsight_stops_before_any_compute(tmp_path, config, archive, monkeypatch):
    config['lens'] = dict(profile='muse_glimmer',layers=[12],before=1,after=2,top_k=5,
                          inspection_backend='nnsight')
    checked = []
    def unavailable(adapter):
        checked.append(adapter)
        raise ValueError('Install the optional NNsight package on this worker.')
    monkeypatch.setattr(ni, 'require', unavailable)
    service = FakeService(archive)
    manager = WorkflowManager(service, tmp_path)
    job = manager.start({'request_id':'missing-optional', 'config':config})
    result = finished(manager, job['id'])
    assert checked == [None]
    assert service.calls == []
    assert result['status'] == 'error' and 'optional NNsight' in result['message']
    assert result['reservations'] == {'samples':0,'generated_tokens':0}


def test_workflow_nnsight_checks_loaded_model_before_scan(tmp_path, config, archive, monkeypatch):
    config['lens'] = dict(profile='muse_glimmer',layers=[12],before=1,after=2,top_k=5,
                          inspection_backend='nnsight')
    checked = []
    def unsupported(adapter):
        checked.append(adapter)
        if adapter is not None:
            raise ValueError('This attached model cannot use the optional capture backend.')
    monkeypatch.setattr(ni, 'require', unsupported)
    service = FakeService(archive)
    manager = WorkflowManager(service, tmp_path)
    result = finished(manager, manager.start({'request_id':'unsupported-optional', 'config':config})['id'])
    assert checked == [None, service.model]
    assert service.calls == ['load']
    assert result['status'] == 'error'
    assert result['reservations'] == {'samples':0,'generated_tokens':0}
