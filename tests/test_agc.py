#!/usr/bin/env python3
"""
test_agc.py - the HF receiver's AGC (radio/agc.py). Needs neither GNU Radio nor a dongle.

    python3 tests/test_agc.py

A steady signal comes out at the reference level whatever its strength; a loud signal arriving
out of quiet comes out without overshoot from its first sample (the look-ahead); after a loud
burst the gain holds for the hang time, then recovers at the set rate; an empty channel's noise
plays at the ceiling's level below the reference; and the output doesn't depend on how the
flowgraph chops the samples up.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402

from radio.agc import AGCCore, max_gain_for  # noqa: E402

FS = 48000
failed = 0


def check(name, ok, detail=''):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:56s} {detail}")


def tone(amp, secs, hz=1000.0, t0=0.0):
    t = t0 + np.arange(int(secs * FS)) / FS
    return (amp * np.exp(2j * np.pi * hz * t)).astype(np.complex64)


def db(x):
    return 20 * math.log10(max(float(x), 1e-12))


def run(x, chunk=4096, **kw):
    a = AGCCore(FS, **kw)
    out = [a.process(x[i:i + chunk]) for i in range(0, len(x), chunk)]
    return a, np.concatenate(out)


def main():
    ref = 0.5
    print('Level')
    for amp in (1e-4, 1e-2, 1.0):
        _, y = run(tone(amp, 1.0), ref=ref, max_gain=1e6)
        pk = np.max(np.abs(y[FS // 2:]))
        check(f'a steady tone at {db(amp):+.0f} dBFS comes out at the reference', abs(db(pk / ref)) < 0.2,
              f'{db(pk / ref):+.2f} dB')

    print('Attack: a loud tone out of quiet')
    x = np.concatenate((tone(1e-3, 1.0), tone(0.8, 0.5, t0=1.0)))
    a, y = run(x, ref=ref, max_gain=1e6)
    lag = 2 * a.step
    burst = y[FS + lag:]
    check('no overshoot, from its first sample', np.max(np.abs(burst)) <= ref * 1.001,
          f'peak {db(np.max(np.abs(burst)) / ref):+.2f} dB re the reference')
    check('the audio is 2 ms late', lag == 2 * FS // 1000, f'{lag} samples')
    check('quiet before it: the weak tone at the reference too', abs(db(np.max(np.abs(y[FS // 2:FS])) / ref)) < 0.5)

    print('Hang, then decay')
    hang, rate = 0.3, 20.0
    x = np.concatenate((tone(0.5, 0.5), tone(0.5e-2, 2.5, t0=0.5)))     # loud, then 40 dB down
    a, y = run(x, ref=ref, max_gain=1e6, hang_s=hang, decay_db_s=rate)
    env = lambda t0, t1: db(np.max(np.abs(y[int(t0 * FS) + 2 * a.step:int(t1 * FS) + 2 * a.step])) / ref)
    held = env(0.55, 0.5 + hang - 0.02)
    check('the gain holds for the hang time', held < -39.5, f'weak tone at {held:+.1f} dB re ref')
    rise = env(0.5 + hang + 1.0, 0.5 + hang + 1.05) - env(0.5 + hang + 0.5, 0.5 + hang + 0.55)
    check(f'then recovers at {rate:g} dB/s', abs(rise - rate * 0.5) < 1.0, f'{rise / 0.5:.1f} dB/s')
    late = env(2.9, 3.0)
    check('and the weak tone ends up at the reference', abs(late) < 0.5, f'{late:+.1f} dB')

    print('Ceiling')
    rng = np.random.default_rng(3)
    r = 1e-3
    n = (rng.standard_normal(3 * FS) + 1j * rng.standard_normal(3 * FS)) * r / math.sqrt(2)
    a, y = run(n.astype(np.complex64), ref=ref, max_gain=max_gain_for(r, ref, 20.0))
    rms = math.sqrt(2) * float(np.sqrt(np.mean(y[FS:] ** 2)))     # the real part carries half
    check('an empty channel plays 20 dB under the reference', abs(db(rms / ref) + 20) < 1.0,
          f'{db(rms / ref):+.1f} dB')
    x = np.concatenate((n[:FS].astype(np.complex64), tone(0.1, 1.0, t0=1.0) + n[FS:2 * FS].astype(np.complex64)))
    a, y = run(x, ref=ref, max_gain=max_gain_for(r, ref, 20.0))
    check('a station above it still comes up to the reference', abs(db(np.max(np.abs(y[int(1.5 * FS):])) / ref)) < 1.0)

    print('Chunks')
    x = np.concatenate((tone(1e-3, 0.3), tone(0.7, 0.2, hz=700.0, t0=0.3), tone(2e-3, 0.5, t0=0.5)))
    _, whole = run(x, chunk=len(x), ref=ref, max_gain=1e6)
    a = AGCCore(FS, ref=ref, max_gain=1e6)
    sizes, out, i = rng.integers(1, 3000, 1000), [], 0
    for s in sizes:
        out.append(a.process(x[i:i + s]))
        i += s
        if i >= len(x):
            break
    out = np.concatenate(out)[:len(whole)]
    check('the same output in any chunk sizes', len(out) == len(whole) and np.allclose(out, whole, atol=1e-6),
          f'{len(out)} samples')

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
