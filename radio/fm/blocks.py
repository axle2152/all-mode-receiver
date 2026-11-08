"""
radio/fm/blocks.py - the FM receiver's own GNU Radio blocks
===========================================================
_DipControl (static cut) and _NoiseReducer (spectral noise reduction).
"""
import math

import numpy as np
from gnuradio import gr


class _DipControl(gr.sync_block):
    """Static cut at a 2 kHz control rate (a plain loop is cheap).
    Inputs: the ultrasonic-noise ratio, and fast (2 ms) averages of |z|^2 and |z|^4
    on the channel being demodulated. Outputs: the top-end cut depth, driven by
    ultrasonic noise as before, and the stereo pull: twice that, or the fast
    multipath reading, whichever is more. FM has a constant envelope, so a quick
    ripple on it is echo or noise whatever the program is doing, and the fast
    circle CNR catches picket-fence bursts too short for the blend to follow.
    It only takes away what the blend curve says this instant can't carry beyond
    what the (slow) blend already removed, so a steady fair station isn't pulled twice."""

    def __init__(self, rate, attack_ms, release_ms, on_db, full_db, stereo_x, mono_db, full_st_db,
                 margin_db):
        gr.sync_block.__init__(self, "dip_control", [np.float32] * 3, [np.float32] * 2)
        self.aa = 1 - math.exp(-1000.0 / (rate * attack_ms))
        self.ar = 1 - math.exp(-1000.0 / (rate * release_ms))
        self.on, self.full, self.x = on_db, full_db, stereo_x
        self.mono_db, self.full_st_db, self.margin = mono_db, full_st_db, margin_db
        self.enabled, self.multipath = True, True
        self.blend = 1.0                               # the slow blend, set by update_blend()
        self.g, self.h = 0.0, 0.0
        self.activity, self.mp_activity = 0.0, 0.0

    def work(self, input_items, output_items):
        r, m2, m4 = input_items
        o, os_ = output_items
        d = np.clip((10 * np.log10(np.maximum(r, 1e-9)) - self.on) / (self.full - self.on), 0.0, 1.0)
        if self.multipath:
            sq = np.sqrt(np.maximum(2 * m2 * m2 - m4, 0.0))
            q = 10 * np.log10(np.maximum(sq, 1e-15) / np.maximum(m2 - sq, 1e-15)) + self.margin
            t = np.clip((q - self.mono_db) / (self.full_st_db - self.mono_db), 0.0, 1.0)
            t = t * t * (3.0 - 2.0 * t)                # the blend curve, this instant
            b = self.blend
            e = np.clip(1.0 - t / b, 0.0, 1.0) if b > 0.02 else np.zeros_like(d)
        else:
            e = np.zeros_like(d)
        if not self.enabled:
            d[:] = 0.0
            e[:] = 0.0
        g, h, aa, ar, x = self.g, self.h, self.aa, self.ar, self.x
        for i in range(len(d)):
            g += (aa if d[i] > g else ar) * (d[i] - g)
            h += (aa if e[i] > h else ar) * (e[i] - h)
            o[i] = g
            os_[i] = max(min(1.0, x * g), h)
        self.g, self.h = g, h
        if len(o):
            self.activity = 0.9 * self.activity + 0.1 * float(np.mean(os_[:len(o)]))
            self.mp_activity = 0.9 * self.mp_activity + 0.1 * h
        return len(o)


