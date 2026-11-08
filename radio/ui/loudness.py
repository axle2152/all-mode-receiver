"""
radio/ui/loudness.py - Loudness (ITU BS.1770-4 / EBU R128) from the meter block energies.
"""
import math
from collections import deque

import numpy as np

from .constants import LOUD_RECALC_S


# ---------------------------------------------------------------------------
# Loudness (BS.1770-4 / EBU R128) from the meter's 100 ms block energies
# ---------------------------------------------------------------------------

def _db(x, floor=-120.0):
    return 20 * math.log10(x) if x > 1e-12 else floor


def _lufs(e):
    return -0.691 + 10 * math.log10(e) if e is not None and e > 0 else None


class Loudness:
    ABS_GATE = -70.0
    KEEP_MAX = 144000                     # four hours of 100 ms steps

    def __init__(self):
        self.reset()

    def reset(self):
        self.blocks = deque(maxlen=30)    # the last 3 s of block energies
        self.mom = []                     # 400 ms energies, one per 100 ms, since reset
        self.short = []                   # 3 s energies, one per 100 ms, since reset
        self._cache, self._t = (None, None), -1e9

    def feed(self, energies, keep):
        for e in energies:
            self.blocks.append(e)
            if keep:
                b = list(self.blocks)
                if len(b) >= 4:
                    self.mom.append(sum(b[-4:]) / 4)
                if len(b) == 30:
                    self.short.append(sum(b) / 30)
        for lst in (self.mom, self.short):
            if len(lst) > self.KEEP_MAX:
                del lst[:len(lst) - self.KEEP_MAX]

    def momentary(self):
        b = list(self.blocks)[-4:]
        return _lufs(sum(b) / 4) if len(b) == 4 else None

    def short_term(self):
        return _lufs(sum(self.blocks) / 30) if len(self.blocks) == 30 else None

    def _gated(self, now):
        if now - self._t < LOUD_RECALC_S:
            return self._cache
        self._t = now
        integ = lra = None
        if len(self.mom) >= 4:
            e = np.asarray(self.mom)
            L = -0.691 + 10 * np.log10(np.maximum(e, 1e-20))
            a = e[L > self.ABS_GATE]
            if len(a):
                rel = _lufs(float(a.mean())) - 10.0
                b = e[(L > self.ABS_GATE) & (L > rel)]
                integ = _lufs(float(b.mean())) if len(b) else None
        if len(self.short) >= 20:
            e = np.asarray(self.short)
            L = -0.691 + 10 * np.log10(np.maximum(e, 1e-20))
            g = L > self.ABS_GATE
            if np.any(g):
                rel = _lufs(float(e[g].mean())) - 20.0
                v = L[g & (L > rel)]
                if len(v) >= 2:
                    lra = float(np.percentile(v, 95) - np.percentile(v, 10))
        self._cache = (integ, lra)
        return self._cache

    def integrated(self, now):
        return self._gated(now)[0]

    def lra(self, now):
        return self._gated(now)[1]
