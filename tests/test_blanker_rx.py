#!/usr/bin/env python3
"""
test_blanker_rx.py - the impulse and noise blankers in the real AM and HF receivers, in real
GNU Radio. Needs GNU Radio, not a dongle.

    python3 tests/test_blanker_rx.py

Pretend signals go in where the dongle would, at its full 960 ksps and in real time, with
sharp noise pulses (three samples long, 100 a second, nearly full scale) added there, as an
ignition or an electric fence would; a silent sound card keeps what comes out:

  - AM: the clicks in the audio with the blankers off, with the impulse blanker, and with the
    noise blanker (which must bring them down to the station's own noise); a clean station
    and a fully modulated one: nothing blanked; the readings the Advanced window shows
  - AM stereo: the stereo difference signal comes through the glitch gate unchanged, and a
    carrier that all but vanishes for half a millisecond is muted and counted
  - HF (USB): the same clicks with the blankers off, the impulse blanker and the noise
    blanker; a strong signal keyed on out of silence isn't taken for a pulse
  - the audio still reaches the sound card steadily

If GNU Radio isn't installed, it says so and stops.
"""
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np

try:
    from gnuradio import gr, blocks, analog, filter
except ImportError:
    print('GNU Radio is not installed here, so there is nothing to test.')
    sys.exit(0)

failed = 0
FS = 48000
RATE = 960_000
SECS = 5.0
rng = np.random.default_rng(11)


def check(name, ok, detail=''):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:58s} {detail}")


