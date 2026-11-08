"""Optional event log for the stand-in, for tests/trace_receivers.py: every block made (with
its arguments), every connection and every call on a block. Off unless STANDIN_TRACE=1."""
import hashlib
import numbers
import os

import numpy as np

ON = os.environ.get('STANDIN_TRACE') == '1'
EVENTS = []
_ids = {}
_keep = []          # logged blocks stay alive, so their id() is never reused


def oid(obj):
    return _ids.get(id(obj))


def register(obj, kind, args, kw):
    if not ON:
        return
    n = len(_keep)
    _keep.append(obj)
    _ids[id(obj)] = n
    EVENTS.append(('new', n, kind, canon(args), canon(kw)))


def log(*event):
    if ON:
        EVENTS.append(tuple(canon(e) if i else e for i, e in enumerate(event)))


def canon(v):
    """A JSON-friendly, repeatable form: floats to 10 digits, long arrays as a hash."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, numbers.Integral):
        return int(v)
    if isinstance(v, numbers.Complex) and not isinstance(v, numbers.Real):
        return ['c', round(complex(v).real, 9), round(complex(v).imag, 9)]
    if isinstance(v, numbers.Real):
        return float('%.10g' % float(v))
    if isinstance(v, np.ndarray) or (isinstance(v, (list, tuple)) and len(v) > 16
                                     and all(isinstance(x, numbers.Number) for x in v)):
        a = np.asarray(v, dtype=complex).ravel()
        r = np.round(a.real, 9) + 1j * np.round(a.imag, 9)
        return ['arr', len(a), hashlib.md5(r.tobytes()).hexdigest()[:12]]
    if isinstance(v, dict):
        return {str(k): canon(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (list, tuple)):
        return [canon(x) for x in v]
    i = oid(v)
    if i is not None:
        return ['blk', i]
    if isinstance(v, type):
        return ['type', v.__name__]
    return ['obj', type(v).__name__]
