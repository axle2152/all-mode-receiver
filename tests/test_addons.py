#!/usr/bin/env python3
"""
test_addons.py - do installed add-ons plug in, and does a missing or broken one stay harmless?

    python3 tests/test_addons.py

Runs the receivers against the GNU Radio stand-in with pretend add-ons in place of installed
ones (see radio/addons.py and docs/ADDONS.md): with none, nothing changes and nothing is
printed; a working one is started on the band it serves, its station data reaches the
receiver, and a retune clears it; one that fails to load, to start, or while running costs one
line in the terminal and never stops the receiver. On FM, a stereo add-on's L-R plays only
without a pilot and while it says its stereo is on, blended as it says, with the main channel
raised by its make-up gain; a pilot, a retune or the add-on stopping hands stereo back.
"""
import contextlib
import io
import math
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
    NAME = 'TEST'

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

    def meter(self):
        return ('Test data', 0.5, 'half')


class BrokenStart:
    BANDS = ('AM',)

    def __init__(self, rx):
        raise ValueError('no carrier tap')


class BrokenUpdate(Good):
    def update(self, now):
        raise ZeroDivisionError('oops')


class SaysMono(Good):
    def update(self, now):
        return {'mono': True}


class FmOnly(Good):
    BANDS = ('FM',)


class Stereo:
    """A pretend FM stereo add-on (its own system, on a station with no pilot)."""
    BANDS = ('FM',)
    NAME = 'STX'
    on, quality = True, 0.5

    def __init__(self, rx):
        self.out = blocks.multiply_const_ff(0.0)
        rx.connect(rx.addon_tap(), self.out)
        self.resets = 0

    def stereo_decoder(self):
        return {'out': (self.out, 0), 'main_gain': 2.0, 'mpx_top': 83e3}

    def reset(self):
        self.resets += 1

    def update(self, now):
        return {'stereo': self.on, 'stereo_quality': self.quality, 'synced': self.on}


class StereoGain(Stereo):
    """...that measures the make-up gain it needs as it goes."""
    gain = 3.0

    def update(self, now):
        return dict(Stereo.update(self, now), main_gain=self.gain)


class StereoBadDecoder(Stereo):
    def stereo_decoder(self):
        return {'out': 'not a block', 'main_gain': 2.0}


class StereoDies(Stereo):
    die = False

    def update(self, now):
        if self.die:
            raise RuntimeError('lost it')
        return Stereo.update(self, now)


def install(*eps):
    radio.addons._entry_points = lambda: list(eps)


def build(cls, pilot=True, **kw):
    """A receiver with whatever is 'installed', and what it printed while starting. On FM the
    stand-in's steady probes pass for a stereo pilot; pilot=False makes it a station without."""
    SIM.reset()
    SIM.fake_clock = True
    SIM.vary = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        tb = cls(**kw)
        if not pilot:
            tb.PILOT_ON = tb.PILOT_OFF = 1.0
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


