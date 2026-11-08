#!/usr/bin/env python3
"""
test_watchdog.py - does each receiver tell a lost dongle from a quiet one?

    python3 tests/test_watchdog.py

Runs the AM and FM receivers against the GNU Radio stand-in (tests/standin) on a pretend
clock, so it needs neither GNU Radio nor a dongle and takes a second or two. The tuner calls
it 'Dongle lost' after 5 errors in a row from update_blend(); each case says when that may
happen, if at all.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, 'standin'), os.path.dirname(HERE)]

from gnuradio._sim import SIM, FakeTime          # noqa: E402  (the stand-in, not GNU Radio)
import am_receiver                               # noqa: E402
import fm_stereo                                 # noqa: E402

am_receiver.time = fm_stereo.time = FakeTime()
GIVE_UP = 5                                      # the tuner's _MAX_ERRORS
STEP = 0.02                                      # the tuner calls update_blend() every 20 ms
RUN_S = 15.0

# (what happens, settings for the pretend dongle, when 'Dongle lost' may come: None = never)
CASES = [
    ("steady signal, samples flowing",           dict(), None),
    ("changing signal, samples flowing",          dict(vary=True), None),
    ("dongle slow to open (3 s before start)",    dict(open_s=3.0, vary=True), None),
    ("first samples 5 s after start",             dict(first=5.0, vary=True), None),
    ("samples stop 4 s after start",              dict(stop=4.0, vary=True), (5.9, 6.5)),
    ("steady signal, then stop at 4 s",           dict(stop=4.0), (5.9, 6.5)),
    ("no samples ever",                           dict(first=99.0), (7.9, 8.5)),
]


def run(make, open_s=0.0, first=0.0, stop=None, vary=False):
    """Seconds after start() until the tuner would say 'Dongle lost', or None."""
    SIM.reset()
    SIM.fake_clock = True
    SIM.first_after, SIM.stop_after, SIM.vary = first, stop, vary
    tb = make()
    SIM.t += open_s
    tb.start()
    t0, errors = SIM.t, 0
    try:
        while SIM.t - t0 < RUN_S:
            try:
                tb.update_blend()
                errors = 0
            except RuntimeError:
                errors += 1
                if errors >= GIVE_UP:
                    return SIM.t - t0
            SIM.t += STEP
        return None
    finally:
        tb.stop()


def main():
    failed = 0
    for band, make in (("AM", lambda: am_receiver.AMReceiver(freq=1080e3)),
                       ("FM", lambda: fm_stereo.FMStereoReceiver(freq=90.5e6))):
        print(f"{band} receiver")
        for name, kw, window in CASES:
            t = run(make, **kw)
            ok = t is None if window is None else (t is not None and window[0] <= t <= window[1])
            got = 'no alarm' if t is None else f'Dongle lost at {t:.1f} s'
            want = 'no alarm' if window is None else f'Dongle lost at {window[0]:.1f}-{window[1]:.1f} s'
            print(f"  {'ok  ' if ok else 'FAIL'} {name:42s} {got}" + ('' if ok else f'  (wanted {want})'))
            failed += not ok
    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
