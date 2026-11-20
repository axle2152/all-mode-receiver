#!/usr/bin/env python3
"""
test_nfm.py - the narrowband FM receiver in real GNU Radio. Needs GNU Radio, not a dongle.

    python3 tests/test_nfm.py

A pretend repeater goes in where the dongle would: a carrier with a 1100 Hz tone at 3 kHz
deviation (60%, the usual test level), a CTCSS tone, noise at a chosen carrier-to-noise ratio
(in 12.5 kHz), keyed on and off, and off frequency by the dongle's crystal error. In real time,
into the real NFMReceiver with a silent sound card that keeps what it was sent. Checks:

  - samples flow, the level for the S-meter, the C/N (quieting) reading at 10, 20 and 30 dB
  - the audio: the tone at the right level and frequency, the CTCSS tone kept out of it
  - AFC takes out the crystal error
  - squelch: shut on noise alone, open on a station, shut again when the carrier drops with
    no burst of noise; a weak station opens a low level and not a high one
  - CTCSS: the tone named (100.0, and 67.0 not taken for its neighbour 69.3), none when there's
    none; tone squelch plays the right tone and stays shut on another
  - 2.5 kHz deviation ('NFM2') plays as loud as 5 kHz

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
AMP = 0.1                                   # the carrier's amplitude (-20 dBFS)
F0 = 146.94e6


def check(name, ok, detail):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:46s} {detail}")


def station(secs, dev_khz=3.0, tone_hz=1100.0, ctcss=100.0, ct_khz=0.6, offset_hz=0.0, keyed=None):
    """The station at 48 kHz complex baseband. keyed: [(from s, to s)] the carrier is on
    (None = all the time)."""
    t = np.arange(int(secs * FS)) / FS
    f = offset_hz + dev_khz * 1e3 * np.sin(2 * np.pi * tone_hz * t)
    if ctcss:
        f = f + ct_khz * 1e3 * np.sin(2 * np.pi * ctcss * t)
    x = AMP * np.exp(2j * np.pi * np.cumsum(f) / FS)
    if keyed is not None:
        on = np.zeros(len(t), bool)
        for a, b in keyed:
            on |= (t >= a) & (t < b)
        x = x * on
    return x.astype(np.complex64)


class Dongle(gr.hier_block2):
    """Stands in for osmosdr.source: the station LO_OFFSET below the tuned LO, at 960 ksps in
    real time, plus noise for the C/N asked (in 12.5 kHz)."""

    def __init__(self, x, rate, cnr_db):
        gr.hier_block2.__init__(self, 'nfm_station', gr.io_signature(0, 0, 0),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.src = blocks.vector_source_c(x.tolist(), False)
        self.up = filter.rational_resampler_ccc(rate // FS, 1)
        self.lo = analog.sig_source_c(rate, analog.GR_COS_WAVE, 0.0, 1.0)
        self.mix, self.add = blocks.multiply_cc(), blocks.add_cc()
        n0 = AMP ** 2 / (10 ** (cnr_db / 10) * 12.5e3)            # noise per Hz
        self.noise = analog.noise_source_c(analog.GR_GAUSSIAN, math.sqrt(n0 * rate), 3)
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
    """A silent sound card that keeps what it was sent on the left channel."""

    def __init__(self):
        gr.hier_block2.__init__(self, 'speaker', gr.io_signature(2, 2, gr.sizeof_float),
                                gr.io_signature(0, 0, 0))
        self.left, self.right = blocks.vector_sink_f(), blocks.null_sink(gr.sizeof_float)
        self.connect((self, 0), self.left)
        self.connect((self, 1), self.right)

    def nitems_read(self, port):
        return self.left.nitems_read(0)


def play(x, cnr_db=30.0, secs=None, width='NFM', squelch=3, tone_sq=None, watch=None):
    """Run the receiver on station x; returns (receiver, left audio, readings over time)."""
    from radio.nfm import NFMReceiver

    class Rx(NFMReceiver):
        def _make_source(self, args):
            return Dongle(x, self.samp_rate, cnr_db)

        def _make_audio_sink(self, rate, device):
            return Speaker()

    import radio.addons
    radio.addons._entry_points = lambda: []
    rx = Rx(freq=F0, agc=False, width=width)
    rx.set_output_gain(0.5, 0.5)
    rx.set_tone(0, 0)
    rx.set_mute(False)
    rx.set_squelch(squelch)
    rx.set_tone_squelch(tone_sq)
    secs = secs or len(x) / FS - 0.3
    seen, errors = [], []
    rx.start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        try:
            rx.update_blend()
        except Exception as e:
            errors.append(f'{type(e).__name__}: {e}')
        if watch:
            seen.append((time.monotonic() - t0,) + tuple(getattr(rx, a) for a in watch))
        time.sleep(0.02)
    rx.stop()
    rx.wait()
    rx.errors = errors
    rx.secs = time.monotonic() - t0
    return rx, np.asarray(rx.audio_sink.left.data()), seen


def tone_db(a, hz, fs=FS):
    """Level of the tone at hz in a (dB re amplitude 1), from a Hann-windowed FFT."""
    w = np.hanning(len(a))
    Y = np.abs(np.fft.rfft(a * w))
    f = np.fft.rfftfreq(len(a), 1 / fs)
    m = np.abs(f - hz) < 15
    return 20 * math.log10(max(2 * float(np.max(Y[m])) / np.sum(w), 1e-12))


def rms_db(a):
    return 20 * math.log10(max(float(np.sqrt(np.mean(np.asarray(a, np.float64) ** 2))), 1e-9))


def expected_db(rx, dev_khz, hz):
    """What the sound card should get for a tone of dev_khz at hz: de-emphasis (0 dB at
    1 kHz), the audio gain, the test's volume (0.5) and the tone controls' 1 dB of headroom."""
    p = math.exp(-1.0 / (rx.DEEMPH_US * 1e-6 * FS))
    h = lambda f: abs((1 - p) / (1 - p * np.exp(-2j * np.pi * f / FS)))
    return 20 * math.log10(dev_khz / rx.dev_khz * h(hz) / h(1000.0) * rx.NFM_AUDIO_GAIN * 0.5) - 1.0


def main():
    print('GNU Radio', gr.version())

    print('A repeater: 3 kHz deviation at 1100 Hz, CTCSS 100.0, 30 dB C/N, 1.5 kHz off frequency')
    rx, a, _ = play(station(6.0, offset_hz=1500.0), cnr_db=30.0)
    rate_in = rx._samples_in() / rx.secs
    check('samples from the dongle', abs(rate_in / rx.samp_rate - 1) < 0.1, f'{rate_in / 1e3:.0f}k a second')
    check('samples to the sound card', abs(len(a) / rx.secs / FS - 1) < 0.1, f'{len(a) / rx.secs / 1e3:.1f}k a second')
    check('updates run without errors', not rx.errors, rx.errors[0] if rx.errors else 'none')
    dbfs, gain = rx.signal_level()
    check('level reads what was sent (-20 dBFS)', abs(dbfs + 20) < 1.0, f'{dbfs:.1f} dBFS, gain {gain}')
    check('C/N (quieting) reads 30 dB', abs(rx.cnr_db - 30) < 3, f'{rx.cnr_db:.1f} dB')
    check('AFC takes out the crystal error', abs(rx.afc_hz - 1500) < 60 and abs(rx.carrier_hz - 1500) < 60,
          f'AFC {rx.afc_hz:+.0f} Hz, carrier {rx.carrier_hz:+.0f} Hz')
    check('squelch open on the station', rx.squelch_open and rx.gate_open,
          f'open {rx.squelch_open}, gate {rx.gate_open}')
    check('CTCSS named', rx.ctcss_hz == 100.0, f'{rx.ctcss_hz} (line at {rx.ctcss_line_hz or 0:.2f} Hz, '
          f'{rx.ctcss_khz:.2f} kHz)')
    s = a[-2 * FS:]
    got, want = tone_db(s, 1100.0), expected_db(rx, 3.0, 1100.0)
    check('the tone at the right level', abs(got - want) < 1.0, f'{got:.1f} dB (expected {want:.1f})')
    level_5k = got
    ct = tone_db(s, 100.0) - got
    check('CTCSS kept out of the audio', ct < -45, f'{ct:.0f} dB re the tone')
    mon = rx.monitor()
    check('meters see the audio', bool(mon and mon['audio']['blocks']), f"{len(mon['audio']['blocks']) if mon else 0} blocks")

    for c in (10.0, 20.0):
        rx, _, _ = play(station(3.5, ctcss=None), cnr_db=c, squelch=0)
        check(f'C/N (quieting) reads {c:.0f} dB', abs(rx.cnr_db - c) < 2.5, f'{rx.cnr_db:.1f} dB')

    print('Squelch: noise, then the carrier keyed on 1.5-3.5 s, off, on again at 5 s')
    x = station(7.0, keyed=[(1.5, 3.5), (5.0, 7.0)])
    rx, a, seen = play(x, cnr_db=25.0, watch=('squelch_open', 'gate_open', 'cnr_db'))
    at = lambda t0, t1: [s for s in seen if t0 <= s[0] < t1]
    check('shut on noise alone', not any(s[2] for s in at(0.5, 1.4)), f'cnr {np.mean([s[3] for s in at(0.5, 1.4)]):.1f} dB')
    check('open on the station', all(s[2] for s in at(2.0, 3.3)), '')
    check('shut when the carrier drops', not any(s[2] for s in at(3.7, 4.8)), '')
    check('open again', all(s[2] for s in at(5.5, 6.5)), '')
    # the audio around the drop: no noise burst. The receiver's clock and the station's differ by
    # however long the dongle took to start; find the drop in the audio itself.
    lev = np.array([rms_db(a[i:i + 480]) for i in range(0, len(a) - 480, 480)])    # 10 ms steps
    on = lev > -40
    drops = [i for i in range(1, len(on)) if on[i - 1] and not on[i]]
    tone_lvl = float(np.median(lev[on])) if on.any() else -99
    if drops:
        i = drops[0]
        after = lev[i:i + 100]                       # the second after the tone stops
        check('no burst of noise when it drops', float(np.max(after)) < tone_lvl - 20,
              f'loudest {float(np.max(after)) - tone_lvl:+.0f} dB re the tone in the second after')
    else:
        check('no burst of noise when it drops', False, 'no drop found in the audio')
    silent = lev[int(0.6 * 100):int(1.3 * 100)]
    check('silence before the station', float(np.max(silent)) < -60, f'loudest {float(np.max(silent)):.0f} dBFS')

    print('A weak station, 6 dB C/N')
    rx, _, _ = play(station(3.5), cnr_db=6.0, squelch=1)
    check('opens squelch level 1 (4 dB)', rx.squelch_open, f'C/N {rx.cnr_db:.1f} dB')
    rx, _, _ = play(station(3.5), cnr_db=6.0, squelch=5)
    check('stays shut at level 5 (13 dB)', not rx.squelch_open and not rx.gate_open, f'C/N {rx.cnr_db:.1f} dB')

    print('CTCSS')
    rx, _, _ = play(station(3.5, ctcss=67.0), cnr_db=25.0)
    check('67.0 named, not 69.3', rx.ctcss_hz == 67.0, f'{rx.ctcss_hz} ({rx.ctcss_line_hz or 0:.2f} Hz)')
    rx, _, _ = play(station(3.5, ctcss=254.1, ct_khz=0.35), cnr_db=25.0)
    check('254.1 at 0.35 kHz named', rx.ctcss_hz == 254.1, f'{rx.ctcss_hz} ({rx.ctcss_line_hz or 0:.2f} Hz)')
    rx, _, _ = play(station(3.5, ctcss=None), cnr_db=25.0)
    check('none when there is none', rx.ctcss_hz is None, f'{rx.ctcss_hz}')
    rx, _, _ = play(station(3.5, ctcss=None), cnr_db=0.0, squelch=0)
    check('none on noise', rx.ctcss_hz is None, f'{rx.ctcss_hz}')
    rx, a, _ = play(station(4.0), cnr_db=25.0, tone_sq=100.0)
    check('tone squelch 100.0 plays the 100.0 station', rx.tone_ok and rx.gate_open and rms_db(a[-FS:]) > -40,
          f'tone ok {rx.tone_ok}, audio {rms_db(a[-FS:]):.0f} dBFS')
    rx, a, _ = play(station(4.0), cnr_db=25.0, tone_sq=103.5)
    check('tone squelch 103.5 stays shut on it', not rx.gate_open and rms_db(a[-FS:]) < -80,
          f'gate {rx.gate_open}, audio {rms_db(a[-FS:]):.0f} dBFS')

    print('2.5 kHz deviation (12.5 kHz channels)')
    rx, a, _ = play(station(4.0, dev_khz=1.5, ct_khz=0.3), cnr_db=30.0, width='NFM2')
    got = tone_db(a[-2 * FS:], 1100.0)
    check('60% at 2.5 kHz as loud as 60% at 5', rx.dev_khz == 2.5 and abs(got - level_5k) < 1.0,
          f'{got:.1f} dB (at 5 kHz: {level_5k:.1f})')
    check('C/N (quieting) reads 30 dB', abs(rx.cnr_db - 30) < 3, f'{rx.cnr_db:.1f} dB')
    check('CTCSS named', rx.ctcss_hz == 100.0, f'{rx.ctcss_hz}')

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