def pulse_train(height, per_s=100, n=RATE // 10):
    """0.1 s of the dongle's samples (repeated): three-sample spikes at random phases."""
    p = np.zeros(n, np.complex64)
    for i in range(n // per_s // 2, n - 3, RATE // per_s):
        p[i:i + 3] = height * np.array([1.0, 0.8, 0.5]) * np.exp(1j * rng.uniform(0, 2 * np.pi))
    return p


class Dongle(gr.hier_block2):
    """Stands in for osmosdr.source: x (baseband around the dial, at 48 kHz) LO_OFFSET below the
    LO at 960 ksps, plus white noise (power per Hz) and a repeating pulse train, in real time."""

    def __init__(self, x, n0, pulses):
        gr.hier_block2.__init__(self, 'pulsy_station', gr.io_signature(0, 0, 0),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.src = blocks.vector_source_c(x.astype(np.complex64).tolist(), False)
        taps = filter.firdes.low_pass(RATE // FS, RATE, 20e3, 4e3)
        self.up = filter.interp_fir_filter_ccf(RATE // FS, taps)
        self.lo = analog.sig_source_c(RATE, analog.GR_COS_WAVE, 0.0, 1.0)
        self.mix, self.add = blocks.multiply_cc(), blocks.add_cc(1)
        self.noise = analog.noise_source_c(analog.GR_GAUSSIAN, math.sqrt(n0 * RATE), 5)
        self.thr = blocks.throttle(gr.sizeof_gr_complex, RATE)
        self.connect(self.src, self.up, (self.mix, 0))
        self.connect(self.lo, (self.mix, 1))
        self.connect(self.mix, (self.add, 0))
        self.connect(self.noise, (self.add, 1))
        if pulses is not None:
            self.pulses = blocks.vector_source_c(pulses.tolist(), True)
            self.connect(self.pulses, (self.add, 2))
        self.connect(self.add, self.thr, self)
        self.station_hz = self.centre = 0.0

    def tune(self, station_hz):
        self.station_hz = station_hz

    def set_center_freq(self, f, *a):
        self.centre = f
        self.lo.set_frequency(self.station_hz - self.centre)
        return f

    def get_gain_range(self, *a):
        raise RuntimeError('none')

    def __getattr__(self, name):               # the other osmosdr settings: accepted
        if name.startswith('__'):
            raise AttributeError(name)
        try:                # GNU Radio's own methods first: 3.10.1 finds them (to_basic_block...) here
            return getattr(object.__getattribute__(self, '_impl'), name)
        except AttributeError:
            return lambda *a, **k: 0


class Speaker(gr.hier_block2):
    def __init__(self):
        gr.hier_block2.__init__(self, 'speaker', gr.io_signature(2, 2, gr.sizeof_float),
                                gr.io_signature(0, 0, 0))
        self.left, self.right = blocks.vector_sink_f(), blocks.vector_sink_f()
        self.connect((self, 0), self.left)
        self.connect((self, 1), self.right)

    def nitems_read(self, port):
        return self.left.nitems_read(0)


def play(band, x, n0, pulses=None, impulse=False, noise='off', freq=None, setup=None):
    """Run the AM or HF receiver on x; returns (receiver, left, right, stats at the end)."""
    import radio.addons
    radio.addons._entry_points = lambda: []
    if band == 'AM':
        from radio.am import AMReceiver as Base
        freq = freq or 1080e3
    else:
        from radio.hf import HFReceiver as Base
        freq = freq or 14.2e6

    class Rx(Base):
        def _make_source(self, args):
            d = Dongle(x, n0, pulses)
            d.tune(freq)
            return d

        def _make_audio_sink(self, rate, device):
            return Speaker()

    kw = dict(mode='USB') if band == 'HF' else {}
    rx = Rx(freq=freq, agc=False, am_source='native', **kw)
    rx.set_blankers(impulse=impulse, noise=noise)
    rx.set_output_gain(0.5, 0.5)
    rx.set_tone(0, 0)
    rx.set_mute(False)
    if setup:
        setup(rx)
    flow, errors = [], []
    rx.start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < len(x) / FS - 0.3:
        flow.append((time.monotonic() - t0, rx.audio_sink.left.nitems_read(0)))
        try:
            rx.update_blend()
        except Exception as e:
            errors.append(f'{type(e).__name__}: {e}')
        time.sleep(0.02)
    stats = rx.blanker_stats()
    rx.stop()
    rx.wait()
    rx.errors, rx.steady_ms = errors, steadiness_ms(flow)
    return rx, np.asarray(rx.audio_sink.left.data()), np.asarray(rx.audio_sink.right.data()), stats


def steadiness_ms(flow, rate=48000, after=1.5):
    t = np.array([f[0] for f in flow if f[0] > after])
    n = np.array([f[1] for f in flow if f[0] > after], float)
    if len(t) < 10:
        return float('inf')
    lead = n - rate * t
    return float(lead.max() - lead.min()) / rate * 1e3


def residual_db(a, hz, start=2.5):
    """What's left of the audio once the steady tone at hz is taken out: noise and clicks."""
    y = np.asarray(a[int(start * FS):], np.float64)
    if len(y) < FS:
        return 0.0
    t = np.arange(len(y)) / FS
    m = np.column_stack((np.sin(2 * np.pi * hz * t), np.cos(2 * np.pi * hz * t), np.ones_like(t)))
    coef, *_ = np.linalg.lstsq(m, y, rcond=None)
    r = y - m @ coef
    return 10 * math.log10(max(float(np.mean(r ** 2)), 1e-20))


def peak_click_db(a, hz, start=2.5):
    """The loudest moment left once the steady tone is taken out."""
    y = np.asarray(a[int(start * FS):], np.float64)
    t = np.arange(len(y)) / FS
    m = np.column_stack((np.sin(2 * np.pi * hz * t), np.cos(2 * np.pi * hz * t), np.ones_like(t)))
    coef, *_ = np.linalg.lstsq(m, y, rcond=None)
    return 20 * math.log10(max(float(np.max(np.abs(y - m @ coef))), 1e-12))


def tone_db(a, hz, start=2.5):
    y = np.asarray(a[int(start * FS):], np.float64)
    w = np.hanning(len(y))
    Y = np.abs(np.fft.rfft(y * w))
    f = np.fft.rfftfreq(len(y), 1 / FS)
    return 20 * math.log10(max(2 * float(np.max(Y[np.abs(f - hz) < 5])) / np.sum(w), 1e-12))


def am_station(carrier, m=0.5, hz=1100.0, secs=SECS, peaks=None):
    t = np.arange(int(secs * FS)) / FS
    s = np.sin(2 * np.pi * hz * t)
    if peaks:                                   # asymmetric: +peaks up, -100% down
        s = np.where(s > 0, peaks * s, s)
        return carrier * (1 + s)
    return carrier * (1 + m * s)


def main():
    from radio.am import AMReceiver
    print(f'GNU Radio {gr.version()}')
    tone = 1100.0

    print('AM: a weak station (-40 dBFS, 30 dB C/N) with noise pulses at 100 a second')
    car = 0.01
    n0 = car ** 2 * 10 ** (-30 / 10) / 1e4
    x = am_station(car)
    p = pulse_train(0.9)
    rx, a, _, st = play('AM', x, n0, None, impulse=True, noise='high')
    base = residual_db(a, tone)
    check('a clean station: nothing blanked, impulse and noise HIGH',
          rx.ib.core.n_blanked == 0 and rx.nb.core.n_blanked == 0,
          f'{rx.ib.core.n_blanked} + {rx.nb.core.n_blanked} samples')
    check('its updates run without errors', not rx.errors, '; '.join(rx.errors[:2]))
    # (AM's flow was 70 to 85 ms lumpy before the blankers, from the Kahn stereo decoder's FFT
    # filter; HF's, below, is under 45)
    check('the audio flows no lumpier than before (under 100 ms)', rx.steady_ms < 100,
          f'{rx.steady_ms:.0f} ms')
    rx, a, _, st = play('AM', x, n0, p, impulse=False, noise='off')
    off = residual_db(a, tone)
    check('blankers off: the pulses are loud clicks', off > base + 10,
          f'{off - base:+.1f} dB over the station\'s own noise')
    rx, a, _, st = play('AM', x, n0, p, impulse=True, noise='off')
    ib = residual_db(a, tone)
    check('impulse blanker: the clicks well down', ib < off - 10, f'{ib - base:+.1f} dB')
    check('and it counts them', st[0] > 500 and rx.ib.core.n_pulses > 300,
          f'{st[0]:.0f} a minute so far, {rx.ib.core.n_pulses} in all')
    rx, a, _, st = play('AM', x, n0, p, impulse=False, noise='med')
    nb = residual_db(a, tone)
    check('noise blanker (MED): the clicks gone, down to the noise', nb < base + 3, f'{nb - base:+.1f} dB')
    check('and its reading: the share of the time it patched', 0.05 < st[1] < 0.5,
          f'{st[1]:.3f}% (pulses {rx.nb.core.n_pulses})')
    check('the tone untouched', abs(tone_db(a, tone) - tone_db(play('AM', x, n0)[1], tone)) < 0.3)

    print('AM: a strong station, +125% peaks')
    x = am_station(0.1, peaks=1.25)
    rx, a, _, st = play('AM', x, 0.1 ** 2 * 1e-5 / 1e4, None, impulse=True, noise='high')
    check('nothing blanked by either', rx.ib.core.n_blanked == 0 and rx.nb.core.n_blanked == 0,
          f'{rx.ib.core.n_blanked} + {rx.nb.core.n_blanked} samples')

    print('AM stereo (C-QUAM, 25 Hz pilot): the glitch gate')
    t = np.arange(int(SECS * FS)) / FS
    lr = 0.3 * np.sin(2 * np.pi * tone * t)                    # L-R only: L = -R
    env = 1.0
    ph = np.arctan(lr / env) + 0.04 * np.sin(2 * np.pi * 25 * t)
    cq = 0.1 * env * np.exp(1j * ph)
    n0 = 0.1 ** 2 * 10 ** (-40 / 10) / 1e4
    stereo = lambda rx: rx.set_stereo_system('cquam')     # noqa: E731
    rx, l1, r1, _ = play('AM', cq, n0, None, impulse=False, setup=stereo)
    gate_off = tone_db(l1 - r1, tone)
    on1 = rx.stereo_indicator
    rx, l2, r2, st = play('AM', cq, n0, None, impulse=True, setup=stereo)
    gate_on = tone_db(l2 - r2, tone)
    check('in stereo, the difference signal through the gate unchanged',
          on1 and rx.stereo_indicator and abs(gate_on - gate_off) < 0.2,
          f'{gate_on - gate_off:+.2f} dB, stereo {rx.stereo_indicator}')
    dips = np.ones(len(t))
    for k in range(int(3.0 * FS), len(t) - FS // 2, FS // 4):  # from 3 s, 4 a second: the
        dips[k:k + 24] = -0.3 * np.sin(np.pi * np.arange(24) / 24)   # envelope driven through
    rx, l3, r3, st = play('AM', cq * dips, n0, None, impulse=True, setup=stereo)   # nothing
    check('over-modulation (the carrier turned over for 0.5 ms): muted, counted',
          rx.glitch.core.n_glitches >= 5 and st[2] > 10,
          f'{rx.glitch.core.n_glitches} glitches, {st[2]:.0f} a minute')
    rx, l4, r4, st = play('AM', cq * dips, n0, None, impulse=False, setup=stereo)
    d_on = peak_click_db(l3 - r3, tone, start=3.0)
    d_off = peak_click_db(l4 - r4, tone, start=3.0)
    check('... and the clicks they leave in L-R are smaller for it', d_on < d_off - 6,
          f'loudest {d_on - d_off:+.1f} dB')

    print('HF (USB): a tone 50 dB over the noise in 2.7 kHz, noise pulses at 100 a second')
    a1 = 0.01
    n0 = a1 ** 2 / (10 ** 5 * 2700)
    x = a1 * np.exp(2j * np.pi * tone * np.arange(int(SECS * FS)) / FS)
    rx, a, _, st = play('HF', x, n0, None, impulse=True, noise='high')
    base = residual_db(a, tone)
    check('a clean signal: nothing blanked (impulse, noise HIGH)', rx.nb.core.n_blanked == 0,
          f'{rx.nb.core.n_blanked} samples')
    check('its updates run without errors', not rx.errors, '; '.join(rx.errors[:2]))
    check('the audio flows steadily (under 45 ms of lumps)', rx.steady_ms < 45, f'{rx.steady_ms:.0f} ms')
    rx, a, _, st = play('HF', x, n0, p, impulse=False, noise='off')
    off = residual_db(a, tone)
    check('blankers off: the pulses are loud clicks', off > base + 15, f'{off - base:+.1f} dB')
    rx, a, _, st = play('HF', x, n0, p, impulse=True, noise='off')
    ib = residual_db(a, tone)
    check('impulse blanker: the clicks gone (these pulses are huge)', ib < base + 3, f'{ib - base:+.1f} dB')
    check('counted as impulses', st[0] > 500, f'{st[0]:.0f} a minute so far')
    small = pulse_train(0.12)                    # about 22 dB over the band: under the impulse line
    rx, a, _, st = play('HF', x, n0, small, impulse=True, noise='off')
    ib_small = residual_db(a, tone)
    rx, a, _, st = play('HF', x, n0, small, impulse=True, noise='med')
    nb = residual_db(a, tone)
    check('smaller pulses: the impulse blanker leaves them, the noise blanker takes them',
          nb < base + 3 and ib_small > nb + 6, f'{ib_small - base:+.1f} dB, then {nb - base:+.1f} dB')
    check('and its reading', 0.05 < st[1] < 0.5, f'{st[1]:.3f}%')
    k0, rise = int(2.517 * FS), FS // 1000                    # keyed on with a 1 ms edge
    edge = np.clip((np.arange(len(x)) - k0) / rise, 0, 1)
    keyed = 0.3 * np.sin(0.5 * np.pi * edge) ** 2 * x / a1
    rx, a, _, st = play('HF', keyed, n0, None, impulse=True, noise='med')
    check('a strong signal keyed on out of the noise: not a pulse', rx.nb.core.n_blanked == 0,
          f'{rx.nb.core.n_blanked} samples blanked')

    print()
    print('all passed' if not failed else f'{failed} FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
