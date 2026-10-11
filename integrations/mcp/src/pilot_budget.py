"""Conservative one-account admissions, not a GCP invoice or spending hard cap.

The existing Scheduler/controller cleanup path never calls this limiter. The
operator must reconcile platform usage; unavailable/stale reconciliation prevents
new work while leaving a small separate retrieval/status/cleanup reserve.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
import math

from fork_microscope.hosted.service import HostedError

COLLECTION = 'mcp_oauth_limits'
REQUEST_LIMIT = 750
RESERVE_LIMIT = 250
READ_BYTES_LIMIT = 4 * 1024 ** 3
MONTHLY_UPLOAD_LIMIT = 1024 ** 3
STOP_USD = 20
RECONCILIATION_MAX_AGE = 86400
_operation_scope = ContextVar('fork_pilot_budget_operation',default=None)


class PilotBudget:
    def __init__(self, store, *, pilot_uid, clock):
        if not pilot_uid:
            raise ValueError('Exact pilot account is required')
        self.store, self.uid, self.clock = store, pilot_uid, clock

    def admit(self, *, kind, byte_count=0, tx=None):
        if kind not in {'request', 'oauth', 'compute', 'evidence_save', 'evidence_read', 'recovery'}:
            raise ValueError('Unknown pilot admission')
        if type(byte_count) is not int or byte_count < 0:
            raise ValueError('Invalid pilot size')
        nested = _operation_scope.get() is self
        if nested and kind not in {'evidence_read','evidence_save'}:
            return
        now = self.clock()
        month = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m')
        key = 'budget_' + month
        def change(transaction):
            control = transaction.get(COLLECTION, 'pilot_budget_control') or {}
            count = transaction.get(COLLECTION, key) or {
                'requests':0, 'recovery_requests':0, 'read_bytes':0, 'upload_bytes':0,
                'minute':-1, 'minute_requests':0, 'owner_uid':self.uid}
            if count['owner_uid'] != self.uid:
                raise HostedError(503, 'Pilot budget ownership changed')
            observed = control.get('observed_at')
            spend = control.get('estimated_total_usd')
            valid = (type(observed) in (int,float) and math.isfinite(observed)
                     and 0 <= now-observed <= RECONCILIATION_MAX_AGE
                     and type(spend) in (int,float) and math.isfinite(spend) and 0 <= spend < STOP_USD
                     and control.get('month') == month and control.get('enabled') is True)
            recovery = kind in {'recovery', 'evidence_read'}
            if not valid and not recovery:
                raise HostedError(503, 'Pilot admissions are paused until hosting usage is reconciled')
            minute = int(now//60)
            if count['minute'] != minute:
                count.update(minute=minute, minute_requests=0)
            if not nested and count['minute_requests'] >= 20:
                raise HostedError(429, 'Pilot request pace exceeded; retry later')
            use_reserve = recovery and (not valid or count['requests'] >= REQUEST_LIMIT)
            counter, limit = ('recovery_requests', RESERVE_LIMIT) if use_reserve else ('requests', REQUEST_LIMIT)
            if not nested and count[counter] >= limit:
                raise HostedError(429, 'Pilot monthly request allowance reached')
            if kind == 'evidence_read':
                if count['read_bytes'] + byte_count > READ_BYTES_LIMIT:
                    raise HostedError(429, 'Pilot evidence transfer allowance reached')
                count['read_bytes'] += byte_count
            if kind == 'evidence_save':
                if count['upload_bytes'] + byte_count > MONTHLY_UPLOAD_LIMIT:
                    raise HostedError(429, 'Pilot monthly evidence allowance reached')
                count['upload_bytes'] += byte_count
            if not nested:
                count[counter] += 1
                count['minute_requests'] += 1
            transaction.set(COLLECTION, key, count)
        return change(tx) if tx is not None else self.store.transaction(change)

    @contextmanager
    def operation(self, kind):
        self.admit(kind=kind)
        token = _operation_scope.set(self)
        try:
            yield
        finally:
            _operation_scope.reset(token)

    def check_owner(self, uid):
        if uid != self.uid:
            raise HostedError(403, 'This pilot account is not authorized')


class BudgetedService:
    """Apply one-account/budget checks at the common API and MCP service boundary.

    Operator reconciliation and controller worker/cleanup paths remain separate.
    MCP operation scopes charge once for nested public reads; byte reservations
    remain independent. Direct REST calls are charged individually.
    """
    RECOVERY = {'me', 'plans', 'plan', 'sessions', 'session', 'jobs', 'get_job', 'artifacts',
                'cancel', 'terminate', 'received', 'download','models','account_status'}

    def __init__(self, service, budget):
        self._service, self.budget = service, budget

    def __getattr__(self, name):
        return getattr(self._service, name)

    def public(self, uid, operation, *args, **kwargs):
        self.budget.check_owner(uid)
        self.budget.admit(kind='recovery' if operation in self.RECOVERY else 'compute')
        return self._service.public(uid, operation, *args, **kwargs)

    def pilot_operation(self, kind):
        return self.budget.operation(kind)
