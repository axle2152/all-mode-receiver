#!/usr/bin/env python3
"""
test_am_width.py - does the AM filter go where the settings say?

    python3 tests/test_am_width.py

Settings > AM width offers 2.5, 5, 8 and 10.2 kHz with an AUTO switch; the Wide key is
12 kHz. With AUTO on, the filter narrows by itself as the carrier-to-noise (C/N) drops, never
wider than the chosen width; with AUTO off it stays at the chosen width. The 10 kHz notch is
in whenever the filter is open past 9.5 kHz. Runs the AM receiver against the GNU Radio
stand-in on a pretend clock, with the C/N set by the test.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, 'standin'), os.path.dirname(HERE)]

from gnuradio._sim import SIM, FakeTime          # noqa: E402  (the stand-in, not GNU Radio)
import radio.am                                  # noqa: E402
import radio.receiver                            # noqa: E402

radio.am.time = radio.receiver.time = FakeTime()
R = radio.am.AMReceiver
failed = 0


def receiver(width, auto=True, wide=False):
    """An AM receiver set up the way the tuner does it."""
    SIM.reset()
    SIM.fake_clock = True
    SIM.vary = True
    tb = R(freq=1080e3)
    tb.set_auto_ceiling(width)
    tb.set_if_mode('auto' if auto else 'fixed')
    tb.set_wide(wide)
    tb.start()
    return tb


def settle(tb, cnr, secs=10.0):
    """Run the receiver's control loop with the C/N held at cnr (dB in 10 kHz)."""
    t_end = SIM.t + secs
    while SIM.t < t_end:
        tb._cnr_slow = cnr
        tb.update_blend()
        SIM.t += 0.02
    return tb


def check(name, tb, want_hz, want_notch, tol=R.BW_STEP):
    global failed
    ok = abs(tb.bw - want_hz) <= tol and tb.notch_on == want_notch
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:52s} {tb.bw / 1e3:5.2f} kHz, notch {'in ' if tb.notch_on else 'out'}"
          + ('' if ok else f"   (wanted {want_hz / 1e3:.2f} kHz, notch {'in' if want_notch else 'out'})"))


def table(cnr):
    return float(np.interp(cnr, [a for a, b in R.BW_TABLE], [b for a, b in R.BW_TABLE]))


def main():
    print("AUTO on: the chosen width is the ceiling")
    check("8 kHz, strong station (C/N 40 dB)", settle(receiver(8000), 40), 8000, False)
    check("8 kHz, weak station (C/N 20 dB)", settle(receiver(8000), 20), table(20), False)
    check("10.2 kHz, strong station", settle(receiver(10200), 40), 10200, True)
    check("10.2 kHz, C/N 28 dB", settle(receiver(10200), 28), table(28), False)
    check("5 kHz, strong station", settle(receiver(5000), 40), 5000, False)
    check("2.5 kHz, strong station", settle(receiver(2500), 40), 2500, False)

    print("\nAUTO off: the filter stays at the chosen width")
    check("10.2 kHz, weak station (C/N 15 dB)", settle(receiver(10200, auto=False), 15), 10200, True)
    check("5 kHz, weak station", settle(receiver(5000, auto=False), 15), 5000, False)
    check("2.5 kHz, strong station", settle(receiver(2500, auto=False), 40), 2500, False)

    print("\nThe Wide key: 12 kHz with the notch, whatever else is set")
    tb = settle(receiver(5000, auto=False, wide=True), 15)
    check("Wide over 5 kHz fixed, weak station", tb, 12000, True, tol=0)
    tb.set_auto_ceiling(8000)
    tb.set_if_mode('auto')
    check("...width and AUTO changed while Wide is on", settle(tb, 15), 12000, True, tol=0)
    tb.set_if_mode('fixed')
    tb.set_wide(False)
    check("...Wide off: back to 8 kHz fixed", settle(tb, 15), 8000, False, tol=0)
    tb = settle(receiver(10200, wide=True), 40)
    tb.set_wide(False)
    check("Wide off with AUTO on, strong station: 10.2 kHz", settle(tb, 40), 10200, True)

    print("\nOlder settings files")
    tb = receiver(8000)
    tb.set_if_mode('wide')
    check("BANDWIDTH WIDE from an old settings file", settle(tb, 15), R.AM_WIDE_HZ, True, tol=0)
    tb.set_if_mode('narrow')
    check("BANDWIDTH NARROW from an old settings file", settle(tb, 40), R.AM_NARROW_HZ, False, tol=0)

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
