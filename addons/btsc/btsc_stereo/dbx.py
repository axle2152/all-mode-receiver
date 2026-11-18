"""
btsc_stereo/dbx.py - the dbx-TV expander for BTSC's L-R channel (FCC OET Bulletin 60)
====================================================================================
BTSC sends L-R compressed: a fixed pre-emphasis F(f), then a wideband gain 1/a and a
spectral (treble) compression S(f, b), both steered by RMS detectors that watch the
compressor's own output through the weighting filters P(f) and Q(f). The expander watches
the same signal through the same filters, so it recovers the same a and b, and undoes them:

    received y -> S(f, b)^-1 -> x a -> F(f)^-1 -> L-R

The compressor's detectors watch its output after a steep 15 kHz band-limit (it's inside
the loop), so they never see anything above 15.7 kHz. A receiver's L-R channel has the pilot
in it, though: demodulating at 2fH turns the 15.734 kHz pilot into a 15.734 kHz tone that a
gentle low-pass barely touches, and the spectral detector, weighted to the treble, would take
it for program. So the expander's detectors get the same band-limit; the signal doesn't.

The compressor's detectors never heard noise either, and a weak station's L-R channel is noisy
(it sits at 16-47 kHz in the composite, where FM noise is strongest): noise lifts the
detectors, quiet passages come out too loud and too bright, and separation suffers. L-R rides
on the sine phase of 2fH, so the cosine phase carries only noise, as strong; given that
(process(y, noise)), its share is taken off what the detectors read.

The equations and constants are OET-60's, as implemented in the reference transmitter
(gr-flowgraphs/ntsc-btsc-tx, btsc.py); the analog prototypes go to the digital domain by the
bilinear transform at the rate given (240 kHz in the radio: warping in the audio band is
about 1%). Needs numpy and scipy, nothing from GNU Radio, so it can be checked on its own.
"""
import math

import numpy as np
from scipy import signal

T1 = 34.7e-3            # wideband RMS detector time constant
T2 = 11.4e-3            # spectral RMS detector time constant
WB_REF = 0.062490       # 0 dB reference of the wideband detector
SP_REF = 0.035655       # 0 dB reference of the spectral detector
F_SPECTRAL = 20.1e3
A_MIN = 1e-3            # wideband gain limit (+60 dB in silence)
B_MIN, B_MAX = 1e-4, 1e4


def _w(hz):
    return 2.0 * math.pi * hz


def fixed_preemphasis_zpk():
    """F(f) = (jf/0.408 + 1)(jf/2.19 + 1) / ((jf/5.23 + 1)(jf/62.5 + 1)), f in kHz"""
    z = [-_w(408.0), -_w(2190.0)]
    p = [-_w(5230.0), -_w(62500.0)]
    return z, p, (_w(5230.0) * _w(62500.0)) / (_w(408.0) * _w(2190.0))


def wideband_weighting_zpk():
    """P(f) = (jf/0.0354) / ((jf/0.0354 + 1)(jf/2.09 + 1))"""
    wa, wb = _w(35.4), _w(2090.0)
    return [0.0], [-wa, -wb], wb


def spectral_weighting_zpk():
    """Q(f) = (jf/5.86)^3 / (((jf/7.66)^2 + jf/7.31 + 1)(jf/26.9 + 1)(jf/3.92 + 1))"""
    wq, wc, wd, we, wf = _w(5860.0), _w(7660.0), _w(7310.0), _w(26900.0), _w(3920.0)
    quad = np.roots([1.0, wc * wc / wd, wc * wc])
    return [0.0, 0.0, 0.0], list(quad) + [-we, -wf], wc * wc * we * wf / wq ** 3


def sos(zpk, fs):
    z, p, k = zpk
    zd, pd, kd = signal.bilinear_zpk(np.asarray(z, complex), np.asarray(p, complex), k, fs)
    return signal.zpk2sos(zd, pd, kd)


def spectral_coeffs(b, fs):
    """S(f, b) as a bilinear first-order section (b0, b1, a1)."""
    wF = _w(F_SPECTRAL)
    wz = wF * (b + 1.0) / (b + 51.0)
    wp = wF * (b + 1.0) / (1.0 + 51.0 * b)
    K = 2.0 * fs
    a0 = 1.0 + K / wp
    return (1.0 + K / wz) / a0, (1.0 - K / wz) / a0, (1.0 - K / wp) / a0


