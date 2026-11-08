"""
radio/ui/monitor.py - The monitor unit: what its meters show, and the audio-delay line.
"""
import math
from collections import deque

import numpy as np

from .constants import (
    CORR_TAU_S, FREQ_PEAK_RATE, MOD_DIST_S, MOD_HOLD_S, OFFSET_AVG_S, PEAK_HOLD_S, PEAK_SPAN,
    PEAK_STEP, PPM_FALL_DB_S, VU_TAU_S)
from .loudness import Loudness, _db


class DelayLine:
    """Values stamped with the time they were measured, read back as of an
    earlier time (requests move forward, so old entries are dropped as read)."""

    def __init__(self):
        self.q = deque()

    def push(self, t, v):
        self.q.append((t, v))
        if len(self.q) > 2000:
            self.q.popleft()

    def at(self, t):
        q = self.q
        while len(q) >= 2 and q[1][0] <= t:
            q.popleft()
        return q[0][1] if q else None


# ---------------------------------------------------------------------------
# Monitor arithmetic: ballistics, holds, overs, offsets. No Tk in here.
# ---------------------------------------------------------------------------

class MonitorLogic:
    def __init__(self, mode='ppm', hold=True):
        self.mode, self.hold_on = mode, hold
        self.loud = Loudness()
        self.reset()

    def reset(self):
        """A retune or the Reset key: start the program measurements over."""
        self.loud.reset()
        self.tp_max = None
        self.over_n = self.total_n = 0
        self.mod_hist = deque()               # (t, pct, raw, pos, neg)
        self.mod_maxpos = self.mod_maxneg = None
        self.off_hist = deque()               # (t, sum, n)
        self.last_over = -1e9
        self.ppm = [-120.0, -120.0]
        self.vu = [[0.0, 0.0], [0.0, 0.0]]
        self.hold = [(-120.0, 0.0), (-120.0, 0.0)]
        self.mod_bar, self.mod_bar_hold = 0.0, (0.0, 0.0)
        self.mod_valid = False
        self.corr, self.gonio = None, None
        self.peak_hist = deque()              # (t, histogram of peak heights, carrier offset)
        self._dist, self._dist_t, self._dist_side = None, -1e9, None

    def level(self, ch):
        return self.ppm[ch] if self.mode == 'ppm' else _db(self.vu[ch][1])

    def _hold_step(self, ch, now, dt):
        cur = self.level(ch)
        hv, ht = self.hold[ch]
        if cur >= hv:
            self.hold[ch] = (cur, now)
        elif now - ht > PEAK_HOLD_S:
            self.hold[ch] = (max(cur, hv - PPM_FALL_DB_S * dt), ht)

    def idle(self, now, dt):
        """No readings (settling, searching, off): let everything fall."""
        for ch in (0, 1):
            self.ppm[ch] = max(-120.0, self.ppm[ch] - PPM_FALL_DB_S * dt)
            a = 1 - math.exp(-dt / VU_TAU_S)
            s1 = self.vu[ch][0] * (1 - a)
            self.vu[ch] = [s1, self.vu[ch][1] + a * (s1 - self.vu[ch][1])]
            self._hold_step(ch, now, dt)
        self.mod_bar = max(0.0, self.mod_bar - 60.0 * dt)

    def feed(self, m, now, dt, band, keep, mod_valid):
        a, md = m['audio'], m['mod']
        if a['n']:
            al = 1 - math.exp(-dt / VU_TAU_S)
            for ch in (0, 1):
                self.ppm[ch] = max(_db(a['peak'][ch]), self.ppm[ch] - PPM_FALL_DB_S * dt)
                rms = math.sqrt(max(a['ms'][ch], 0.0))
                s1 = self.vu[ch][0] + al * (rms - self.vu[ch][0])
                self.vu[ch] = [s1, self.vu[ch][1] + al * (s1 - self.vu[ch][1])]
                self._hold_step(ch, now, dt)
            if a['corr'] is not None:
                c = 1 - math.exp(-dt / CORR_TAU_S)
                self.corr = a['corr'] if self.corr is None else self.corr + c * (a['corr'] - self.corr)
            self.gonio = a['gonio']
            if keep:
                tp = _db(max(a['true_peak']))
                self.tp_max = tp if self.tp_max is None else max(self.tp_max, tp)
                if tp > 0.0:
                    self.last_over = now
        else:
            self.idle(now, dt)
        self.loud.feed(a['blocks'], keep)

        self.mod_valid = bool(mod_valid)
        off = 0.0
        if band == 'FM' and md['n']:           # peaks from the carrier's centre, not from 0 Hz
            off = self.offset_khz()
            if off is None:
                off = md['mean'] or 0.0
        if md['n'] and mod_valid:
            pos, neg = md['max'] - off, off - md['min']
            if band == 'AM':                   # AM: the negative side (clicks only go up)
                pct = raw = neg
            if md.get('peaks') is not None:
                self.peak_hist.append((now, md['peaks'], off))
                while self.peak_hist and now - self.peak_hist[0][0] > MOD_DIST_S:
                    self.peak_hist.popleft()
            if band == 'FM':
                pct, raw = max(pos, neg) / 75.0 * 100.0, max(pos, neg)
            self.mod_hist.append((now, pct, raw, pos, neg))
            while self.mod_hist and now - self.mod_hist[0][0] > MOD_HOLD_S:
                self.mod_hist.popleft()
            self.mod_bar = max(pct, self.mod_bar - 60.0 * dt)
            hv, ht = self.mod_bar_hold
            if pct >= hv:
                self.mod_bar_hold = (pct, now)
            elif now - ht > PEAK_HOLD_S:
                self.mod_bar_hold = (max(pct, hv - 60.0 * dt), ht)
            if keep:
                self.over_n += md['over']
                self.total_n += md['n']
                if md['over']:
                    self.last_over = now
                self.mod_maxpos = pos if self.mod_maxpos is None else max(self.mod_maxpos, pos)
                self.mod_maxneg = neg if self.mod_maxneg is None else max(self.mod_maxneg, neg)
        else:
            self.mod_bar = max(0.0, self.mod_bar - 60.0 * dt)
            self.mod_bar_hold = (self.mod_bar, now)
            self.mod_hist.clear()
            self.peak_hist.clear()
        # The average frequency stays sound under noise and multipath, which only
        # spoil the peaks: carrier offset keeps measuring while tuned.
        if keep and band == 'FM' and md['n'] and md['mean'] is not None:
            self.off_hist.append((now, md['mean'] * md['n'], md['n']))
            while self.off_hist and now - self.off_hist[0][0] > OFFSET_AVG_S:
                self.off_hist.popleft()

    def mod_peak(self):
        """Highest peak in the hold window: (percent, display value, +peak, -peak)."""
        if not self.mod_hist:
            return None
        i = max(range(len(self.mod_hist)), key=lambda j: self.mod_hist[j][1])
        _, pct, raw, _, _ = self.mod_hist[i]
        return pct, raw, max(h[3] for h in self.mod_hist), max(h[4] for h in self.mod_hist)

    def distribution(self, now, side='both'):
        """(levels, peaks per second reaching each level, seconds covered), measured
        from the carrier's centre; None until 2 s have been counted. side='neg'
        counts only the troughs (AM's negative modulation)."""
        if now - self._dist_t < 0.25 and self._dist is not None and self._dist_side == side:
            return self._dist
        self._dist_t, self._dist_side = now, side
        if len(self.peak_hist) < 2 or now - self.peak_hist[0][0] < 2.0:
            self._dist = None
            return None
        nb = int(round(PEAK_SPAN / PEAK_STEP)) + 1
        centres = -PEAK_SPAN + (np.arange(int(round(2 * PEAK_SPAN / PEAK_STEP))) + 0.5) * PEAK_STEP
        mag = np.zeros(nb)
        for _, h, off in self.peak_hist:
            v = centres - off
            if side == 'neg':
                h = np.where(v < 0, h, 0)
            j = np.minimum((np.abs(v) / PEAK_STEP).astype(np.int64), nb - 1)
            mag += np.bincount(j, weights=h, minlength=nb)[:nb]
        secs = max(0.5, now - self.peak_hist[0][0])
        rate = np.cumsum(mag[::-1])[::-1] / secs                 # peaks per second at or above
        self._dist = (np.arange(nb) * PEAK_STEP, rate, secs)
        return self._dist

    def frequent_peak(self, now, side='both'):
        """Peaks of frequent recurrence: the highest level reached FREQ_PEAK_RATE times a second."""
        d = self.distribution(now, side)
        if d is None:
            return None
        levels, rate, _ = d
        ok = np.nonzero(rate >= FREQ_PEAK_RATE)[0]
        return float(levels[ok[-1]] + PEAK_STEP / 2) if len(ok) else None

    def offset_khz(self):
        n = sum(h[2] for h in self.off_hist)
        return sum(h[1] for h in self.off_hist) / n if n else None

    def over_pct(self):
        return 100.0 * self.over_n / self.total_n if self.total_n else None
