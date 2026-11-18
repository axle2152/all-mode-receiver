#!/usr/bin/env python3
"""
test_btsc.py - does the BTSC add-on play TV stereo, and only TV stereo?

    python3 addons/btsc/test_btsc.py

Needs GNU Radio and scipy, not a dongle, and not the add-on installed (it plugs this folder's
copy in the way an installed one plugs in). Makes pretend stations with a BTSC encoder of its
own (the FCC OET-60 equations, as the reference transmitter in gr-flowgraphs/ntsc-btsc-tx does
them), FM at the dongle's rate, and runs the real FM receiver with the add-on in real time:

  BTSC stereo             found; BTSC STEREO; separation at 1 and 10 kHz, and at a low
                          level (where dbx-TV expansion is hardest); as loud as FM broadcast
  L-R on 19 kHz           BTSC whose L-R puts energy right on 19 kHz (enough to lock the FM
                          stereo detector) still plays BTSC
  BTSC at 80% deviation   the pilot sets the level: still separated, still as loud
  TV sound in mono        not BTSC
  FM stereo (19 kHz)      the radio's own decoder; not BTSC
  Crosby, if its add-on   with both add-ons installed, each station plays through its own
  is beside this one

The tones are 1100 Hz and 10007 Hz. The receiver tunes 2 kHz off the station, and a loud tone
whose FM lines fall a few Hz from 2 kHz (997 Hz's second does) is partly eaten by its DC
canceller, which costs separation that real program never loses.
"""
import math
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CROSBY = os.path.join(os.path.dirname(HERE), 'crosby')
sys.path[:0] = [os.path.dirname(os.path.dirname(HERE)), HERE]

try:
    import numpy as np
    from scipy import signal
    from gnuradio import gr, blocks, analog
except ImportError as e:
    print(f'Needs GNU Radio and scipy ({e}), so there is nothing to test.')
    sys.exit(0)

FH = 2.0 * (315e6 / 88.0) / 455.0
FS_C = 480000.0                  # the pretend transmitter's composite rate
SECS = 4.5
failed = 0


# ---------------------------------------------------------------- a BTSC encoder (OET-60)
def _w(hz):
    return 2 * math.pi * hz


def _sos(z, p, k, fs):
    zd, pd, kd = signal.bilinear_zpk(np.asarray(z, complex), np.asarray(p, complex), k, fs)
    return signal.zpk2sos(zd, pd, kd)


def _bandlimit(fs):
    return signal.iirdesign(15000.0, 15700.0, 0.05, 65.0, ftype='ellip', output='sos', fs=fs)


def _preemph(fs):                # 75 us, made realisable with a 100 kHz pole
    return _sos([-1 / 75e-6], [-_w(100e3)], 75e-6 * _w(100e3), fs)