class Expander:
    """dbx-TV expander, a stream at a time: process(y) takes the received L-R channel
    (1.0 = 100%, 50 kHz on the air) and returns the decoded (L-R)/2. The spectral expansion
    is updated every block_s (the detectors' time constants are 11 and 35 ms)."""

    def __init__(self, fs, block_s=0.25e-3):
        self.fs = float(fs)
        z, p, k = fixed_preemphasis_zpk()
        self.finv = sos((p, z, 1.0 / k), self.fs)
        self.p = sos(wideband_weighting_zpk(), self.fs)
        self.q = sos(spectral_weighting_zpk(), self.fs)
        self.bl = signal.iirdesign(15000.0, 15700.0, 0.05, 65.0, ftype='ellip', output='sos',
                                   fs=self.fs)       # the band-limit the detectors watch through
        self.r1 = math.exp(-1.0 / (T1 * self.fs))
        self.r2 = math.exp(-1.0 / (T2 * self.fs))
        self.block = max(1, int(round(block_s * self.fs)))
        self.reset()

    def reset(self):
        self.zf = np.zeros((self.finv.shape[0], 2))
        self.zp = np.zeros((self.p.shape[0], 2))
        self.zq = np.zeros((self.q.shape[0], 2))
        self.zb = np.zeros((self.bl.shape[0], 2))
        self.ms1, self.ms2 = WB_REF ** 2, SP_REF ** 2        # start at unity gain, flat
        self.nz = [np.zeros((m.shape[0], 2)) for m in (self.bl, self.p, self.q)]
        self.ns1 = self.ns2 = 0.0                            # the noise's share of each
        self.sx = self.sy = 0.0
        self.a = self.b = 1.0

    def _detect(self, y, zs):
        """y through the band-limit and the two weightings: (P(y)^2, Q(y)^2), new states."""
        yd, zb = signal.sosfilt(self.bl, y, zi=zs[0])
        pw, zp = signal.sosfilt(self.p, yd, zi=zs[1])
        qw, zq = signal.sosfilt(self.q, yd, zi=zs[2])
        return pw * pw, qw * qw, [zb, zp, zq]

    def process(self, y, noise=None):
        y = np.asarray(y, dtype=float)
        if not len(y):
            return y
        p2, q2, (self.zb, self.zp, self.zq) = self._detect(y, [self.zb, self.zp, self.zq])
        ms1, _ = signal.lfilter([1 - self.r1], [1, -self.r1], p2, zi=[self.r1 * self.ms1])
        ms2, _ = signal.lfilter([1 - self.r2], [1, -self.r2], q2, zi=[self.r2 * self.ms2])
        self.ms1, self.ms2 = float(ms1[-1]), float(ms2[-1])
        if noise is not None:                   # take off what the noise alone reads
            n1, n2, self.nz = self._detect(np.asarray(noise, dtype=float), self.nz)
            ns1, _ = signal.lfilter([1 - self.r1], [1, -self.r1], n1, zi=[self.r1 * self.ns1])
            ns2, _ = signal.lfilter([1 - self.r2], [1, -self.r2], n2, zi=[self.r2 * self.ns2])
            self.ns1, self.ns2 = float(ns1[-1]), float(ns2[-1])
            ms1 = np.maximum(ms1 - ns1, 1e-3 * ms1)
            ms2 = np.maximum(ms2 - ns2, 1e-3 * ms2)
        a = np.maximum(np.sqrt(ms1) / WB_REF, A_MIN)
        v = np.empty_like(y)
        B = self.block
        for s in range(0, len(y), B):
            yb = y[s:s + B]
            b = min(max(math.sqrt(ms2[s]) / SP_REF, B_MIN), B_MAX)
            b0, b1, a1 = spectral_coeffs(b, self.fs)
            ib0, ib1, ia1 = 1.0 / b0, a1 / b0, b1 / b0          # S^-1: zero and pole swapped
            vb, _ = signal.lfilter((ib0, ib1), (1.0, ia1), yb, zi=(ib1 * self.sx - ia1 * self.sy,))
            self.sx, self.sy = yb[-1], vb[-1]
            v[s:s + len(yb)] = vb
        self.a, self.b = float(a[-1]), b
        x, self.zf = signal.sosfilt(self.finv, v * a, zi=self.zf)
        return x
