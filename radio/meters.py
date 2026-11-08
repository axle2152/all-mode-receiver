"""
radio/meters.py
===============
Read-only metering taps for the SDR receivers. They sit beside the audio
path inside the flowgraph and only listen, so the monitor sees every
sample: no peak is missed between snapshots, and loudness is measured on
the continuous program the way BS.1770 expects.

AudioMeterBlock   station audio (before volume / soft mute / tone):
                  K-weighted 100 ms block energies for loudness (momentary,
                  short-term, integrated, LRA are worked out from these by
                  the UI), sample peak, 4x oversampled true peak, mean
                  square (VU), L/R correlation sums, and the last samples
                  for a goniometer.
ModMeterBlock     the demodulated composite (FM, in kHz of deviation) or
                  the AM envelope (percent modulation): highest and lowest
                  value, mean (FM carrier offset), how many samples went
                  past the over-modulation limits, and a histogram of the
                  height of every peak and trough (for "peaks of frequent
                  recurrence", the way the FCC words the limit).

Each block's read() returns what has happened since the previous read().
"""

import math
import threading
from collections import deque

import numpy as np

try:
    from gnuradio import gr
except ImportError:                      # the numeric cores still work for testing
    gr = None


BLOCK_S       = 0.1        # loudness block: the BS.1770 gating step
PEAK_STEP     = 0.25       # peak-height histogram: bin width in display units (kHz or %)
PEAK_SPAN     = 150.0      # ... covering -PEAK_SPAN .. +PEAK_SPAN
GONIO_N       = 1024       # samples kept for the goniometer
TP_OVERSAMPLE = 4
TP_TAPS       = 48         # BS.1770-4 Annex 2 uses a 48-tap, 4-phase interpolator


def k_weighting(fs):
    """BS.1770 K-weighting as two biquads (b1, a1, b2, a2), scipy convention
    (a[0] = 1, denominator 1 + a1 z^-1 + a2 z^-2), for any sample rate.
    At 48 kHz these reproduce the coefficients printed in the standard."""
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / fs)
    vh = 10 ** (gain_db / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    b1 = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]
    a1 = [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / fs)
    a0 = 1 + k / q + k * k
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    return b1, a1, b2, a2


def _tp_phases():
    """Polyphase 4x interpolator (windowed sinc), each phase normalised to unity gain."""
    n = np.arange(TP_TAPS) - (TP_TAPS - 1) / 2
    h = np.sinc(n / TP_OVERSAMPLE) * np.kaiser(TP_TAPS, 5.0)
    phases = [h[p::TP_OVERSAMPLE] for p in range(TP_OVERSAMPLE)]
    return [p / np.sum(p) for p in phases]


class AudioMeterCore:
    """The audio meter's arithmetic, independent of GNU Radio.
    Inputs are scaled by 1/ref, so 1.0 = full scale (100% modulation)."""

    def __init__(self, rate, ref=1.0):
        self.rate = int(rate)
        self.inv = 1.0 / float(ref)
        self.block_n = max(1, int(round(self.rate * BLOCK_S)))
        self._phases = _tp_phases()
        self._hist_n = len(self._phases[0]) - 1
        self._hist = np.zeros((2, self._hist_n), np.float64)
        self._acc, self._acc_n = 0.0, 0
        self._blocks = deque(maxlen=600)          # a minute of backlog at most
        self._gonio = np.zeros((2, GONIO_N), np.float32)
        self._gpos = 0
        self._lock = threading.Lock()
        self._clear()

    def _clear(self):
        self._peak = [0.0, 0.0]
        self._tpeak = [0.0, 0.0]
        self._ss = [0.0, 0.0]
        self._slr = 0.0
        self._n = 0

    def _true_peak(self, ch, x):
        xe = np.concatenate((self._hist[ch], x))
        self._hist[ch] = xe[-self._hist_n:]
        tp = float(np.max(np.abs(x))) if len(x) else 0.0
        for ph in self._phases:
            y = np.convolve(xe, ph, mode='valid')
            if len(y):
                tp = max(tp, float(np.max(np.abs(y))))
        return tp

    def process(self, l, r, lk, rk):
        n = len(l)
        if n == 0:
            return
        s = self.inv
        l = np.asarray(l, np.float64) * s
        r = np.asarray(r, np.float64) * s
        lk = np.asarray(lk, np.float64) * s
        rk = np.asarray(rk, np.float64) * s
        e = lk * lk + rk * rk                     # channel weights are 1.0 for L and R
        tl, tr = self._true_peak(0, l), self._true_peak(1, r)
        with self._lock:
            i = 0
            while i < n:
                take = min(n - i, self.block_n - self._acc_n)
                self._acc += float(np.sum(e[i:i + take]))
                self._acc_n += take
                i += take
                if self._acc_n >= self.block_n:
                    self._blocks.append(self._acc / self._acc_n)
                    self._acc, self._acc_n = 0.0, 0
            self._peak[0] = max(self._peak[0], float(np.max(np.abs(l))))
            self._peak[1] = max(self._peak[1], float(np.max(np.abs(r))))
            self._tpeak[0] = max(self._tpeak[0], tl)
            self._tpeak[1] = max(self._tpeak[1], tr)
            self._ss[0] += float(np.dot(l, l))
            self._ss[1] += float(np.dot(r, r))
            self._slr += float(np.dot(l, r))
            self._n += n
            m = min(n, GONIO_N)
            idx = (self._gpos + np.arange(m)) % GONIO_N
            self._gonio[0, idx] = l[-m:]
            self._gonio[1, idx] = r[-m:]
            self._gpos = (self._gpos + m) % GONIO_N

    def read(self):
        with self._lock:
            n = self._n
            out = dict(
                n=n,
                peak=tuple(self._peak),
                true_peak=tuple(self._tpeak),
                ms=(self._ss[0] / n, self._ss[1] / n) if n else (0.0, 0.0),
                corr=(self._slr / math.sqrt(self._ss[0] * self._ss[1])
                      if self._ss[0] > 1e-12 and self._ss[1] > 1e-12 else None),
                blocks=list(self._blocks),
                gonio=np.roll(self._gonio, -self._gpos, axis=1).copy(),
            )
            self._blocks.clear()
            self._clear()
        return out


