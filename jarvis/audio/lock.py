"""Whoever is speaking, or hearing a live sentence, holds this lock."""

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
        self._held = False

    def acquire(self, blocking=True):
        if self._held:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if fcntl is not None:
            flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            try:
                fcntl.flock(handle.fileno(), flags)
            except OSError:
                handle.close()
                if blocking:
                    raise
                return False
        elif not _THREAD_LOCK.acquire(blocking):
            handle.close()
            return False
        self._handle = handle
        self._held = True
        return True

    def release(self):
        if not self._held:
            return
        if fcntl is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        else:
            _THREAD_LOCK.release()
        self._handle.close()
        self._handle = None
        self._held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
