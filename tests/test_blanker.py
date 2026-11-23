#!/usr/bin/env python3
"""
test_blanker.py - the impulse and noise blankers (radio/blanker.py). Needs neither GNU Radio
nor a dongle.

    python3 tests/test_blanker.py

Off, or with nothing to catch, a blanker passes the signal through untouched (only late by its
look-ahead). On one station, pulses are patched far below themselves and counted. On a crowded
band, with stations 30 dB stronger than the one listened to sharing the dongle's bandwidth
(as on medium wave), the pulses that crackle in that station's channel hide under the strong
stations at full bandwidth; the noise blanker finds them in what its prediction of the band
misses, and its patches carry the strong stations through, so the channel comes out 10 dB
cleaner, while a band with no pulses is left exactly alone. A burst longer than a pulse and a
signal keyed on are left alone, even across the flowgraph's chunks; set too keen on dense
noise it backs itself off; and the stereo glitch gate mutes the difference signal around a
carrier glitch only.
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


def pulses(n, every, height, width=3, phase0=100):
    """Spikes `height` tall, `width` samples wide, every `every` samples."""
    p = np.zeros(n, np.complex64)
    for i in range(phase0, n - width, every):
        p[i:i + width] = height * np.exp(1j * rng.uniform(0, 2 * np.pi))
    return p

def fftconv(x, h):
    n, m = len(x), len(h)
    nfft = 1 << int(math.ceil(math.log2(n + m - 1)))
    y = np.fft.ifft(np.fft.fft(x, nfft) * np.fft.fft(h, nfft))[:n + m - 1]
    return y[(m - 1) // 2:(m - 1) // 2 + n].astype(np.complex64)


def lowpass(fc, fs, n):
    h = 2 * fc / fs * np.sinc(2 * fc / fs * (np.arange(n) - (n - 1) / 2)) * np.kaiser(n, 8.0)
    return h / np.sum(h)


def channel(y, fs=960_000, off_hz=-20e3):
    """The 5 kHz channel around off_hz, at 48 kHz, as a receiver hears it."""
    z = y * np.exp(-2j * np.pi * off_hz * np.arange(len(y)) / fs).astype(np.complex64)
    z = fftconv(z, lowpass(40e3, fs, 101))[::10]
    z = fftconv(z, lowpass(14e3, fs / 10, 63))[::2]
    return fftconv(z, lowpass(5e3, fs / 20, 97))


def crowded(secs, fs=960_000, local_db=30.0, pulse_db=50.0, per_s=120):
    """A medium-wave band as an RTL sends it: a weak station tuned (carrier 1, 20 kHz below the
    LO), a dozen others, two locals local_db stronger, a little noise; and, apart, pulses
    pulse_db over the tuned carrier, 120 bursts a second, shaped by a decimation filter."""
    n = int(secs * fs)
    t = np.arange(n) / fs
    prog = lambda: 1 + 0.3 * np.sin(2 * np.pi * rng.uniform(300, 3000) * t + rng.uniform(0, 6))  # noqa: E731
    x = prog() * np.exp(-2j * np.pi * 20e3 * t)
    for off in rng.choice(np.arange(-460e3, 470e3, 10e3), 12, replace=False):
        if abs(off + 20e3) > 15e3:
            x = x + 10 ** (rng.uniform(-10, 15) / 20) * prog() * np.exp(2j * np.pi * (off * t + rng.random()))
    for off, db_ in ((180e3, local_db), (-310e3, local_db - 6)):
        x = x + 10 ** (db_ / 20) * prog() * np.exp(2j * np.pi * (off * t + rng.random()))
    x = x + 0.03 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    h = np.sinc(np.arange(-12, 13) * 0.85) * np.hanning(25)
    p = np.zeros(n, np.complex128)
    for t0 in np.arange(0.003, secs - 0.01, 1 / per_s):
        for _ in range(rng.integers(1, 4)):
            i = int((t0 + rng.uniform(0, 40e-6)) * fs)
            p[i - 12:i + 13] += 10 ** (pulse_db / 20) * rng.uniform(0.5, 1.0) * h * np.exp(1j * rng.uniform(0, 6.3))
    scale = 0.01                                        # the tuned carrier at -40 dBFS
    return (x * scale).astype(np.complex64), (p * scale).astype(np.complex64)


def channel_error_db(y, delay, clean_ch):
    """What's wrong in the channel, against the station's program, in dB."""
    c = channel(np.concatenate((y[delay:], np.zeros(delay, np.complex64))))
    s = slice(4800, len(c) - 4800)
    prog = clean_ch[s] - np.mean(clean_ch[s])
    return db(np.mean(np.abs(c[s] - clean_ch[s]) ** 2)) - db(np.mean(np.abs(prog) ** 2))


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
    c = BlankerCore(fs, pre=2, post=4, max_run=48, order=24, backoff=True, **kw)
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
    ref = c.ref                                        # the prediction's median miss here
    check('on (HIGH), a clean station: passes exactly', np.array_equal(y[D:], x[:-D]),
          f'{c.n_blanked} samples blanked')
    ib = BlankerCore(48_000, pre=4, post=8, max_run=96, detect='level', order=16)
    ib.set_k(4.0)
    t = np.arange(48_000) / 48_000
    full = (1.0 + 1.25 * np.maximum(np.sin(2 * np.pi * 1100 * t), -0.8)).astype(np.complex64)
    yi = run(ib, full, 480)
    check('impulse blanker: +125% peaks (the legal limit) untouched',
          np.array_equal(yi[ib.delay:], full[:-ib.delay]), f'{ib.n_blanked} blanked')
    crash = np.zeros(len(full), np.complex64)
    crash[20_000:20_030] = 8.0                         # a crash, 0.6 ms at +700%
    ib = BlankerCore(48_000, pre=4, post=8, max_run=96, detect='level', order=16)
    ib.set_k(4.0)
    yi = run(ib, full + crash, 480)
    left = np.abs(yi[ib.delay:] - full[:-ib.delay])[19_900:20_200].max()
    check('impulse blanker: a static crash patched, the program carried through', left < 0.5,
          f'{ib.n_pulses} patched, {left:.2f} left of 8')

    print('Pulses on one station')
    p = pulses(len(x), 4801, 3.0)                     # 30 x the carrier, 200 a second
    c = nb(k=5.6)
    y = run(c, x + p)
    clean = x[:-D]
    err = y[D:] - clean
    before = np.mean(np.abs(p[:-D]) ** 2)
    after = np.mean(np.abs(err) ** 2)
    check('noise blanker patches them: what is left 30 dB under the pulses',
          db(after) < db(before) - 30, f'{db(before) - db(after):.1f} dB down')
    n_p = int(np.sum(np.abs(p) > 0) // 3)
    check('and counts every one', c.n_pulses == n_p, f'{c.n_pulses} of {n_p}')
    sig = np.mean(np.abs(clean) ** 2)
    check('the patch carries the station across the gap (error 30 dB under it)',
          db(after) < db(sig) - 30, f'{db(sig) - db(after):.1f} dB under the station')
    ch = np.abs(y[D:] - clean) > 1e-6
    check('only the pulses and their guard samples are touched',
          ch.sum() <= n_p * (3 + 2 + 4 + 2), f'{int(ch.sum())} samples, {100 * ch.mean():.2f}%')

    print('A crowded band: locals 30 dB stronger than the station listened to')
    xb, pb = crowded(0.6, pulse_db=42.0)
    clean_ch = channel(xb)
    off = channel_error_db(xb + pb, 0, clean_ch)
    pk = 20 * math.log10(float(np.abs(pb).max()) / float(np.median(np.abs(xb))))
    check('the pulses crackle in its channel, but hide under the locals at full bandwidth',
          off > -10 and pk < 12, f'{off:+.1f} dB against the program; tallest {pk:.1f} dB over the band')
    for lv, k in (('MED', 5.6), ('HIGH', 4.0)):
        c = nb(k=k)
        y = run(c, xb + pb)
        got = channel_error_db(y, c.delay, clean_ch)
        check(f'noise blanker {lv}: the channel 10 dB cleaner', got < off - 10,
              f'{got:+.1f} dB, {c.n_pulses / 0.6:.0f} pulses a second')
    c = nb(k=4.0)
    y = run(c, xb)
    check('no pulses, HIGH: the band left exactly alone', np.array_equal(y[c.delay:], xb[:-c.delay]),
          f'{c.n_blanked} samples blanked')

    print('Chopped up differently')
    ys, ns = [], []
    sizes = iter(rng.integers(1, 20000, 10000))
    for chunk in (9600, 4096, 777, lambda: int(next(sizes))):
        c = nb(k=5.6)
        ys.append(run(c, x + p, chunk))
        ns.append(c.n_pulses)
    dev = max(db(np.mean(np.abs(v - ys[0]) ** 2)) for v in ys[1:]) - db(sig)
    check('9600, 4096, 777 and random chunks: the same pulses, the same patches',
          len(set(ns)) == 1 and dev < -40, f'{ns[0]} pulses; outputs within {dev:.0f} dB')

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
    check('also chopped into 64-sample pieces', np.array_equal(y[D:], (x + burst)[:-D]),
          f'{c.n_blanked} blanked')

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
    dense = pulses(len(x) * 6, 120, 8 * ref, width=4)  # 3.3% of the time
    xx = np.tile(x, 6)
    c = nb(k=4.0)
    run(c, xx + dense)
    check('blanking 3% of the time, it backs off to under 1%', c.frac < 0.012 and c.backoff > 1.5,
          f'{100 * c.frac:.2f}% now, line x{c.backoff:.2f}')

    print('Impulses (HF: big pulses counted apart)')
    small = pulses(len(x), 9601, 10 * ref)             # 20 dB over the median miss: noise
    big = pulses(len(x), 9601, 50 * ref, phase0=4800)  # 34 dB: impulses
    nb_, ns_ = int(np.sum(np.abs(big) > 0) // 3), int(np.sum(np.abs(small) > 0) // 3)
    c = nb(k=5.6, gate=0.0)
    c.set_k(5.6, k_big=16.0)
    run(c, x + small + big)
    check('every pulse patched, the big ones counted as impulses',
          c.n_pulses == nb_ + ns_ and c.n_big == nb_, f'{c.n_pulses} pulses, {c.n_big} big')
    c = nb(k=16.0)
    c.set_k(16.0, k_big=16.0)
    run(c, x + small)
    check('the impulse line alone: small pulses pass', c.n_pulses == 0, f'{c.n_pulses}')
    c = nb(k=16.0)
    c.set_k(16.0, k_big=16.0)
    run(c, x + small + big)
    check('the impulse line alone: big pulses patched', c.n_pulses == nb_ and c.n_big == nb_,
          f'{c.n_pulses}')

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