def stereo_tests(FM):
    print("\nFM add-ons")
    install()
    tb, said = build(FM, freq=90.5e6)
    check("FM starts with no add-ons, prints nothing, and wires nothing extra",
          tb.addons == [] and said == '' and tb._stereo is None and run(tb) == '')
    check("FM offers add-ons the composite", tb.addon_tap() == (tb.fm_demod, 0))
    tb.stop()

    install(EntryPoint('fm', FmOnly))
    tb, said = build(FM, freq=90.5e6)
    check("an FM add-on is started on FM", len(tb.addons) == 1 and said == '', said)
    run(tb)
    check("its station data stays in rx.station: FM's own RDS keeps its fields",
          tb.station.get('ps') == 'TESTING' and tb.rds_ps != 'TESTING' and tb.rds_pi is None)
    check("one with no stereo decoder leaves the matrix alone", tb._stereo is None)
    tb.stop()

    install(EntryPoint('stx', Stereo))
    Stereo.on, Stereo.quality = True, 0.5
    tb, said = build(FM, pilot=False, freq=90.5e6)
    check("a stereo add-on joins the matrix", tb._stereo is not None and said == '', said)
    run(tb, 1.0)
    check("no pilot, its stereo on: its L-R plays, blended as it says",
          tb.addon_stereo == 'STX' and tb._stereo['route'] == 'addon' and tb.stereo_indicator
          and abs(tb.current_blend - 0.5) < 0.01, (tb.addon_stereo, tb.current_blend))
    check("and the main channel is raised by its make-up gain (6 dB)",
          abs(tb._stereo['gain_db'] - 6.02) < 0.01, tb._stereo['gain_db'])
    tb.set_enhancements(blend=False)
    run(tb, 0.2)
    check("blend off: full stereo", tb.current_blend == 1.0, tb.current_blend)
    tb.set_enhancements(blend=True)
    tb.set_force_mono(True)
    run(tb, 0.3)
    check("mono: no L-R, but the main channel keeps its make-up",
          tb.target_lmr == 0.0 and abs(tb._stereo['gain_db'] - 6.02) < 0.01)
    tb.set_force_mono(False)
    del tb.PILOT_ON, tb.PILOT_OFF                 # the stand-in's probes now pass for a pilot
    run(tb, 1.0)
    check("a pilot wins: the pilot decoder plays, the main channel back to normal",
          tb.pilot_present and tb.addon_stereo is None and tb._stereo['route'] == 'own'
          and tb._stereo['gain_db'] == 0.0, (tb.pilot_present, tb._stereo))
    tb.PILOT_ON = tb.PILOT_OFF = 1.0
    run(tb, 1.0)
    check("pilot gone: back to the add-on's stereo", tb.addon_stereo == 'STX')
    tb.set_freq(98.1e6)
    check("a retune: the add-on starts over, the matrix and level go back at once",
          tb.addons[0].obj.resets == 1 and tb._stereo['route'] == 'own'
          and tb._stereo['gain_db'] == 0.0 and tb.station == {})
    Stereo.on = False
    run(tb, 1.0)
    check("its stereo off: mono at the normal level",
          tb.addon_stereo is None and not tb.stereo_indicator and tb._stereo['gain_db'] == 0.0)
    tb.stop()
    Stereo.on = True

    install(EntryPoint('gain', StereoGain))
    tb, said = build(FM, pilot=False, freq=90.5e6)
    run(tb, 1.0)
    check("one that reports the make-up gain it needs: the main channel follows it",
          abs(tb._stereo['gain_db'] - 20 * math.log10(3.0)) < 0.01, tb._stereo['gain_db'])
    StereoGain.gain = 100.0
    run(tb, 1.0)
    check("a gain out of range (past 8): its decoder's own gain instead",
          abs(tb._stereo['gain_db'] - 6.02) < 0.01, tb._stereo['gain_db'])
    tb.stop()

    install(EntryPoint('bad', StereoBadDecoder))
    tb, said = build(FM, pilot=False, freq=90.5e6)
    check("a stereo decoder that can't be used: one line, the add-on runs on without it",
          said.count('\n') == 1 and "Add-on 'bad' stereo not used" in said and tb._stereo is None
          and tb.addons[0].alive, said)
    check("and the receiver plays on", run(tb) == '' and tb.addon_stereo is None)
    tb.stop()

    install(EntryPoint('dies', StereoDies))
    StereoDies.die = False
    tb, said = build(FM, pilot=False, freq=90.5e6)
    run(tb, 1.0)
    ok_before = tb.addon_stereo == 'STX'
    StereoDies.die = True
    said = run(tb, 1.0)
    check("a stereo add-on that stops: one line, and stereo goes back to the pilot decoder",
          ok_before and said.count('\n') == 1 and "Add-on 'dies' stopped" in said
          and tb.addon_stereo is None and tb._stereo['route'] == 'own'
          and tb._stereo['gain_db'] == 0.0, (ok_before, said, tb._stereo))
    tb.stop()


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
    check("it listens before the carrier lock (the add-on runs its own)",
          tb.addon_tap() == (tb.if_filter, 0))
    check("its name, meter and readout reach the window",
          tb.addons[0].label == 'TEST' and tb.addons[0].meter() == ('Test data', 0.5, 'half'))
    check("every station field starts empty",
          (tb.rds_ps, tb.rds_pi, tb.rds_af, tb.rds_tp, tb.rds_rate, tb.rds_ms) == ('', None, [], None, 0.0, None))
    run(tb)
    check("its station data reaches the receiver",
          (tb.rds_ps, tb.rds_pi, tb.rds_call, tb.rds_synced) == ('TESTING', 0x54A8, 'WAAA', True),
          (tb.rds_ps, tb.rds_pi, tb.rds_call, tb.rds_synced))
    check("fields of its own are kept in rx.station", tb.station.get('extra', 0) > 0)
    check("its lines are in the Advanced readout", 'Test add-on: all well' in tb.readout())
    tb.set_freq(750e3)
    check("a retune resets it and clears the old station",
          tb.addons[0].obj.resets == 1 and tb.rds_ps == '' and tb.rds_pi is None and tb.station == {}
          and tb.rds_synced is False and tb.rds_af == [])
    tb.stop()
    tb, said = build(FM, freq=90.5e6)
    check("not started on FM (it serves AM)", tb.addons == [] and said == '')
    tb.stop()

    install(EntryPoint('mono', SaysMono))
    tb, said = build(AM, freq=1080e3)
    tb.pilot_on, tb._pil_hits = True, 3                   # as if something passed for a pilot
    run(tb, 0.1)
    check("one that says the station is mono: no stereo pilot, whatever it looks like",
          tb.pilot_on is False and tb._pil_hits == 0)
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
    stereo_tests(FM)

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
