import pytest
from datetime import datetime, timezone
from fork_microscope.hosted.store import MemoryStore
from fork_microscope.hosted.service import HostedError
from pilot_budget import PilotBudget, COLLECTION, RECONCILIATION_MAX_AGE, READ_BYTES_LIMIT, REQUEST_LIMIT, RESERVE_LIMIT

def setup():
    store=MemoryStore();now=[1791504000.]
    budget=PilotBudget(store,pilot_uid='alice',clock=lambda:now[0])
    month=datetime.fromtimestamp(now[0],timezone.utc).strftime('%Y-%m')
    store.transaction(lambda tx:tx.set(COLLECTION,'pilot_budget_control',dict(enabled=True,
        observed_at=now[0],estimated_total_usd=1.2,month=month)))
    return store,now,budget

def test_default_and_stale_control_fail_closed_without_blocking_recovery():
    store,now,budget=setup()
    now[0]+=RECONCILIATION_MAX_AGE+1
    with pytest.raises(HostedError):budget.admit(kind='oauth')
    budget.admit(kind='recovery')
    blank=PilotBudget(MemoryStore(),pilot_uid='alice',clock=lambda:now[0])
    with pytest.raises(HostedError):blank.admit(kind='compute')
    blank.admit(kind='evidence_read',byte_count=100)

def test_monthly_transfer_quota_atomic_and_one_account():
    store,now,budget=setup()
    budget.admit(kind='evidence_read',byte_count=READ_BYTES_LIMIT)
    with pytest.raises(HostedError):budget.admit(kind='evidence_read',byte_count=1)
    with pytest.raises(HostedError):budget.check_owner('bob')
    budget.check_owner('alice')

def test_stop_target_and_separate_recovery_reserve():
    store,now,budget=setup()
    def stop(tx):
        c=tx.get(COLLECTION,'pilot_budget_control');c['estimated_total_usd']=20
        tx.set(COLLECTION,'pilot_budget_control',c)
    store.transaction(stop)
    with pytest.raises(HostedError):budget.admit(kind='request')
    budget.admit(kind='recovery')

def test_per_minute_limit():
    store,now,budget=setup()
    for _ in range(20):budget.admit(kind='request')
    with pytest.raises(HostedError):budget.admit(kind='request')
    now[0]+=60
    budget.admit(kind='request')


def test_normal_allowance_exhaustion_preserves_bounded_recovery_reserve():
    store,now,budget=setup()
    budget.admit(kind='request')
    key=next(k for k in store._data[COLLECTION] if k.startswith('budget_'))
    def exhausted(tx):
        row=tx.get(COLLECTION,key);row['requests']=REQUEST_LIMIT
        tx.set(COLLECTION,key,row)
    store.transaction(exhausted)
    with pytest.raises(HostedError):budget.admit(kind='compute')
    with budget.operation('recovery'):
        budget.admit(kind='recovery')
        budget.admit(kind='evidence_read',byte_count=42)
    row=store.transaction(lambda tx:tx.get(COLLECTION,key))
    assert row['requests']==REQUEST_LIMIT and row['recovery_requests']==1 and row['read_bytes']==42
    def reserve_spent(tx):
        row=tx.get(COLLECTION,key);row['recovery_requests']=RESERVE_LIMIT
        tx.set(COLLECTION,key,row)
    store.transaction(reserve_spent)
    with pytest.raises(HostedError):budget.admit(kind='recovery')


@pytest.mark.parametrize('field,bad', [('observed_at', 'future'), ('observed_at', True), ('month', '1999-01'),
    ('enabled', 1), ('estimated_total_usd', True), ('estimated_total_usd', float('nan')), ('estimated_total_usd', -1)])
def test_malformed_or_untrusted_control_fails_closed_but_keeps_reserve(field, bad):
    store,now,budget=setup()
    def corrupt(tx):
        c=tx.get(COLLECTION,'pilot_budget_control');c[field]=now[0]+60 if bad=='future' else bad
        tx.set(COLLECTION,'pilot_budget_control',c)
    store.transaction(corrupt)
    for kind in ('compute','oauth','request'):
        with pytest.raises(HostedError) as error:budget.admit(kind=kind)
        assert error.value.status==503
    with pytest.raises(HostedError):budget.admit(kind='evidence_save',byte_count=1)
    budget.admit(kind='recovery');budget.admit(kind='evidence_read',byte_count=1)
    row=next(v for k,v in store._data[COLLECTION].items() if k.startswith('budget_'))
    assert row['requests']==0 and row['recovery_requests']==2 and row['upload_bytes']==0


def test_stale_service_boundary_blocks_compute_but_allows_cancel_and_cleanup():
    from pilot_budget import BudgetedService
    store,now,budget=setup()
    calls=[]
    class Fake:
        def public(self,uid,operation,*args,**kwargs):calls.append(operation);return {}
    service=BudgetedService(Fake(),budget)
    now[0]+=RECONCILIATION_MAX_AGE+1
    for operation in ('create_session','start_job'):
        with pytest.raises(HostedError):service.public('alice',operation)
    for operation in ('session','get_job','cancel','terminate','artifacts','download'):
        service.public('alice',operation)
    assert calls==['session','get_job','cancel','terminate','artifacts','download']
    with pytest.raises(HostedError):service.public('bob','cancel')


def test_operation_scope_charges_once_and_keeps_byte_charges():
    store,now,budget=setup()
    with budget.operation('recovery'):
        for _ in range(8):budget.admit(kind='recovery')
        budget.admit(kind='evidence_read',byte_count=42)
    row=next(v for k,v in store._data[COLLECTION].items() if k.startswith('budget_'))
    assert row['requests']==1 and row['read_bytes']==42 and row['minute_requests']==1
    budget.admit(kind='recovery')
    row=next(v for k,v in store._data[COLLECTION].items() if k.startswith('budget_'))
    assert row['requests']==2
