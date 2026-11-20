#!/usr/bin/env python3
"""
test_hf.py - the HF receiver (SSB, CW, synchronous AM) in real GNU Radio. Needs GNU Radio, not
a dongle.

    python3 tests/test_hf.py

Pretend signals go in where the dongle would, in real time, into the real HFReceiver with a
silent sound card that keeps what it was sent:

  - USB and LSB: a voice tone comes out at its frequency and the AGC's level, a tone on the
    other sideband stays out (70 dB down), the S/N reading fits what was sent
  - CW: a carrier on the dial plays at the 600 Hz pitch; one 700 Hz away stays out of 500 Hz
  - synchronous AM, 30 Hz off frequency: the carrier found and locked, the tone heard, and an
    interferer on one side heard only when listening to that side
  - Width: a 2500 Hz tone passes at 2.7 kHz and not at 1.8; changing mode and width while it
    runs, without a restart
  - the AGC: a strong carrier keyed on comes out without overshoot; an empty channel plays
    its noise about 20 dB under a station

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
F0 = 14.2e6
VOL, HEADROOM_DB = 0.5, -1.0                 # the test's volume, the tone controls' headroom


def check(name, ok, detail):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:50s} {detail}")


def tones(secs, parts, t0=0.0):
    """Complex baseband around the dial: [(Hz from the dial, amplitude)]."""
    t = t0 + np.arange(int(secs * FS)) / FS
    x = np.zeros(len(t), np.complex128)
    for hz, a in parts:
        x += a * np.exp(2j * np.pi * hz * t)
    return x


def am_station(secs, offset=0.0, carrier=0.05, m=0.5, hz=1100.0):
    t = np.arange(int(secs * FS)) / FS
    return carrier * (1 + m * np.cos(2 * np.pi * hz * t)) * np.exp(2j * np.pi * offset * t)


class Dongle(gr.hier_block2):
    """Stands in for osmosdr.source: x (baseband around the dial) at the dial, LO_OFFSET below the
    tuned LO, at 960 ksps in real time, plus white noise of the given power per Hz."""

    def __init__(self, x, rate, n0):
        gr.hier_block2.__init__(self, 'hf_station', gr.io_signature(0, 0, 0),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.src = blocks.vector_source_c(x.astype(np.complex64).tolist(), False)
        self.up = filter.rational_resampler_ccc(rate // FS, 1)
        self.lo = analog.sig_source_c(rate, analog.GR_COS_WAVE, 0.0, 1.0)
        self.mix, self.add = blocks.multiply_cc(), blocks.add_cc()
        self.noise = analog.noise_source_c(analog.GR_GAUSSIAN, math.sqrt(n0 * rate), 5)
        self.thr = blocks.throttle(gr.sizeof_gr_complex, rate)
        self.connect(self.src, self.up, (self.mix, 0))
        self.connect(self.lo, (self.mix, 1))
        self.connect(self.mix, (self.add, 0))
        self.connect(self.noise, (self.add, 1))
        self.connect(self.add, self.thr, self)
        self.station_hz = self.centre = F0

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
        self.left, self.right = blocks.vector_sink_f(), blocks.null_sink(gr.sizeof_float)
        self.connect((self, 0), self.left)
        self.connect((self, 1), self.right)

    def nitems_read(self, port):
        return self.left.nitems_read(0)


def play(x, mode, width=None, n0=1e-12, actions=(), watch=()):
    """Run the receiver on x; actions: [(seconds, fn(rx))] done while it runs."""
    from radio.hf import HFReceiver
    import radio.addons
    radio.addons._entry_points = lambda: []

    class Rx(HFReceiver):
        def _make_source(self, args):
            return Dongle(x, self.samp_rate, n0)

        def _make_audio_sink(self, rate, device):
            return Speaker()

    rx = Rx(freq=F0, agc=False, am_source='native', mode=mode, width=width)
    rx.set_output_gain(VOL, VOL)
    rx.set_tone(0, 0)
    rx.set_mute(False)
    secs = len(x) / FS - 0.3
    todo, seen, errors = sorted(actions, key=lambda a: a[0]), [], []
    rx.start()
    t0 = time.monotonic()
    marks = []
    while time.monotonic() - t0 < secs:
        now = time.monotonic() - t0
        while todo and now >= todo[0][0]:
            marks.append((now, rx.audio_sink.left.nitems_read(0) if False else len(rx.audio_sink.left.data())))
            todo.pop(0)[1](rx)
        try:
            rx.update_blend()
        except Exception as e:
            errors.append(f'{type(e).__name__}: {e}')
        if watch:
            seen.append((now,) + tuple(getattr(rx, a) for a in watch))
        time.sleep(0.02)
    rx.stop()
    rx.wait()
    rx.errors, rx.secs, rx.marks = errors, time.monotonic() - t0, marks
    return rx, np.asarray(rx.audio_sink.left.data()), seen


def level_db(a, hz):
    w = np.hanning(len(a))
    Y = np.abs(np.fft.rfft(a * w))
    f = np.fft.rfftfreq(len(a), 1 / FS)
    return 20 * math.log10(max(2 * float(np.max(Y[np.abs(f - hz) < 15])) / np.sum(w), 1e-12))


def peak_hz(a):
    w = np.hanning(len(a))
    Y = np.abs(np.fft.rfft(a * w))
    f = np.fft.rfftfreq(len(a), 1 / FS)
    return float(f[np.argmax(Y)])


def rms_db(a):
    return 20 * math.log10(max(float(np.sqrt(np.mean(np.asarray(a, np.float64) ** 2))), 1e-9))


def n0_for(amp, snr_db, bw):
    """Noise per Hz for a tone of this amplitude snr_db over the noise in bw."""
    return amp ** 2 / (10 ** (snr_db / 10) * bw)


def main():
    from radio.hf import HFReceiver
    ref = HFReceiver.AGC_REF
    want = 20 * math.log10(ref * VOL) + HEADROOM_DB          # a steady tone at the AGC's level
    print('GNU Radio', gr.version())

    print('USB: a 1000 Hz tone, one on the other sideband (1700 Hz below), 30 dB S/N')
    a_ = 0.02
    rx, a, _ = play(tones(5.0, [(1000, a_), (-1700, a_)]), 'USB', n0=n0_for(a_, 30, 2700))
    rate_in = rx._samples_in() / rx.secs
    check('samples from the dongle', abs(rate_in / rx.samp_rate - 1) < 0.1, f'{rate_in / 1e3:.0f}k a second')
    check('samples to the sound card', abs(len(a) / rx.secs / FS - 1) < 0.1, f'{len(a) / rx.secs / 1e3:.1f}k a second')
    check('updates run without errors', not rx.errors, rx.errors[0] if rx.errors else 'none')
    s = a[-2 * FS:]
    check('the tone comes out at 1000 Hz', abs(peak_hz(s) - 1000) < 5, f'{peak_hz(s):.0f} Hz')
    got = level_db(s, 1000)
    check("at the AGC's level", abs(got - want) < 1.5, f'{got:.1f} dB (expected {want:.1f})')
    rej = level_db(s, 1700) - got
    check('the other sideband stays out (down in the noise)', rej < -55, f'{rej:.0f} dB')
    check('S/N reads what was sent (30 dB)', abs(rx.cnr_db - 30) < 3, f'{rx.cnr_db:.1f} dB')
    dbfs, gain = rx.signal_level()
    want_lvl = 20 * math.log10(a_)
    check(f'level reads the tone ({want_lvl:.0f} dBFS)', abs(dbfs - want_lvl) < 1.0, f'{dbfs:.1f} dBFS, gain {gain}')

    print('LSB: the same, mirrored, at 50 dB S/N to see the filter itself')
    rx, a, _ = play(tones(4.0, [(-1000, a_), (1700, a_)]), 'LSB', n0=n0_for(a_, 50, 2700))
    s = a[-2 * FS:]
    got = level_db(s, 1000)
    check('the tone comes out at 1000 Hz', abs(peak_hz(s) - 1000) < 5 and abs(got - want) < 1.5,
          f'{peak_hz(s):.0f} Hz, {got:.1f} dB')
    check('the other sideband stays out', level_db(s, 1700) - got < -70, f'{level_db(s, 1700) - got:.0f} dB')

    print('CW: a carrier on the dial, another 700 Hz up, 500 Hz filter')
    rx, a, _ = play(tones(4.0, [(0, a_), (700, a_)]), 'CW', n0=n0_for(a_, 30, 500))
    s = a[-2 * FS:]
    got = level_db(s, 600)
    check('plays at the 600 Hz pitch', abs(peak_hz(s) - 600) < 5 and abs(got - want) < 1.5,
          f'{peak_hz(s):.0f} Hz, {got:.1f} dB')
    check('the neighbour (1300 Hz tone) stays out', level_db(s, 1300) - got < -50, f'{level_db(s, 1300) - got:.0f} dB')

    print('Synchronous AM: a station 30 Hz off, 50% at 1100 Hz; an interferer 2500 Hz below')
    st = am_station(6.0, offset=30.0) + tones(6.0, [(30 - 2500, 0.01)])
    for mode, hears in (('SAM_U', False), ('SAM_L', True)):
        rx, a, _ = play(st, mode, n0=n0_for(0.05, 45, 10e3))
        s = a[-2 * FS:]
        tone_ = level_db(s, 1100)
        intf = level_db(s, 2500) - tone_
        check(f'{mode}: carrier found and locked', rx.locked and abs(rx.carrier_hz - 30) < 3,
              f'offset {rx.carrier_hz:+.1f} Hz, locked {rx.locked}')
        exp = 20 * math.log10(0.25 / 1.25 * ref * rx.SAM_GAIN * VOL) + HEADROOM_DB
        check(f'{mode}: the tone heard', abs(tone_ - exp) < 2.0, f'{tone_:.1f} dB (expected {exp:.1f})')
        if hears:
            check(f'{mode}: hears the interferer on its side', intf > -15, f'{intf:+.0f} dB re the tone')
        else:
            check(f'{mode}: the interferer on the other side stays out', intf < -40, f'{intf:+.0f} dB re the tone')

    print('Width, and changes while it runs')
    acts = [(2.0, lambda r: r.set_width(1800)), (4.0, lambda r: r.set_mode('CW')), (6.0, lambda r: r.set_mode('USB'))]
    rx, a, _ = play(tones(8.0, [(2500, a_), (0, a_ / 4)]), 'USB', width=2700, actions=acts)
    seg = lambda i: a[rx.marks[i][1] - int(0.8 * FS): rx.marks[i][1]] if i < len(rx.marks) else a[-int(0.8 * FS):]
    wide, narrow, cw, back = (level_db(seg(i), hz) for i, hz in ((0, 2500), (1, 2500), (2, 600), (3, 2500)))
    check('a 2500 Hz tone passes at 2.7 kHz', abs(wide - want) < 2.0, f'{wide:.1f} dB')
    check('and not at 1.8 kHz', narrow < want - 40, f'{narrow:.1f} dB')
    check('CW, without a restart: the carrier at 600 Hz', abs(cw - want) < 2.0 and not rx.errors, f'{cw:.1f} dB')
    check('back to USB at the width it had (1.8 kHz)', back < want - 40 and rx.width == 1800,
          f'{back:.1f} dB, width {rx.width}')

    print('AGC')
    key = np.concatenate((np.zeros(2 * FS), np.full(2 * FS, 0.3)))
    rx, a, _ = play(key.astype(np.complex128), 'CW', n0=n0_for(0.3, 50, 500))
    top = 20 * math.log10(max(float(np.max(np.abs(a[len(a) // 3:]))), 1e-9))
    check('a strong carrier keyed on: no overshoot', top < want + 0.5, f'peak {top - want:+.1f} dB re the level')
    rx, a, _ = play(np.zeros(4 * FS, np.complex128), 'USB', n0=1e-11)
    quiet = rms_db(a[-2 * FS:]) + 3.0 - want                 # RMS + 3 dB: a steady tone's peak
    check('an empty channel plays about 20 dB under a station', -25 < quiet < -15, f'{quiet:+.1f} dB')

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
