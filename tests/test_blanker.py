#!/usr/bin/env python3
"""
test_blanker.py - the impulse and noise blankers (radio/blanker.py). Needs neither GNU Radio
nor a dongle.

    python3 tests/test_blanker.py

Off, or with nothing to catch, a blanker passes the signal through untouched (only late by its
look-ahead); pulses on a station are patched so what's left is far below them, and counted;
a burst longer than a pulse is left alone, even across the flowgraph's chunks; the result
doesn't depend on how the samples are chopped up; set too keen on dense noise it backs itself
off; and the stereo glitch gate mutes the difference signal around a carrier glitch only.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402

from radio.blanker import BlankerCore, GlitchGateCore, EventRate  # noqa: E402

failed = 0
rng = np.random.default_rng(7)


def check(name, ok, detail=''):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:60s} {detail}")


def db(x):
    return 10 * math.log10(max(float(x), 1e-30))


def am_station(fs, secs, off_hz=-20e3, mod=0.8, audio_hz=1100.0, noise=0.01):
    """A carrier at off_hz, 80% modulated, with a little noise: what the dongle sends."""
    t = np.arange(int(secs * fs)) / fs
    env = 1.0 + mod * np.sin(2 * np.pi * audio_hz * t)
    x = 0.1 * env * np.exp(2j * np.pi * off_hz * t)
    x = x + noise * 0.1 * (rng.standard_normal(len(t)) + 1j * rng.standard_normal(len(t)))
    return x.astype(np.complex64)


def pulses(n, every, height, width=3, phase0=0):
    """Spikes `height` tall, `width` samples wide, every `every` samples."""
    p = np.zeros(n, np.complex64)
    for i in range(phase0, n - width, every):
        p[i:i + width] = height * np.exp(1j * rng.uniform(0, 2 * np.pi))
    return p


def run(core, x, chunk=9600):
    if callable(chunk):
        out, i = [], 0
        while i < len(x):
            c = chunk()
            out.append(core.process(x[i:i + c]))
            i += c
        return np.concatenate(out)
    return np.concatenate([core.process(x[i:i + chunk]) for i in range(0, len(x), chunk)])


def nb(fs=960_000, k=5.6, **kw):
    c = BlankerCore(fs, pre=3, post=6, max_run=48, backoff=True, **kw)
    c.set_k(k)
    return c


def main():
    fs = 960_000
    x = am_station(fs, 0.5)
    D = nb().delay

    print('Untouched')
    c = nb(k=None)
    y = run(c, x)
    check('off: the signal passes exactly, only late by the look-ahead',
          np.array_equal(y[D:], x[:-D]), f'{D} samples = {1e6 * D / fs:.0f} us')
    c = nb(k=4.0)
    y = run(c, x)
    check('on (HIGH), a clean station: passes exactly', np.array_equal(y[D:], x[:-D]),
          f'{c.n_blanked} samples blanked')
    ib = BlankerCore(48_000, pre=4, post=8, max_run=96)
    ib.set_k(4.0)
    t = np.arange(48_000) / 48_000
    full = (1.0 + 1.25 * np.maximum(np.sin(2 * np.pi * 1100 * t), -0.8)).astype(np.complex64)
    yi = run(ib, full, 480)
    check('impulse blanker: +125% peaks (the legal limit) untouched',
          np.array_equal(yi[ib.delay:], full[:-ib.delay]), f'{ib.n_blanked} blanked')

    print('Pulses on a station')
    p = pulses(len(x), 4801, 3.0)                     # 30 x the carrier, 200 a second
    c = nb(k=5.6)
    y = run(c, x + p)
    clean = x[:-D]
    err = y[D:] - clean
    before = np.mean(np.abs(p[:-D]) ** 2)
    after = np.mean(np.abs(err) ** 2)
    check('noise blanker patches them: what is left 30 dB under the pulses',
          db(after) < db(before) - 30, f'{db(before) - db(after):.1f} dB down')
    n_p = int(np.sum(np.abs(p[::1]) > 0) // 3)
    check('and counts every one', c.n_pulses == n_p, f'{c.n_pulses} of {n_p}')
    sig = np.mean(np.abs(clean) ** 2)
    check('the patch follows the station across the gap (error 25 dB under it)',
          db(after) < db(sig) - 25, f'{db(sig) - db(after):.1f} dB under the station')
    ch = np.abs(y[D:] - clean) > 1e-6
    check('only the pulses and their guard samples are touched',
          ch.sum() <= n_p * (3 + 3 + 6 + 2), f'{int(ch.sum())} samples, {100 * ch.mean():.2f}%')

    print('Chopped up differently')
    ys = [run(nb(k=5.6), x + p, ch) for ch in (9600, 4096, 777)]
    sizes = iter(rng.integers(1, 20000, 10000))
    ys.append(run(nb(k=5.6), x + p, lambda: int(next(sizes))))
    check('the same output in 9600, 4096, 777 and random chunks',
          all(np.array_equal(ys[0], v) for v in ys[1:]))

    print('Not a pulse')
    burst = np.zeros(len(x), np.complex64)
    i0 = 100_000
    burst[i0:i0 + 1000] = 2.0                          # 1 ms, 20 x the carrier: a signal arriving
    i1 = 9600 * 20 - 30                                # and one across a chunk boundary
    burst[i1:i1 + 200] = 2.0
    c = nb(k=4.0)
    y = run(c, x + burst)
    check('a 1 ms burst is left alone', np.array_equal(y[D:], (x + burst)[:-D]),
          f'{c.n_blanked} blanked')
    c = nb(k=4.0)
    y = run(c, x + burst, 64)
    check('also chopped into 64-sample pieces (runs carried across calls)',
          np.array_equal(y[D:], (x + burst)[:-D]), f'{c.n_blanked} blanked')

    n = 2 * fs
    t = np.arange(n) / fs
    hiss = (0.004 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    for rise, at in ((960, 1.0171), (1, 1.0171), (960, 1.0 + (9600 * 3 - 30) / fs)):
        k0 = int(at * fs)
        edge = np.clip((np.arange(n) - k0) / rise, 0, 1)
        keyed = (0.3 * np.sin(0.5 * np.pi * edge) ** 2 * np.exp(2j * np.pi * 1100 * t) + hiss).astype(np.complex64)
        c = nb(k=5.6)
        run(c, keyed)
        check(f'a strong signal keyed on out of the noise ({rise} sample edge), MED: untouched',
              c.n_blanked == 0, f'{c.n_blanked} blanked')
    c = nb(k=4.0)
    run(c, hiss)
    check('noise alone, HIGH: a chance crossing now and then, no more', c.n_blanked / n < 2e-4,
          f'{c.n_pulses / 2:.0f} a second, {100 * c.n_blanked / n:.3f}% of the time')

    print('Too keen')
    dense = pulses(len(x) * 6, 120, 0.7, width=4)      # 3.3% of the time, 7 x the carrier
    xx = np.tile(x, 6)
    c = nb(k=4.0)
    run(c, xx + dense)
    check('blanking 3% of the time, it backs off to under 1%', c.frac < 0.012 and c.backoff > 1.5,
          f'{100 * c.frac:.2f}% now, threshold x{c.backoff:.2f}')

    print('Impulses (HF: big pulses counted apart)')
    c = BlankerCore(fs, pre=3, post=6, max_run=48)
    c.set_k(5.6, k_big=16.0)
    small = pulses(len(x), 9601, 0.9)                  # 9 x: noise
    big = pulses(len(x), 9601, 4.0, phase0=4800)       # 40 x: impulses
    run(c, x + small + big)
    nb_, ns_ = int(np.sum(np.abs(big) > 0) // 3), int(np.sum(np.abs(small) > 0) // 3)
    check('every pulse patched, the big ones counted as impulses',
          c.n_pulses == nb_ + ns_ and c.n_big == nb_, f'{c.n_pulses} pulses, {c.n_big} big')
    c = BlankerCore(fs, pre=3, post=6, max_run=48)
    c.set_k(None, k_big=16.0)
    y = run(c, x + small)
    check('the impulse line alone: small pulses pass', c.n_pulses == 0)
    c.set_k(16.0, k_big=16.0)
    c2 = BlankerCore(fs, pre=3, post=6, max_run=48)
    c2.set_k(16.0, k_big=16.0)
    run(c2, x + small + big)
    check('the impulse line alone: big pulses patched', c2.n_pulses == nb_ and c2.n_big == nb_,
          f'{c2.n_pulses}')

    print('Rates')
    r = EventRate(15.0)
    for _ in range(6000):
        r.add(1 if _ % 50 == 0 else 0, 0.01)            # 2 a second for a minute
    check('a steady 2 a second reads 120 a minute', abs(r.per_min - 120) < 5, f'{r.per_min:.1f}')
    r = EventRate(15.0)
    r.add(1, 0.01)
    check('one event reads 4 a minute at once', abs(r.per_min - 4.0) < 0.01, f'{r.per_min:.2f}')

    print('Stereo glitch gate')
    fs2, lead = 48_000, 512
    n = fs2
    t = np.arange(n) / fs2
    lr_now = (0.3 * np.sin(2 * np.pi * 1100 * t)).astype(np.float32)
    lr = np.concatenate((np.zeros(lead, np.float32), lr_now[:-lead]))   # lr runs `lead` behind
    env = (1.0 + 0.5 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    ph = (0.2 * np.sin(2 * np.pi * 1100 * t)).astype(np.float32)
    g = GlitchGateCore(fs2, lead)
    g.on, g.counting = True, True
    out = np.concatenate([g.process(lr[i:i + 480], env[i:i + 480], ph[i:i + 480])
                          for i in range(0, n, 480)])
    check('no glitch: the difference signal passes exactly', np.array_equal(out, lr))
    env2 = env.copy()
    k0 = 20_000
    env2[k0:k0 + 24] = 0.02                            # the carrier all but gone for 0.5 ms
    ph2 = ph.copy()
    ph2[30_000:30_096] += 1.5                          # and a 2 ms phase hit
    g = GlitchGateCore(fs2, lead)
    g.on, g.counting = True, True
    out = np.concatenate([g.process(lr[i:i + 480], env2[i:i + 480], ph2[i:i + 480])
                          for i in range(0, n, 480)])
    m = k0 + lead                                      # where the dip is in the lr stream
    shut = np.abs(out[m - 20:m + 40]).max() < 1e-6
    away = np.array_equal(out[:m - 200], lr[:m - 200]) and \
        np.array_equal(out[m + 300:30_000 + lead - 200], lr[m + 300:30_000 + lead - 200]) and \
        np.array_equal(out[30_096 + lead + 300:], lr[30_096 + lead + 300:])
    hit = np.abs(out[30_000 + lead:30_096 + lead]).max() < 1e-6
    check('a dip in the carrier: muted from just before it to just after', shut and away)
    check('a phase hit (beyond what C-QUAM sends): muted', hit)
    check('both glitches counted', g.n_glitches == 2, f'{g.n_glitches}')
    edge = out[m - 60:m - 40]
    check('the gate closes on a ramp (no click)', np.all(np.abs(edge) <= np.abs(lr[m - 60:m - 40]) + 1e-7)
          and np.abs(edge).max() > 0)
    g = GlitchGateCore(fs2, lead)
    g.on, g.counting = False, True
    out = np.concatenate([g.process(lr[i:i + 480], env2[i:i + 480], ph2[i:i + 480])
                          for i in range(0, n, 480)])
    check('off: untouched', np.array_equal(out, lr) and g.n_glitches == 0)
    g = GlitchGateCore(fs2, lead)
    g.on, g.counting = True, False
    out = np.concatenate([g.process(lr[i:i + 480], env2[i:i + 480], ph2[i:i + 480])
                          for i in range(0, n, 480)])
    check('muted but not counted while the stereo is not heard', g.n_glitches == 0
          and np.abs(out[m - 20:m + 40]).max() < 1e-6)

    print()
    print('all passed' if not failed else f'{failed} FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