class Compressor:
    """dbx-TV compressor, OET-60 Figure 1 (a feedback compressor: the detectors watch its
    output), updated every 0.25 ms."""

    def __init__(self, fs):
        self.fs = fs
        self.F = _sos([-_w(408), -_w(2190)], [-_w(5230), -_w(62500)],
                      _w(5230) * _w(62500) / (_w(408) * _w(2190)), fs)
        self.P = _sos([0.0], [-_w(35.4), -_w(2090)], _w(2090), fs)
        wq, wc, wd = _w(5860), _w(7660), _w(7310)
        self.Q = _sos([0.0] * 3, list(np.roots([1, wc * wc / wd, wc * wc])) + [-_w(26900), -_w(3920)],
                      wc * wc * _w(26900) * _w(3920) / wq ** 3, fs)
        self.BL = np.vstack([_sos([], [-_w(100e3)], _w(100e3), fs), _bandlimit(fs)])
        self.r1, self.r2 = math.exp(-1 / (34.7e-3 * fs)), math.exp(-1 / (11.4e-3 * fs))
        self.block = int(round(0.25e-3 * fs))

    def process(self, x):
        u = signal.sosfilt(self.F, x)
        zp = np.zeros((self.P.shape[0], 2))
        zq = np.zeros((self.Q.shape[0], 2))
        zb = np.zeros((self.BL.shape[0], 2))
        ms1, ms2, a, b, sx, sy = 0.06249 ** 2, 0.035655 ** 2, 1.0, 1.0, 0.0, 0.0
        y = np.empty_like(u)
        wF, K = _w(20.1e3), 2 * self.fs
        for s in range(0, len(u), self.block):
            v = u[s:s + self.block] / a
            wz, wp = wF * (b + 1) / (b + 51), wF * (b + 1) / (1 + 51 * b)
            a0 = 1 + K / wp
            b0, b1, a1 = (1 + K / wz) / a0, (1 - K / wz) / a0, (1 - K / wp) / a0
            w, _ = signal.lfilter((b0, b1), (1, a1), v, zi=(b1 * sx - a1 * sy,))
            sx, sy = v[-1], w[-1]
            np.clip(w, -1, 1, out=w)
            yb, zb = signal.sosfilt(self.BL, w, zi=zb)
            y[s:s + len(yb)] = yb
            p, zp = signal.sosfilt(self.P, yb, zi=zp)
            q, zq = signal.sosfilt(self.Q, yb, zi=zq)
            for v2 in p * p:
                ms1 = self.r1 * ms1 + (1 - self.r1) * v2
            for v2 in q * q:
                ms2 = self.r2 * ms2 + (1 - self.r2) * v2
            a = max(math.sqrt(ms1) / 0.06249, 1e-3)
            b = min(max(math.sqrt(ms2) / 0.035655, 1e-4), 1e4)
        return y


def composite(kind, left, right):
    """The station's deviation in Hz at FS_C."""
    n = np.arange(len(left))
    m = signal.sosfilt(_preemph(FS_C), 0.5 * (left + right))
    if kind in ('btsc', 'tv_mono'):
        m = signal.sosfilt(_bandlimit(FS_C), np.clip(m, -1, 1))
        dev = 25e3 * m
        if kind == 'btsc':
            y = Compressor(FS_C).process(0.5 * (left - right))
            dev += 5e3 * np.sin(2 * np.pi * FH * n / FS_C) + 50e3 * y * np.sin(4 * np.pi * FH * n / FS_C)
        return dev
    if kind == 'fm_mono':
        return 75e3 * m
    if kind == 'gez':
        s = signal.sosfilt(_preemph(FS_C), 0.5 * (left - right))
        return 75e3 * (0.9 * (m + s * np.sin(4 * np.pi * 19e3 * n / FS_C)) + 0.1 * np.sin(2 * np.pi * 19e3 * n / FS_C))
    if kind == 'crosby':
        s = signal.sosfilt(_preemph(FS_C), 0.5 * (left - right))
        ph = 2 * np.pi * (50e3 * n / FS_C + 25e3 * np.cumsum(s) / FS_C)
        return 75e3 * (0.5 * m + 0.5 * np.cos(ph))
    raise ValueError(kind)


def station_file(kind, left, right, scale=1.0, cnr_db=40.0):
    """The station as the dongle would send it (2.4 Msps IQ, the carrier at 0 Hz), in a file."""
    dev = composite(kind, left, right) * scale
    up = signal.resample_poly(dev, 5, 1)                 # 480k -> 2.4M
    ph = np.cumsum(up) * (2 * np.pi / 2.4e6)
    rng = np.random.default_rng(7)
    amp = 0.3
    n_std = amp * math.sqrt(10 ** (-cnr_db / 10) * 2.4e6 / 200e3 / 2)
    iq = (amp * np.exp(1j * ph) + n_std * (rng.standard_normal(len(ph)) + 1j * rng.standard_normal(len(ph))))
    fd, path = tempfile.mkstemp(suffix='.c64')
    os.close(fd)
    iq.astype(np.complex64).tofile(path)
    return path