class _NoiseReducer(gr.sync_block):
    """Spectral noise reduction for L+R and L-R (inputs: MPX reference, L-R
    reference, L+R audio, L-R audio; outputs: cleaned L+R, L-R). Per-bin SNR comes
    from the references, where the MPX gap gives the exact noise level; the gains
    are dimensionless, so volume, de-emphasis and blend don't matter. Fixed delay
    of one frame; with depth 0 it is an exact pass-through."""

    # critical bands (Zwicker), up to the top of the L+R audio
    EDGES = [0, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320,
             2700, 3150, 3700, 4400, 5300, 6400, 7700, 9500, 12000, 15500]

    def __init__(self, n, rate, alpha=0.98, close_ms=100.0):
        gr.sync_block.__init__(self, "fm_nr", [np.float32] * 4, [np.float32] * 2)
        self.n, self.hop, self.alpha = n, n // 4, alpha
        self.a_close = 1 - math.exp(-(n // 4) / rate * 1000.0 / close_ms)
        k = np.arange(n)
        self.w = np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * k / n))     # sqrt periodic Hann
        self.f = np.fft.rfftfreq(n, 1.0 / rate)
        self.gap = (self.f >= 16000) & (self.f < 18500)
        self.gap2 = (self.f >= 19600) & (self.f < 22000)       # between the pilot and L-R
        self.f1 = float(np.mean(self.f[self.gap] ** 2))
        self.f2 = float(np.mean(self.f[self.gap2] ** 2))
        self.n1 = self.n2 = None
        self.band = self.f < 15500
        self.inbuf = np.zeros((4, 0), np.float32)
        self.ola = np.zeros((2, n))
        self.fifo = np.zeros((2, n), np.float32)                       # one frame of latency
        e = np.array(self.EDGES, dtype=float)
        self.bidx = np.clip(np.searchsorted(e, self.f, side='right') - 1, 0, len(e) - 2)
        self.nb = len(e) - 1
        self.centers = (e[:-1] + e[1:]) / 2
        self.G = np.ones((2, self.nb))           # per band, smoothed in time
        self.xi = np.ones((2, self.nb))
        self.flicker_db = 0.0                    # average frame-to-frame gain jump (warble)
        self.depth_db = 0.0
        self.reduction_db = 0.0                                        # what it's taking off, on average

    def _gains(self, ch, P, Nf, floor):
        """Wiener gains per critical band (steadier than per bin, like the ear),
        opening fast and closing slowly, then interpolated back to the bins."""
        sel = self.band
        Pb = np.bincount(self.bidx[sel], weights=P[sel], minlength=self.nb)
        Nb = np.bincount(self.bidx[sel], weights=Nf[sel], minlength=self.nb)
        gam = Pb / np.maximum(Nb, 1e-24)
        xi = self.alpha * self.G[ch] ** 2 * self.xi[ch] + (1 - self.alpha) * np.maximum(gam - 1, 0)
        g = np.maximum(xi / (1 + xi), floor)
        prev = self.G[ch]
        k = np.where(g > prev, 0.6, self.a_close)
        gs = prev + k * (g - prev)
        if ch == 0:
            jump = np.abs(20 * np.log10(np.maximum(gs, 1e-6)) - 20 * np.log10(np.maximum(prev, 1e-6)))
            self.flicker_db = 0.95 * self.flicker_db + 0.05 * float(np.mean(jump))
        self.G[ch], self.xi[ch] = gs, gam
        return np.interp(self.f, self.centers, gs)

    def work(self, input_items, output_items):
        n_out = len(output_items[0])
        self.inbuf = np.concatenate([self.inbuf, np.vstack([x[:n_out] for x in input_items])], axis=1)
        N, H, w = self.n, self.hop, self.w
        floor = 10 ** (-self.depth_db / 20)
        produced = []
        while self.inbuf.shape[1] >= N:
            fr = self.inbuf[:, :N]
            if floor >= 0.999:
                gm = gs = None
            else:
                Pm = np.abs(np.fft.rfft(fr[0] * w)) ** 2
                Ps = np.abs(np.fft.rfft(fr[1] * w)) ** 2
                # noise = b + a f^2, from two empty stretches of the MPX: flat (threshold
                # clicks) shows the same level in both, the f^2 rise above threshold doesn't
                n1, n2 = float(np.mean(Pm[self.gap])), float(np.mean(Pm[self.gap2]))
                self.n1 = n1 if self.n1 is None else 0.9 * self.n1 + 0.1 * n1
                self.n2 = n2 if self.n2 is None else 0.9 * self.n2 + 0.1 * n2
                a = max((self.n2 - self.n1) / (self.f2 - self.f1), 0.0)
                b = max(self.n1 - a * self.f1, 0.0)
                Nm = b + a * self.f ** 2                                               # L+R noise
                Ns = 2 * b + a * ((38e3 + self.f) ** 2 + (38e3 - self.f) ** 2)         # L-R noise
                gm, gs = self._gains(0, Pm, Nm, floor), self._gains(1, Ps, Ns, floor)
                self.reduction_db = 0.9 * self.reduction_db + 0.1 * float(-20 * np.log10(np.mean(gm[self.band])))
            for ch, g in ((0, gm), (1, gs)):
                X = np.fft.rfft(fr[2 + ch] * w)
                y = np.fft.irfft(X * g if g is not None else X, N) * w
                self.ola[ch] += y
            produced.append(self.ola[:, :H] / 2.0)                    # Hann^2 at 75% overlap sums to 2
            self.ola = np.concatenate([self.ola[:, H:], np.zeros((2, H))], axis=1)
            self.inbuf = self.inbuf[:, H:]
        if floor >= 0.999:
            self.reduction_db *= 0.9
        if produced:
            self.fifo = np.concatenate([self.fifo] + produced, axis=1)
        output_items[0][:] = self.fifo[0, :n_out]
        output_items[1][:] = self.fifo[1, :n_out]
        self.fifo = self.fifo[:, n_out:]
        return n_out
