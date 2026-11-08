#!/usr/bin/env python3
"""
test_addons.py - do installed add-ons plug in, and does a missing or broken one stay harmless?

    python3 tests/test_addons.py

Runs the receivers against the GNU Radio stand-in with pretend add-ons in place of installed
ones (see radio/addons.py and docs/ADDONS.md): with none, nothing changes and nothing is
printed; a working one is started on AM only, its station data reaches the receiver, and a
retune clears it; one that fails to load, to start, or while running costs one line in the
terminal and never stops the receiver.
"""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, 'standin'), os.path.dirname(HERE)]

from gnuradio import blocks, gr                   # noqa: E402  (the stand-in, not GNU Radio)
from gnuradio._sim import SIM, FakeTime          # noqa: E402
import radio.addons                              # noqa: E402
import radio.am                                  # noqa: E402
import radio.fm.receiver                         # noqa: E402
import radio.receiver                            # noqa: E402

radio.am.time = radio.fm.receiver.time = radio.receiver.time = FakeTime()
failed = 0


def check(name, ok, detail=''):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f'   ({detail})'))


class EntryPoint:
    """Stands in for an installed package's entry point."""
    def __init__(self, name, cls=None, error=None):
        self.name, self._cls, self._error = name, cls, error

    def load(self):
        if self._error:
            raise ImportError(self._error)
        return self._cls


class Good:
    BANDS = ('AM',)

    def __init__(self, rx):
        self.rx, self.resets, self.n = rx, 0, 0
        self.sink = blocks.null_sink(gr.sizeof_gr_complex)
        rx.connect(rx.addon_tap(), self.sink)

    def reset(self):
        self.resets += 1

    def update(self, now):
        self.n += 1
        return {'ps': 'TESTING', 'pi': 0x54A8, 'call': 'WAAA', 'synced': True, 'extra': self.n}

    def readout(self):
        return 'Test add-on: all well'


class BrokenStart:
    BANDS = ('AM',)

    def __init__(self, rx):
        raise ValueError('no carrier tap')


class BrokenUpdate(Good):
    def update(self, now):
        raise ZeroDivisionError('oops')


class FmOnly(Good):
    BANDS = ('FM',)


def install(*eps):
    radio.addons._entry_points = lambda: list(eps)


def build(cls, **kw):
    """A receiver with whatever is 'installed', and what it printed while starting."""
    SIM.reset()
    SIM.fake_clock = True
    SIM.vary = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        tb = cls(**kw)
        tb.start()
    return tb, out.getvalue()


def run(tb, secs=1.0):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        t_end = SIM.t + secs
        while SIM.t < t_end:
            tb.update_blend()
            SIM.t += 0.02
    return out.getvalue()


def main():
    AM, FM = radio.am.AMReceiver, radio.fm.receiver.FMStereoReceiver

    print("Nothing installed")
    install()
    tb, said = build(AM, freq=1080e3)
    check("AM starts with no add-ons", tb.addons == [])
    check("and prints nothing", said == '' and run(tb) == '', repr(said))
    check("station data stays empty", tb.rds_ps == '' and tb.rds_pi is None)
    tb.stop()

    print("\nA working add-on")
    install(EntryPoint('good', Good))
    tb, said = build(AM, freq=1080e3)
    check("started on AM", len(tb.addons) == 1 and said == '', said)
    run(tb)
    check("its station data reaches the receiver",
          (tb.rds_ps, tb.rds_pi, tb.rds_call, tb.rds_synced) == ('TESTING', 0x54A8, 'WAAA', True),
          (tb.rds_ps, tb.rds_pi, tb.rds_call, tb.rds_synced))
    check("fields of its own are kept in rx.station", tb.station.get('extra', 0) > 0)
    check("its lines are in the Advanced readout", 'Test add-on: all well' in tb.readout())
    tb.set_freq(750e3)
    check("a retune resets it and clears the old station",
          tb.addons[0].obj.resets == 1 and tb.rds_ps == '' and tb.rds_pi is None and tb.station == {})
    tb.stop()
    tb, said = build(FM, freq=90.5e6)
    check("not started on FM (it serves AM)", tb.addons == [] and said == '')
    tb.stop()

    print("\nBroken add-ons")
    install(EntryPoint('missing', error="No module named 'x'"), EntryPoint('good', Good))
    tb, said = build(AM, freq=1080e3)
    check("one that won't load: one line, the others still start",
          said.count('\n') == 1 and "Add-on 'missing' not used" in said and len(tb.addons) == 1, said)
    tb.stop()
    install(EntryPoint('broken', BrokenStart))
    tb, said = build(AM, freq=1080e3)
    check("one that fails to start: one line, AM carries on",
          said.count('\n') == 1 and "Add-on 'broken' not used: ValueError" in said and tb.addons == [], said)
    tb.stop()
    install(EntryPoint('flaky', BrokenUpdate))
    tb, said = build(AM, freq=1080e3)
    said = run(tb, 2.0)
    check("one that fails while running: one line, then it's dropped",
          said.count('\n') == 1 and "Add-on 'flaky' stopped: ZeroDivisionError" in said
          and not tb.addons[0].alive, said)
    check("and the receiver keeps running", run(tb, 2.0) == '')
    tb.stop()
    install(EntryPoint('fm', FmOnly))
    tb, said = build(FM, freq=90.5e6)
    check("an FM add-on isn't started: FM takes no add-ons yet", tb.addons == [] and said == '')
    tb.stop()

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
