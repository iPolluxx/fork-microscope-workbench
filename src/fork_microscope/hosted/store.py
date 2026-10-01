"""Small transactional persistence contract; optional Firestore imports stay lazy."""
from copy import deepcopy
from threading import RLock

class MemoryStore:
    def __init__(self):
        self._data = {}
        self._lock = RLock()
    def transaction(self, fn):
        with self._lock:
            candidate = deepcopy(self._data)
            result = fn(_MemoryTransaction(candidate))
            self._data = candidate
            return deepcopy(result)

class _MemoryTransaction:
    def __init__(self, data): self.data = data
    def get(self, collection, key): return deepcopy(self.data.get(collection, {}).get(key))
    def set(self, collection, key, value): self.data.setdefault(collection, {})[key] = deepcopy(value)
    def list(self, collection): return deepcopy(list(self.data.get(collection, {}).values()))

class FirestoreStore:
    """Durable Firestore adapter. Reads are staged before writes for SDK transactions.

    Transactions query the bounded metadata collections; evidence is never stored here.
    Production deployments should retain terminal metadata outside the active namespace.
    """
    def __init__(self, client=None, namespace='hosted_v1'):
        if client is None:
            from google.cloud import firestore
            client = firestore.Client()
        self.client, self.namespace = client, namespace
    def transaction(self, fn):
        from google.cloud import firestore
        transaction = self.client.transaction()
        @firestore.transactional
        def execute(transaction):
            tx = _FirestoreTransaction(self.client, transaction, self.namespace)
            result = fn(tx)
            tx.flush()
            return result
        return execute(transaction)

class _FirestoreTransaction:
    def __init__(self, client, transaction, namespace):
        self.client, self.transaction, self.namespace = client, transaction, namespace
        self.pending = {}
    def collection(self, name): return self.client.collection(self.namespace + '_' + name)
    def get(self, collection, key):
        if (collection,key) in self.pending: return deepcopy(self.pending[collection,key])
        snap = self.collection(collection).document(key).get(transaction=self.transaction)
        return snap.to_dict() if snap.exists else None
    def list(self, collection):
        values = {snap.id:snap.to_dict() for snap in self.collection(collection).stream(transaction=self.transaction)}
        values.update({key:value for (name,key),value in self.pending.items() if name==collection})
        return deepcopy(list(values.values()))
    def set(self, collection, key, value): self.pending[collection,key] = deepcopy(value)
    def flush(self):
        for (collection,key),value in self.pending.items():
            self.transaction.set(self.collection(collection).document(key),value)
