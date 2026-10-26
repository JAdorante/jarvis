"""One process at a time may hold the microphone."""

import threading
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

_THREAD_LOCK = threading.Lock()


class AudioLock:
    def __init__(self, path):
        self.path = Path(path)
        self._handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        if fcntl is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        else:
            _THREAD_LOCK.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        if fcntl is not None and self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        elif fcntl is None:
            _THREAD_LOCK.release()
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        return False
