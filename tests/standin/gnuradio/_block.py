"""One permissive block type for every GNU Radio block the receivers build."""
from . import _log
from ._sim import SIM


class Block:
    def __init__(self, kind='block', *a, **k):
        self._kind, self._a = kind, a
        _log.register(self, kind, a, k)

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        if name in ('nitems_read', 'nitems_written'):
            return lambda port=0: SIM.count()
        if name == 'level':
            return self._level
        def call(*a, **k):                        # setters, getters: accepted, nothing happens
            _log.log('call', _log.oid(self), name, a, k)
            return 0.0
        return call

    def _value(self):
        n = SIM.count()
        if n == 0:
            return 0j
        return SIM.wb_value + (1e-12 * n if SIM.vary else 0.0)

    def _level(self):
        if self._kind == 'probe_signal_vc':
            return [self._value()] * self._a[0]
        if self._kind == 'probe_signal_vf':
            return [0.0] * self._a[0]
        if self._kind == 'probe_signal_c':
            v = self._value()
            return complex(v.real * 1e4, 0.0) if v else 0j
        return 0.0


def module_getattr(name):
    if name.startswith('__'):
        raise AttributeError(name)
    return lambda *a, **k: Block(name, *a, **k)
