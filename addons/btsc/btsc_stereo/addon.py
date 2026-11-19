"""
btsc_stereo/addon.py - BTSC (MTS) stereo, the US analog TV sound system, for the FM receiver
=========================================================================================
NTSC television sound is FM, and since 1984 it could be stereo, by the BTSC system (Multichannel
Television Sound). The radio tunes TV channel 6's sound as 87.7 (on 87.75 MHz), and BTSC
turns up elsewhere too. The composite:

    L+R            75 us pre-emphasis, 25 kHz deviation at 100% (a third of FM broadcast's)
    pilot          fH = 15.734 kHz (the NTSC line rate), 5 kHz deviation
    L-R            dbx-TV compressed, double sideband on 2fH = 31.469 kHz, 50 kHz at 100%
    (SAP           a second program on an FM subcarrier at 5fH: not decoded here)

Signal path (at the receiver's composite rate, 240 kHz):
  composite -> fH band-pass -> PLL -> doubled -> 2fH carrier --.
                               PLL -> its frequency, 10 ms at a time (is it on fH?)
  composite -> delay (= the band-pass's) -------------------- x -> the receiver's own 15 kHz
     low-pass -> level -> dbx-TV expansion -> 75 us pre-emphasis -> the receiver's matrix
  (and the same at the carrier's cosine phase, which carries only noise: the expander takes
  the noise's share off its detectors, so weak stations keep their separation)
The band-pass is as long as the receiver's own low-pass, so the delays add up to the
receiver's: L-R reaches the matrix lined up with L+R. The expander and its fixed filters are
the exact inverse of the compressor's, so they add no delay. BTSC's L-R carries no 75 us
pre-emphasis, but the receiver de-emphasizes left and right after the matrix, so L-R gets the
inverse of that de-emphasis first (exact through the audio band).

Levels. L+R is raised by 3 (9.5 dB) so a TV station plays as loud as an FM station. dbx-TV
expansion doubles any level error (in dB) on L-R, so a station deviating a little off would
lose its separation; the 5 kHz pilot is the reference: the L-R channel and the 9.5 dB both
follow how far the measured pilot is from 5 kHz.

Is it BTSC? When, for 0.3 s, the signal is usable and there's a pilot locked within 25 Hz of
fH at a real level (half its 5 kHz or more). That wins over anything that looks like a 19 kHz
pilot: BTSC's L-R puts energy right on 19 kHz (L-R at 12.47 kHz has its lower sideband
there, and dbx-TV boosts the treble), enough to lock the receiver's FM stereo detector, while
FM stereo has nothing at 15.734 kHz anywhere near a pilot's level. So once it's BTSC, the
add-on reports own_pilot and the receiver stops looking for 19 kHz.
"""
import math

import numpy as np
from gnuradio import gr, blocks, analog, filter
from gnuradio.filter import firdes

FH = 2.0 * (315e6 / 88.0) / 455.0        # the NTSC line rate, 15734.2657 Hz: the pilot