class FileStation(gr.hier_block2):
    """Stands in for the dongle: the station from a file, in real time, at the frequency tuned."""

    def __init__(self, path, rate, station_hz):
        gr.hier_block2.__init__(self, 'station', gr.io_signature(0, 0, 0),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.station_hz = station_hz
        self.lo = analog.sig_source_c(rate, analog.GR_COS_WAVE, 0.0, 1.0)
        src, mix = blocks.file_source(gr.sizeof_gr_complex, path, True), blocks.multiply_cc()
        self.connect(src, (mix, 0))
        self.connect(self.lo, (mix, 1))
        self.connect(mix, blocks.throttle(gr.sizeof_gr_complex, rate), self)

    def set_center_freq(self, f, *a):
        self.lo.set_frequency(self.station_hz - f)
        return f

    def get_gain_range(self, *a):
        raise RuntimeError('no gain table: the receiver uses its own')

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return lambda *a, **k: 0


class Speakers(gr.sync_block):
    def __init__(self):
        gr.sync_block.__init__(self, 'speakers', [np.float32, np.float32], None)
        self.blocks = []

    def work(self, ins, outs):
        self.blocks.append((ins[0].copy(), ins[1].copy()))
        return len(ins[0])

    def last(self, secs, rate):
        n, got = int(secs * rate), list(self.blocks)
        return (np.concatenate([b[0] for b in got])[-n:], np.concatenate([b[1] for b in got])[-n:])


def addons(with_crosby=False):
    import radio.addons
    from btsc_stereo import Addon
    eps = [('btsc', Addon)]
    if with_crosby:
        sys.path.insert(1, CROSBY)
        from crosby_stereo import Addon as Crosby
        eps.append(('crosby', Crosby))

    class EntryPoint:
        def __init__(self, name, cls):
            self.name, self._cls = name, cls

        def load(self):
            return self._cls
    radio.addons._entry_points = lambda: [EntryPoint(n, c) for n, c in eps]


def listen(path, secs=4.0, with_crosby=False):
    """The real FM receiver on the station in the file; returns it, stopped, and its speakers."""
    from radio.fm.receiver import FMStereoReceiver
    addons(with_crosby)

    class Rx(FMStereoReceiver):
        def _make_source(self, args):
            return FileStation(path, self.samp_rate, 90.5e6)

        def _make_audio_sink(self, rate, device):
            return Speakers()
    tb = Rx(freq=90.5e6, agc=False)
    tb.set_output_gain(1.0, 1.0)
    tb.set_tone(0, 0)
    tb.set_mute(False)
    tb.start()
    t0, found = time.monotonic(), None
    while time.monotonic() - t0 < secs:
        tb.update_blend()
        if found is None and tb.addon_stereo:
            found = time.monotonic() - t0
        time.sleep(0.02)
    tb.stop()
    tb.wait()
    os.remove(path)
    return tb, found


def tone(x, f, rate=48000):
    n, w = np.arange(len(x)), np.hanning(len(x))
    return 2 * abs(np.sum(x * w * np.exp(-2j * np.pi * f * n / rate))) / np.sum(w)


def db(a, b):
    return 20 * math.log10(max(a, 1e-12) / max(b, 1e-12))


def check(name, ok, detail):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:46s} {detail}")


