#!/usr/bin/env python3
"""
test_loudness.py - does the monitor's loudness meter read what the standards say it should?

    python3 tests/test_loudness.py

Checks radio/meters.py (K-weighting, 100 ms block energies) and the window's Loudness class
(momentary, short-term, gated integrated loudness, loudness range) against:
  - the K-weighting coefficients printed in ITU-R BS.1770-4 for 48 kHz
  - the EBU Tech 3341 test signals for integrated loudness (+/-0.1 LU)
  - the EBU Tech 3342 test signals for loudness range (+/-1 LU)
The test signals are 1 kHz sine waves, which the K-weighting only scales, so the weighted
signal is worked out exactly from the filter's response. Needs only numpy: no GNU Radio, no
dongle, no window.
"""
import cmath
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, 'standin'), os.path.dirname(HERE)]

from radio import meters as rx_meters            # noqa: E402
from radio.ui import loudness                    # noqa: E402

FS = 48000
failed = 0


def check(name, got, want, tol):
    global failed
    ok = got is not None and abs(got - want) <= tol
    failed += not ok
    shown = 'none' if got is None else f'{got:+.4f}'
    print(f"  {'ok  ' if ok else 'FAIL'} {name:52s} {shown}   (want {want:+.4f} +/- {tol})")


def k_gain(f):
    """Power gain of the K-weighting at frequency f, from rx_meters' own coefficients."""
    b1, a1, b2, a2 = rx_meters.k_weighting(FS)
    z = cmath.exp(-2j * math.pi * f / FS)
    h = 1.0
    for b, a in ((b1, a1), (b2, a2)):
        h *= (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    return abs(h) ** 2


def play(segments):
    """Feed (seconds, dBFS) stretches of a 1 kHz stereo sine through the meter; return
    the tuner's Loudness object after it. dBFS is the sine's peak, as EBU Tech 3341 uses."""
    core = rx_meters.AudioMeterCore(FS)
    loud = loudness.Loudness()
    gk = math.sqrt(k_gain(1000.0))
    t0 = 0
    for secs, dbfs in segments:
        a = 10 ** (dbfs / 20)
        for _ in range(int(round(secs))):        # a second at a time, as the tuner reads it
            n = np.arange(t0, t0 + FS)
            t0 += FS
            x = a * np.sin(2 * np.pi * 1000.0 * n / FS)
            core.process(x, x, gk * x, gk * x)
            loud.feed(core.read()['blocks'], True)
        frac = secs - int(round(secs))
        if frac > 1e-9:
            n = np.arange(t0, t0 + int(round(frac * FS)))
            t0 += len(n)
            x = a * np.sin(2 * np.pi * 1000.0 * n / FS)
            core.process(x, x, gk * x, gk * x)
            loud.feed(core.read()['blocks'], True)
    return loud


def main():
    print("K-weighting at 48 kHz (BS.1770-4, Tables 1 and 2)")
    b1, a1, b2, a2 = rx_meters.k_weighting(FS)
    published = [('stage 1 b0', b1[0], 1.53512485958697), ('stage 1 b1', b1[1], -2.69169618940638),
                 ('stage 1 b2', b1[2], 1.19839281085285), ('stage 1 a1', a1[1], -1.69065929318241),
                 ('stage 1 a2', a1[2], 0.73248077421585), ('stage 2 b0', b2[0], 1.0),
                 ('stage 2 b1', b2[1], -2.0), ('stage 2 b2', b2[2], 1.0),
                 ('stage 2 a1', a2[1], -1.99004745483398), ('stage 2 a2', a2[2], 0.99007225036621)]
    for name, got, want in published:
        check(name, got, want, 1e-6)

    print("\nIntegrated loudness (EBU Tech 3341, stereo 1 kHz sine)")
    big = 1e12                                   # 'now' for the tuner's recalculation timer
    cases = [
        ("case 1: -23 dBFS, 20 s", [(20, -23)], -23.0),
        ("case 2: -33 dBFS, 20 s", [(20, -33)], -33.0),
        ("case 3: -36 / -23 / -36 dBFS (relative gate)", [(10, -36), (60, -23), (10, -36)], -23.0),
        ("case 4: -72 / -36 / -23 / -36 / -72 (absolute gate)",
         [(10, -72), (10, -36), (60, -23), (10, -36), (10, -72)], -23.0),
        ("case 5: -26 / -20 / -26 dBFS", [(20, -26), (20.1, -20), (20, -26)], -23.0),
    ]
    for name, seg, want in cases:
        check(name, play(seg).integrated(big), want, 0.1)

    print("\nMomentary and short-term on a steady -23 dBFS sine")
    loud = play([(10, -23)])
    check("momentary (400 ms)", loud.momentary(), -23.0, 0.1)
    check("short-term (3 s)", loud.short_term(), -23.0, 0.1)

    print("\nLoudness range (EBU Tech 3342, stereo 1 kHz sine)")
    for name, seg, want in [
            ("case 1: -20 then -30 dBFS, 20 s each", [(20, -20), (20, -30)], 10.0),
            ("case 2: -20 then -15 dBFS, 20 s each", [(20, -20), (20, -15)], 5.0),
            ("case 3: -40 then -20 dBFS, 20 s each", [(20, -40), (20, -20)], 20.0),
            ("case 4: -50 / -35 / -20 / -35 / -50 dBFS, 20 s each",
             [(20, -50), (20, -35), (20, -20), (20, -35), (20, -50)], 15.0)]:
        check(name, play(seg).lra(big), want, 1.0)

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
