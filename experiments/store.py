"""Private, atomic local records. One lock serializes revisions, queue admission and events."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

from .config import digest, identifier


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_mode & 0o777 != 0o700:
            os.chmod(self.root, 0o700)

    @contextlib.contextmanager
    def lock(self, name='state', blocking=True):
        identifier(name)
        with open(self.root / ('.' + name + '.lock'), 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def path(self, kind, name):
        identifier(kind); identifier(name)
        return self.root / kind / (name + '.json')

    def read(self, kind, name):
        return json.loads(self.path(kind, name).read_text())

    def save(self, kind, name, value):
        write_json(self.path(kind, name), value)

    def list(self, kind):
        identifier(kind)
        return [json.loads(p.read_text()) for p in sorted((self.root / kind).glob('*.json'))]

    def token(self):
        with self.lock():
            path = self.root / 'access-token'
            if not path.exists():
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as out:
                    out.write(secrets.token_urlsafe(32))
            return path.read_text().strip()

    def event(self, name, experiment_id, attempt_id=None, detail=None):
        """Caller holds state lock. Stable content key suppresses unchanged observations."""
        payload = {'name': name, 'experiment_id': experiment_id, 'attempt_id': attempt_id,
                   'detail': detail or {}}
        event_id = 'evt-' + digest(payload)[:32]
        path = self.path('events', event_id)
        if not path.exists():
            self.save('events', event_id, payload | {'event_id': event_id, 'at': time.time()})
        return event_id