class ModMeterCore:
    """Peaks of the composite (FM, kHz) or envelope (AM, percent) since the last read."""

    NB = int(round(2 * PEAK_SPAN / PEAK_STEP))

    def __init__(self, scale=1.0, over_pos=75.0, over_neg=75.0):
        self.scale = float(scale)
        self.over_pos, self.over_neg = float(over_pos), float(over_neg)
        self._lock = threading.Lock()
        self._tail = np.zeros(0)
        self._clear()

    def _clear(self):
        self._max, self._min = -1e30, 1e30
        self._sum, self._n, self._over = 0.0, 0, 0
        self._peaks = np.zeros(self.NB, np.int64)

    def process(self, x):
        if len(x) == 0:
            return
        y = np.asarray(x, np.float64) * self.scale
        hi, lo = float(np.max(y)), float(np.min(y))
        over = int(np.count_nonzero(y > self.over_pos) + np.count_nonzero(y < -self.over_neg))
        tot = float(np.sum(y))
        # every local peak (above zero) and trough (below), carried across calls
        z = np.concatenate((self._tail, y))
        self._tail = z[-2:]
        a, b, c = z[:-2], z[1:-1], z[2:]
        ext = np.concatenate((b[(b > a) & (b >= c) & (b > 0)], b[(b < a) & (b <= c) & (b < 0)]))
        idx = np.clip(((ext + PEAK_SPAN) / PEAK_STEP).astype(np.int64), 0, self.NB - 1)
        hist = np.bincount(idx, minlength=self.NB)
        with self._lock:
            self._max = max(self._max, hi)
            self._min = min(self._min, lo)
            self._sum += tot
            self._n += len(y)
            self._over += over
            self._peaks += hist

    def read(self):
        with self._lock:
            n = self._n
            out = dict(n=n, max=self._max if n else None, min=self._min if n else None,
                       mean=self._sum / n if n else None, over=self._over, peaks=self._peaks)
            self._clear()
        return out


if gr is not None:
    class AudioMeterBlock(gr.sync_block):
        """Sink: in 0/1 = L/R audio, in 2/3 = the same K-weighted."""

        def __init__(self, rate, ref=1.0):
            gr.sync_block.__init__(self, name='audio_meter',
                                   in_sig=[np.float32] * 4, out_sig=None)
            self.core = AudioMeterCore(rate, ref)

        def work(self, input_items, output_items):
            self.core.process(input_items[0], input_items[1], input_items[2], input_items[3])
            return len(input_items[0])

        def read(self):
            return self.core.read()

    class ModMeterBlock(gr.sync_block):
        """Sink: the composite (FM) or envelope - 1 (AM), scaled into display units."""

        def __init__(self, scale=1.0, over_pos=75.0, over_neg=75.0):
            gr.sync_block.__init__(self, name='mod_meter', in_sig=[np.float32], out_sig=None)
            self.core = ModMeterCore(scale, over_pos, over_neg)

        def work(self, input_items, output_items):
            self.core.process(input_items[0])
            return len(input_items[0])

        def read(self):
            return self.core.read()
else:
    AudioMeterBlock = ModMeterBlock = None