def smoothstep(x, lo, hi):
    t = min(max((x - lo) / (hi - lo), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class _Decode(gr.sync_block):
    """The received L-R channel (1.0 = 100%), and the same channel's quadrature (only noise)
    -> dbx-TV expansion -> 75 us pre-emphasis that the receiver's de-emphasis takes off
    again -> (L-R)/2 x MPX_FULL."""

    def __init__(self, fs, full, deemph_b0, deemph_a1):
        from .dbx import Expander
        from scipy import signal
        gr.sync_block.__init__(self, 'btsc_dbx', [np.float32, np.float32], [np.float32])
        self.exp = Expander(fs)
        self.signal = signal
        # The receiver's de-emphasis is b0 (1 + z^-1) / (1 + a1 z^-1). Its exact inverse has a
        # pole at the Nyquist frequency, so (1 + z^-1) becomes (1 + c z^-1), scaled to match at
        # DC: off by under 0.6 degrees at 15 kHz, and the boost near 120 kHz is filtered off later.
        c = 0.9
        g = (1 + c) / (2 * deemph_b0)
        self.pre_b, self.pre_a = [g * full, g * full * deemph_a1], [1.0, c]
        self.zpre = np.zeros(1)

    def reset(self):
        self.exp.reset()
        self.zpre = np.zeros(1)

    def work(self, input_items, output_items):
        x = self.exp.process(input_items[0], input_items[1])
        y, self.zpre = self.signal.lfilter(self.pre_b, self.pre_a, x, zi=self.zpre)
        output_items[0][:] = y
        return len(y)


class Addon:
    BANDS = ('FM',)
    NAME = 'BTSC'

    # ---- the system (OET-60) ----
    DEV_MAIN = 25e3           # L+R at 100%
    DEV_PILOT = 5e3
    DEV_STEREO = 50e3         # L-R at 100%
    MAIN_GAIN = 75e3 / 25e3   # to play as loud as FM broadcast, whose 100% is 75 kHz
    MPX_TOP_HZ = 2 * FH + 15e3
    # ---- is it there? ----
    WINDOW_S = 0.01           # pilot measurement windows
    AVG_S = 0.2               # pilot level averaged about this long
    PILOT_MIN = 0.5           # of its 5 kHz
    PILOT_GONE = 0.3
    LOCK_HZ = 25.0            # the PLL within this of fH
    MIN_CNR_DB = 6.0          # below this a PLL locks onto noise
    FIND_S = 0.3
    LOSE_S = 0.5
    SETTLE_S = 0.15
    LEVEL_RANGE = (0.5, 2.0)  # the pilot reference corrects deviation errors up to 6 dB
    LEVEL_AVG_S = 1.0
    PLL_BW_HZ = 50.0

    def __init__(self, rx):
        if rx.BAND != 'FM':
            raise ValueError('BTSC is TV sound, received like FM')
        try:
            import scipy  # noqa: F401
        except ImportError:
            raise ImportError('BTSC decoding needs scipy (Ubuntu: sudo apt install python3-scipy)')
        self.rx = rx
        fs = rx.mpx_rate
        full = rx.MPX_FULL
        W = filter.window.WIN_HAMMING
        lp = list(rx.stereo_lowpass())
        # the pilot's positive frequency only: a real pilot's mirror at -fH would make the PLL
        # ripple at 2fH, and that ripple tips L-R into the carrier's cosine phase
        bp = firdes.complex_band_pass(1.0, fs, FH - 500, FH + 500, 2000, W)
        d_bp = (len(bp) - 1) // 2
        align = (len(lp) - 1) - d_bp - (len(lp) - 1) // 2
        if align < 0:
            raise ValueError(f'the pilot filter ({len(bp)} taps) is longer than the receiver allows')
        self._win = int(round(fs * self.WINDOW_S))
        # the composite's own scale: what 100% pilot and 100% L-R read in it
        self._droop_p = float(np.sinc(FH / fs))           # the FM discriminator's roll-off
        self._pilot_full = full * self.DEV_PILOT / 75e3 * self._droop_p
        self._lmr_full = full * self.DEV_STEREO / 75e3

        self.f2c = blocks.float_to_complex(1)
        self.bp = filter.fft_filter_ccc(1, bp)
        self.pll = analog.pll_refout_cc(2 * math.pi * self.PLL_BW_HZ / fs, 2 * math.pi * (FH + 150) / fs,
                                        2 * math.pi * (FH - 150) / fs)
        self.dbl = blocks.multiply_cc(1)
        droop = float(np.sinc(2 * FH / fs))                 # at 2fH
        self.rot = blocks.multiply_const_cc(complex(0.0, 2.0 / droop))   # 2 e^(j90): sin(2wt)
        self.ref = blocks.complex_to_float(1)     # sine phase: L-R; cosine phase: noise only
        self.dly = blocks.delay(gr.sizeof_float, d_bp + align)
        self.mul, self.mulq = blocks.multiply_ff(1), blocks.multiply_ff(1)
        self.lpf, self.lpfq = filter.fft_filter_fff(1, lp), filter.fft_filter_fff(1, lp)
        self.level = blocks.multiply_const_ff(1.0 / self._lmr_full)
        self.levelq = blocks.multiply_const_ff(1.0 / self._lmr_full)
        w_c, k = 1.0 / 75e-6, 2.0 * fs                      # the receiver's de-emphasis
        self.dec = _Decode(fs, full, w_c / (k + w_c), (w_c - k) / (w_c + k))
        # pilot: coherent level (pilot x conj(PLL)), 10 ms at a time
        self.pmix = blocks.multiply_conjugate_cc(1)
        self.pint = blocks.integrate_cc(self._win)
        self.probe = blocks.probe_signal_c()
        # the PLL's frequency, from how fast its output turns, 10 ms at a time. (Not
        # pll.get_frequency(): GNU Radio 3.10.1, Ubuntu 22.04's, doesn't offer it in Python, so
        # the PLL never looked locked there.)
        self.pfm = analog.quadrature_demod_cf(fs / (2 * math.pi))     # in Hz
        self.pfint = blocks.integrate_ff(self._win)
        self.pfprobe = blocks.probe_signal_f()

        c = rx.connect
        tap = rx.addon_tap()
        c(tap, self.f2c, self.bp, self.pll)
        c(self.pll, (self.dbl, 0))
        c(self.pll, (self.dbl, 1))
        c(self.dbl, self.rot, self.ref)
        c((self.ref, 0), (self.mul, 1))
        c((self.ref, 1), (self.mulq, 1))
        c(tap, self.dly, (self.mul, 0))
        c(self.dly, (self.mulq, 0))
        c(self.mul, self.lpf, self.level, (self.dec, 0))
        c(self.mulq, self.lpfq, self.levelq, (self.dec, 1))
        c(self.bp, (self.pmix, 0))
        c(self.pll, (self.pmix, 1))
        c(self.pmix, self.pint, self.probe)
        c(self.pll, self.pfm, self.pfint, self.pfprobe)
        self.reset()

    def stereo_decoder(self):
        return {'out': (self.dec, 0), 'main_gain': self.MAIN_GAIN, 'mpx_top': self.MPX_TOP_HZ}

    def reset(self):
        self._last = None
        self._t = None
        self._settle = None
        self._ok_s = self._bad_s = 0.0
        self.found = False
        self.quality = 0.0
        self.pilot = None            # pilot level as a share of its 5 kHz
        self.pilot_hz = None
        self.k = 1.0                 # level correction from the pilot
        self.level.set_k(1.0 / self._lmr_full)
        self.levelq.set_k(1.0 / self._lmr_full)
        self.main_gain = self.MAIN_GAIN

    def update(self, now):
        rx = self.rx
        dt = 0.0 if self._t is None else min(max(now - self._t, 0.0), 0.2)
        self._t = now
        if self._settle is None:
            self._settle = now + self.SETTLE_S
        p = self.probe.level()
        if p != self._last and now >= self._settle:
            self._last = p
            lv = 2 * abs(p) / self._win / self._pilot_full
            a = 1 - math.exp(-self.WINDOW_S / self.AVG_S)
            self.pilot = lv if self.pilot is None else self.pilot + a * (lv - self.pilot)
        self.pilot_hz = self.pfprobe.level() / self._win if now >= self._settle else None
        locked = self.pilot_hz is not None and abs(self.pilot_hz - FH) <= self.LOCK_HZ
        ok = (self.pilot is not None and locked
              and self.pilot >= self.PILOT_MIN and rx.cnr_db >= self.MIN_CNR_DB)
        gone = (self.pilot is None or self.pilot < self.PILOT_GONE
                or not locked or rx.cnr_db < self.MIN_CNR_DB - 3)
        if ok:
            self._ok_s, self._bad_s = self._ok_s + dt, 0.0
        elif gone:
            self._ok_s, self._bad_s = 0.0, self._bad_s + dt
        else:
            self._ok_s = 0.0
        if not self.found and self._ok_s >= self.FIND_S:
            self.found = True
            self.k = 1.0 / self.pilot
        elif self.found and self._bad_s >= self.LOSE_S:
            self.found = False
        if self.found and self.pilot:                       # the pilot is the level reference
            lo, hi = self.LEVEL_RANGE
            tgt = min(max(1.0 / self.pilot, lo), hi)
            self.k += (1 - math.exp(-dt / self.LEVEL_AVG_S)) * (tgt - self.k)
        elif not self.found:
            self.k = 1.0
        self.level.set_k(self.k / self._lmr_full)
        self.levelq.set_k(self.k / self._lmr_full)
        self.main_gain = self.MAIN_GAIN * self.k
        self.quality = (smoothstep(rx.cnr_db, rx.STEREO_MONO_DB, rx.STEREO_FULL_DB)
                        if self.found else 0.0)
        return {'stereo': self.found, 'stereo_quality': self.quality, 'synced': self.found,
                'main_gain': self.main_gain, 'own_pilot': self.found}

    # --------------------------------------------------------- for the window
    def why_not(self):
        """Why the station isn't taken for BTSC stereo right now ('' when it is)."""
        if self.found:
            return ''
        if self.pilot is None:
            return 'listening for a 15.734 kHz pilot'
        if self.rx.cnr_db < self.MIN_CNR_DB:
            return 'signal too noisy to tell'
        if self.pilot < 0.1:
            return 'no 15.734 kHz pilot' + (' (an FM stereo station)' if self.rx.pilot_present else '')
        if self.pilot_hz is None or abs(self.pilot_hz - FH) > self.LOCK_HZ:
            return 'pilot not locked'
        if self.pilot < self.PILOT_MIN:
            return f'pilot too weak ({self.pilot * self.DEV_PILOT / 1e3:.1f} kHz, needs 2.5)'
        return 'found, checking'

    def readout(self):
        if self.pilot is None:
            return 'BTSC  listening for a 15.734 kHz pilot'
        state = ('STEREO' if self.quality > 0 else 'stereo station, blended to mono') \
            if self.found else self.why_not()
        hz = f" at {self.pilot_hz:7.1f} Hz" if self.pilot_hz is not None else ''
        line = f"BTSC  {state}   pilot {self.pilot * self.DEV_PILOT / 1e3:3.1f} kHz{hz}"
        if self.found:
            line += f", L+R raised {20 * math.log10(self.main_gain):4.1f} dB"
        return line

    def meter(self):
        if not self.found:
            return ('BTSC pilot', 0.0, 'none')
        return ('BTSC pilot', min(1.0, self.pilot), f"{self.pilot * self.DEV_PILOT / 1e3:3.1f} kHz")
