"""Optional hosted compute; importing the local package needs no cloud SDK."""
from .service import HostedService, HostedError
from .store import MemoryStore, FirestoreStore
__all__ = ['HostedService', 'HostedError', 'MemoryStore', 'FirestoreStore']
