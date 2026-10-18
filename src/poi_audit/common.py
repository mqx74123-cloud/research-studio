"""Small shared helpers with no geospatial dependency."""
from pathlib import Path
import hashlib

class AuditError(ValueError):
    """An actionable input/configuration problem."""


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _unknown(obj, allowed, context):
    extra = set(obj) - set(allowed)
    if extra:
        raise AuditError(f"{context}: unknown keys {sorted(extra)}")


def resolve(base, path):
    p = Path(path)
    return p if p.is_absolute() else base / p