def main():
    print('GNU Radio', gr.version())
    t = np.arange(int(SECS * FS_C)) / FS_C
    l1, r10 = 0.5 * np.cos(2 * np.pi * 1100.0 * t), 0.2 * np.cos(2 * np.pi * 10007.0 * t)
    quiet = 0.5 * 10 ** (-30 / 20) * np.cos(2 * np.pi * 1100.0 * t)
    on19 = 0.3 * np.cos(2 * np.pi * 1100.0 * t) + 0.1 * np.cos(2 * np.pi * (2 * FH - 19e3) * t)
    zero = np.zeros_like(t)

    print('A loud FM station (75 kHz), the same left tone, for loudness')
    tb, _ = listen(station_file('fm_mono', l1, zero))
    L, R = tb.audio_sink.last(1.5, tb.audio_rate)
    fm_level = tone(L + R, 1100.0)
    print(f"       left + right at 1 kHz: {fm_level:.4f}")

    print('BTSC stereo: 1.1 kHz on the left, 10 kHz on the right')
    tb, found = listen(station_file('btsc', l1, r10))
    a = next(x.obj for x in tb.addons if x.name == 'btsc')
    L, R = tb.audio_sink.last(1.5, tb.audio_rate)
    check('found within 1 s', found is not None and found < 1.0,
          'not found' if found is None else f"after {found:.2f} s")
    check('BTSC STEREO, full stereo', tb.addon_stereo == 'BTSC' and tb.stereo_indicator
          and tb.current_blend > 0.99, f"blend {tb.current_blend:.2f}")
    check('pilot reads 5 kHz at 15734 Hz', abs(a.pilot - 1) < 0.03 and abs(a.pilot_hz - FH) < 2,
          f"{a.pilot * 5:.2f} kHz at {a.pilot_hz:.1f} Hz")
    check('L+R raised 9.5 dB', abs(tb._stereo['gain_db'] - 9.54) < 0.3, f"{tb._stereo['gain_db']:.2f} dB")
    s1, s10 = db(tone(L, 1100.0), tone(R, 1100.0)), db(tone(R, 10007.0), tone(L, 10007.0))
    check('separation at 1 kHz', s1 >= 45, f"{s1:.1f} dB (45 or more)")
    check('separation at 10 kHz', s10 >= 40, f"{s10:.1f} dB (40 or more)")
    lv = db(tone(L + R, 1100.0), fm_level)
    check('as loud as the FM station', abs(lv) < 0.5, f"{lv:+.2f} dB")
    check('the echo canceller still free to work', not tb._stereo_wide(), 'BTSC fits the channel')

    print('BTSC stereo, quiet: 1.1 kHz on the left at -30 dB')
    tb, found = listen(station_file('btsc', quiet, zero))
    L, R = tb.audio_sink.last(1.5, tb.audio_rate)
    s1 = db(tone(L, 1100.0), tone(R, 1100.0))
    check('separation at 1 kHz, quiet', s1 >= 35, f"{s1:.1f} dB (35 or more)")

    print('BTSC whose L-R puts energy right on 19 kHz')
    tb, found = listen(station_file('btsc', on19, zero))
    check("what passes for a 19 kHz pilot doesn't stop it", tb.addon_stereo == 'BTSC'
          and not tb.pilot_present, f"19 kHz detector reads {tb.pilot_level:.3f} (0.035 locks it)")

    print('BTSC at 80% of its deviation')
    tb, found = listen(station_file('btsc', l1, r10, scale=0.8))
    a = next(x.obj for x in tb.addons if x.name == 'btsc')
    L, R = tb.audio_sink.last(1.5, tb.audio_rate)
    check('pilot reads 4 kHz; L+R raised 1.9 dB more',
          abs(a.pilot - 0.8) < 0.03 and abs(tb._stereo['gain_db'] - 9.54 - 1.94) < 0.4,
          f"{a.pilot * 5:.2f} kHz, {tb._stereo['gain_db']:.1f} dB")
    s1 = db(tone(L, 1100.0), tone(R, 1100.0))
    check('separation at 1 kHz still', s1 >= 40, f"{s1:.1f} dB (40 or more)")
    lv = db(tone(L + R, 1100.0), fm_level)
    check('as loud as the FM station still', abs(lv) < 0.7, f"{lv:+.2f} dB")

    print('TV sound in mono (no pilot)')
    tb, found = listen(station_file('tv_mono', l1, zero))
    a = next(x.obj for x in tb.addons if x.name == 'btsc')
    check('not BTSC', found is None and not tb.stereo_indicator, a.why_not())

    print('FM stereo (19 kHz pilot)')
    tb, found = listen(station_file('gez', l1, zero))
    a = next(x.obj for x in tb.addons if x.name == 'btsc')
    check("the radio's own decoder; not BTSC", tb.pilot_present and tb.stereo_indicator
          and found is None, a.why_not())

    if os.path.isdir(os.path.join(CROSBY, 'crosby_stereo')):
        print('Crosby and BTSC installed together')
        tb, found = listen(station_file('btsc', l1, r10), with_crosby=True)
        check('a BTSC station plays BTSC', tb.addon_stereo == 'BTSC', f"{tb.addon_stereo}")
        tb, found = listen(station_file('crosby', l1, zero), with_crosby=True)
        check('a Crosby station plays Crosby', tb.addon_stereo == 'CROSBY', f"{tb.addon_stereo}")

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
