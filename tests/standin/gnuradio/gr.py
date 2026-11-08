from . import _log
from ._block import Block
from ._sim import SIM

sizeof_gr_complex, sizeof_float, sizeof_char, sizeof_short, sizeof_int = 8, 4, 1, 2, 4


def version():
    return '3.10 (test stand-in)'


def io_signature(*a, **k):
    return None


class _Base(Block):
    def __init__(self, *a, **k):
        Block.__init__(self, type(self).__name__)


class sync_block(_Base): pass
class basic_block(_Base): pass
class decim_block(_Base): pass
class interp_block(_Base): pass
class hier_block2(_Base): pass


class top_block:
    def __init__(self, *a, **k): pass

    def connect(self, *a):
        _log.log('connect', list(a))

    def disconnect(self, *a):
        _log.log('disconnect', list(a))

    def msg_connect(self, *a): pass
    def lock(self): pass
    def unlock(self): pass
    def wait(self): pass

    def start(self, *a, **k):
        _log.log('start')
        SIM.started_at = SIM.now()

    def stop(self):
        SIM.started_at = None
