"""
radio/blanker.py - the impulse and noise blankers (AM broadcast, shortwave, SSB and CW)
=====================================================================================
A noise pulse (a spark, an ignition, a static crash) is very short and very tall. Patched out
before a filter, it costs a few microseconds of signal; left in, every filter after it stretches
it into a tick or a crash as long as the filter's own response. So the blankers work ahead of
the filters, on the samples themselves, and the noise blanker on the dongle's full 960 ksps,
where a pulse is tallest and shortest.

But that full bandwidth holds every station within 480 kHz, and on medium wave the locals can
be 30 dB stronger than the station listened to. A pulse loud in that station's channel can be
smaller than them, so measured against the band's level it can't be seen; and a patch that
drew a straight line across the gap would chop the locals, whose splatter lands in the channel
as a click of its own. (The first version did both: on a band with locals it patched pulses and
made no difference, or made it worse. The tests now include such a band.) So the noise blanker
predicts the band:

  predict  linear prediction, ORDER samples back, fitted every 40 ms to what it gave out
           (the autocorrelation and Levinson's recursion): the stations are steady tones and
           slow modulation, so it predicts them closely; noise and pulses it can't
  detect   a pulse is where the prediction misses by more than K times its median miss. The
           forward prediction is clean up to a pulse, the backward one (the same, run from the
           future) clean after it, so the pulse runs from where the forward miss first crosses
           the line to where the backward miss last does; misses where only one of them
           crosses are a pulse's wake
  spare    a pulse is no longer than MAX_RUN samples, and doesn't sit in a long stretch over
           half the line (a strong signal arriving, a syllable): those are left alone
  widen    PRE samples before the pulse, POST after
  patch    the prediction carried across the gap from the samples before it, and backward
           from those after, faded from one to the other, so every station carries on through
           it; and only if the pulse outweighs the error such a patch makes (GATE)
  back off with a BACKOFF_ABOVE share of the time blanked, the line rises by itself until
           it's under that again, so a blanker set too keen can't eat the program

The AM impulse blanker looks by level instead (detect='level'): the envelope on the 48 kHz
channel over +300% of the carrier (its median), which no legal program reaches, patched by
prediction too (a straight line there would chop the stations 10 kHz either side).

BlankerCore is that arithmetic, numpy only (tests/test_blanker.py runs it without GNU Radio);
BlankerBlock puts it in a flowgraph. blanker_check.py runs it over a recording from the dongle.

GlitchGateCore / GlitchGateBlock mute the AM stereo difference signal for a moment around a
glitch in the carrier (the envelope all but gone, or the phase further round than C-QUAM ever
puts it), which tan(phase) would otherwise turn into a loud click in one channel.
"""

import collections
import math
import threading

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

try:
    from gnuradio import gr
except ImportError:                      # the arithmetic still works for testing
    gr = None


class EventRate:
    """Events a minute, over about the last TAU seconds (a leaky count: one event reads 60/TAU
    a minute at once and fades away; a steady rate reads true)."""

    def __init__(self, tau=15.0):
        self.tau, self.per_min = float(tau), 0.0

    def add(self, n, dt):
        self.per_min = self.per_min * math.exp(-dt / self.tau) + n * 60.0 / self.tau


def describe(impulse, noise):
    """'impulse + noise MED', 'impulse', 'noise LOW' or 'off', for a receiver's readout."""
    on = (['impulse'] if impulse else []) + ([f'noise {noise.upper()}'] if noise != 'off' else [])
    return ' + '.join(on) or 'off'


def levinson(r, p):
    """The prediction-error filter [1, a1 .. ap] for the autocorrelation r[0 .. p] (complex), and
    the power of what's left unpredicted."""
    a = np.zeros(p + 1, np.complex128)
    a[0] = 1.0
    e = float(r[0].real)
    for i in range(1, p + 1):
        k = -np.dot(a[:i], r[i:0:-1]) / e
        if not abs(k) < 0.9999:          # rounding has run out: stop at this order
            break
        a[1:i] = a[1:i] + k * np.conj(a[i - 1:0:-1])
        a[i] = k
        e *= 1.0 - abs(k) ** 2
    return a, e


class BlankerCore:
    """The blanker's arithmetic. process(x) takes complex samples and returns as many, patched,
    `delay` samples late. set_k(k) sets the line (None: off, the samples pass untouched).

    detect='predict': pulses are found in what the prediction misses (the noise blanker);
    detect='level': in the magnitude over its median (the AM impulse blanker's +300%)."""

    BACKOFF_ABOVE = 0.01        # blanking more than this share of the time raises the line,
    BACKOFF_BELOW = 0.005       # below this it settles back
    BACKOFF_MAX   = 4.0         # at most this far (12 dB)
    REF_EVERY     = 4           # the level median looks at every 4th sample: plenty, and cheaper
    FIT_N         = 4096        # the prediction is fitted to the last this many samples given out,
    FIT_EVERY     = 4           # every this many calls (40 ms in a flowgraph): the band changes slowly

    def __init__(self, rate, pre, post, max_run, detect='predict', order=24, gate=4.0,
                 release_s=0.1, backoff=False, tau_s=15.0):
        self.rate = float(rate)
        self.pre, self.post, self.max_run = int(pre), int(post), int(max_run)
        self.detect, self.p, self.gate = detect, int(order), float(gate)
        # look-ahead: enough to see a whole pulse and the prediction's worth of samples after it
        self.delay = self.max_run + self.pre + self.post + 2 * self.p + 16
        self._T = self.delay + self.max_run + 2 * self.p + 64      # samples kept
        self._xt = np.zeros(self._T, np.complex64)
        self._yf = np.zeros(max(self._T, self.FIT_N), np.complex64)
        self._abs0 = -self._T                # the sample number of _xt[0]
        self._carry = None                   # (sample number, patched values) past the last output
        self.a = self.err = self._filt = None
        self._fit_wait = 0
        self.release_s = float(release_s)
        self.backoff_on = bool(backoff)
        self.backoff = 1.0
        self.k = None                        # the line over the reference (None: off)
        self.k_big = None                    # pulses over this count as impulses (HF)
        self.ref = None
        self._ref_was = collections.deque()  # level: (reference, samples) of the last calls
        self._ref_n = 0
        self.frac = 0.0                      # share of the time blanked, about the last second
        self.pulses = EventRate(tau_s)       # pulses patched a minute
        self.big = EventRate(tau_s)          # of those, over k_big
        self.n_pulses = self.n_big = self.n_blanked = self.n_skipped = 0     # totals (tests)
        self.n_out = 0                       # samples given out
        self.events = None                   # a list to keep (sample, gap length, peak over the
                                             # reference) for each pulse (blanker_check.py)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- controls
    def set_k(self, k, k_big=None):
        with self._lock:
            self.k = None if k is None else float(k)
            self.k_big = None if k_big is None else float(k_big)
            self.backoff = 1.0

    def reset(self):
        """A retune: a new band to learn, and its level, from the next samples on."""
        with self._lock:
            self.ref = None
            self.a = self.err = self._filt = None
            self._ref_was.clear()
            self._ref_n = 0
            self.backoff = 1.0

    def threshold(self):
        """The line now, over the reference, after any backing off (None: off)."""
        if self.k is None:
            return None
        k = self.k * self.backoff
        return k if self.k_big is None else min(k, self.k_big)

    # ---------------------------------------------------------------- work
    def process(self, x):
        x = np.asarray(x, np.complex64)
        n = len(x)
        if n == 0:
            return np.zeros(0, np.complex64)
        with self._lock:
            return self._process(x, n)

    def _process(self, x, n):
        T, D = self._T, self.delay
        buf = np.concatenate((self._xt, x))
        L = len(buf)
        o0, o1 = T - D, T - D + n                       # this call gives out buf[o0:o1]
        y = buf.copy()
        y[:o0] = self._yf[-o0:]                         # what was given out already
        if self._carry is not None:                     # a patch that ran past the last output
            s, v = self._carry
            j = s - self._abs0
            y[j:j + len(v)] = v[:L - j]
            self._carry = None
        k = self.threshold()
        found = 0
        if k is not None:
            d, ef, ref = self._detector(buf, o0, o1)
            if d is not None and ref > 0:
                line = k * ref
                hi = d > line
                if hi[max(0, o0 - self.post):min(L, o1 + self.pre + self.max_run)].any():
                    found = self._blank(buf, y, d, ef, hi, line, ref, o0, o1)
        out = y[o0:o1].copy()
        blanked = int(np.count_nonzero(out != buf[o0:o1]))
        self._yf = np.concatenate((self._yf, out))[-len(self._yf):]
        self._xt = buf[-T:]
        self._abs0 += n
        self.n_out += n
        if k is not None:
            self._fit()
        dt = n / self.rate
        if not found:
            self.pulses.add(0, dt)
            self.big.add(0, dt)
        self.n_blanked += blanked
        self.frac += (1.0 - math.exp(-dt / 1.0)) * (blanked / n - self.frac)
        if self.backoff_on and self.k is not None:
            if self.frac > self.BACKOFF_ABOVE:
                self.backoff = min(self.BACKOFF_MAX, self.backoff * (1.0 + 2.0 * dt))
            elif self.frac < self.BACKOFF_BELOW and self.backoff > 1.0:
                self.backoff = max(1.0, self.backoff * (1.0 - 0.1 * dt))
        return out

    def _detector(self, buf, o0, o1):
        """What pulses are looked for in, sample by sample; the prediction error (or None); and
        the reference the line stands over."""
        n, L, p = o1 - o0, len(buf), self.p
        if self.detect == 'level':
            d = np.abs(buf)
            med = float(np.median(d[L - n:][::self.REF_EVERY]))
            dt = n / self.rate
            if self.ref is None or med > self.ref:
                self.ref = med
            else:
                self.ref += (1.0 - math.exp(-dt / self.release_s)) * (med - self.ref)
            # the line rises late: no higher than the reference was over the last 4 x MAX_RUN
            # samples (at least the last call), so a stretch begun under it can show it's long
            ref, back = self.ref, 0
            for r, m in reversed(self._ref_was):
                ref = min(ref, r)
                back += m
                if back >= 4 * self.max_run:
                    break
            self._ref_was.append((self.ref, n))
            self._ref_n += n
            while self._ref_n - self._ref_was[0][1] >= 4 * self.max_run:
                self._ref_n -= self._ref_was.popleft()[1]
            return d, None, ref
        if self._filt is None:                          # at the start, or a retune: learn the band
            self._fit(buf[-self.FIT_N:])                # from these samples at once
            if self._filt is None:
                return None, None, 0.0
        # forward and backward prediction errors at once: each window of p+1 samples against
        # the filter both ways (a matrix product, so the maths library does the work)
        r = sliding_window_view(buf, p + 1) @ self._filt
        ef = np.zeros(L, np.float32)
        eb = np.zeros(L, np.float32)
        ef[p:] = np.abs(r[:, 0])
        eb[:L - p] = np.abs(r[:, 1])
        ef[:p] = eb[L - p:] = 0.0
        fresh = L - (self.n_out + n)                    # at the very start, the memory before
        if fresh > 0:                                   # the first sample is empty: the stream
            ef[:fresh + p] = eb[:fresh + p] = 0.0       # beginning isn't a pulse
        med = float(np.median(ef[o0:o1:2]))
        self.ref = med if self.ref is None else 0.8 * self.ref + 0.2 * med
        self._eb = eb
        return np.maximum(ef, eb), ef, self.ref

    def _blank(self, buf, y, d, ef, hi, line, ref, o0, o1):
        """Find the pulses that begin in this output, patch them; how many."""
        L = len(buf)
        lo = d > 0.5 * line
        dd = np.diff(np.concatenate(([0], lo.view(np.int8), [0])))
        starts, ends = np.flatnonzero(dd == 1), np.flatnonzero(dd == -1)
        spans = []
        if ef is None:
            # by level: a stretch over half the line that reaches the line, if short enough
            chi = np.concatenate(([0], np.cumsum(hi, dtype=np.int64)))
            keep = ((chi[ends] - chi[starts]) > 0) & ((ends - starts) <= self.max_run)
            spans = [(int(a), int(b)) for a, b in zip(starts[keep], ends[keep])]
        else:
            # by prediction: the prediction runs on past a pulse with it in its memory, and
            # inside a smooth or flat pulse it follows the pulse itself, so the samples over the
            # line come in clusters around each pulse. A pulse starts where the forward error
            # (clean before it) first crosses and ends where the backward error (clean after
            # it) last does; a cluster where only one of them crosses is a pulse's wake. One
            # that sits in a long stretch over half the line is a signal, not a pulse.
            idx = np.flatnonzero(hi)
            join = self.max_run // 2
            brk = np.flatnonzero(np.diff(idx) > join)
            reach = 2 * self.max_run + 2 * self.p
            for g0, g1 in zip(idx[np.concatenate(([0], brk + 1))], idx[np.concatenate((brk, [len(idx) - 1]))]):
                k0 = int(np.searchsorted(ends, g0, side='right'))
                k1 = int(np.searchsorted(starts, g1, side='right')) - 1
                if ends[k1] - starts[k0] > reach:
                    continue
                f = np.flatnonzero(ef[g0:g1 + 1] > line)
                b = np.flatnonzero(self._eb[g0:g1 + 1] > line)
                if not (len(f) and len(b)) or f[0] > b[-1]:
                    continue
                a, e = int(g0 + f[0]), int(g0 + b[-1]) + 1
                if e - a <= self.max_run:
                    spans.append((a, e))
        gaps = []
        for a, e in spans:
            gs, ge = max(0, a - self.pre), min(L, e + self.post)
            if gaps and gs <= gaps[-1][1]:
                gaps[-1][1] = ge
            else:
                gaps.append([gs, ge])
        found = big = 0
        dt = (o1 - o0) / self.rate
        for gs, ge in gaps:
            if not o0 <= gs < o1:
                continue                                # decided before, or next time
            if ef is not None and self.gate:
                # worth it only if the pulse outweighs the error a patch of that length makes:
                # what the prediction misses on clean samples (the median miss is sqrt(ln 2) of
                # its RMS, for noise)
                if float(np.sum(ef[gs:ge] ** 2)) < self.gate * (ge - gs) * ref ** 2 / math.log(2):
                    self.n_skipped += 1
                    continue
            self._patch(y, buf, gs, ge)
            found += 1
            peak = float(d[gs:ge].max()) / ref
            big += int(self.k_big is not None and peak > self.k_big)
            if self.events is not None:
                self.events.append((self._abs0 + gs, ge - gs, peak))
            if ge > o1:
                self._carry = (self._abs0 + o1, y[o1:ge].copy())
        self.n_pulses += found
        self.n_big += big
        self.pulses.add(found, dt)
        self.big.add(big, dt)
        return found

    def _patch(self, y, buf, gs, ge):
        """Carry the band across the gap: the prediction run forward from the samples before it
        and backward from those after, faded from one to the other. (Before there's a
        prediction: a straight line.)"""
        g, p, L = ge - gs, self.p, len(buf)
        if self.a is None or gs < p:
            left = complex(y[gs - 1]) if gs > 0 else 0j
            right = complex(buf[ge]) if ge < L else left
            t = (np.arange(g) + 1) / (g + 1)
            y[gs:ge] = (left + (right - left) * t).astype(np.complex64)
            return
        c = -self.a[1:]                                 # x[n] is about sum c_k x[n - k]
        cr = c[::-1]
        f = np.concatenate((y[gs - p:gs].astype(np.complex128), np.zeros(g, np.complex128)))
        for i in range(g):
            f[p + i] = np.dot(cr, f[i:i + p])
        if ge + p <= L:
            b = np.concatenate((np.zeros(g, np.complex128), buf[ge:ge + p].astype(np.complex128)))
            cc = np.conj(c)
            for i in range(g - 1, -1, -1):
                b[i] = np.dot(cc, b[i + 1:i + 1 + p])
            w = (np.arange(g) + 1) / (g + 1)
            y[gs:ge] = ((1 - w) * f[p:] + w * b[:g]).astype(np.complex64)
        else:
            y[gs:ge] = f[p:].astype(np.complex64)

    def _fit(self, y=None):
        """The prediction, from the last FIT_N samples given out (patched, so pulses don't spoil
        it), or from y: the autocorrelation, then Levinson's recursion."""
        if y is None:
            self._fit_wait -= 1
            if self._fit_wait > 0 and self.a is not None:
                return
            self._fit_wait = self.FIT_EVERY
            y = self._yf[-self.FIT_N:]
        p = self.p
        n = len(y)
        r = np.array([np.vdot(y[:n - k], y[k:]) for k in range(p + 1)], np.complex128) / n
        if not r[0].real > 1e-20:
            return
        r[0] *= 1.0 + 1e-4                              # a whisker of white noise: keeps it stable
        a, err = levinson(r, p)
        if not (np.all(np.isfinite(a)) and err > 0):
            return
        self.a, self.err = a, err
        self._filt = np.stack((self.a[::-1], np.conj(self.a)), axis=1).astype(np.complex64)

    def tick(self, dt):
        """Called when no samples came (the rates still fade)."""
        with self._lock:
            self.pulses.add(0, dt)
            self.big.add(0, dt)


class GlitchGateCore:
    """Mutes the AM stereo difference signal for a moment around a carrier glitch. process(lr,
    env, ph): lr is the difference signal, `lead` samples behind env (the envelope over the
    carrier) and ph (the carrier phase), so the gate closes before a glitch reaches it. No
    delay of its own.

    A glitch: the envelope all but gone (over-modulation, a deep fade: the phase there is noise,
    and it flips over as the envelope passes through nothing), or the phase further round than
    C-QUAM ever puts it (a carrier phase hit, or the carrier lock thrown off). Either way
    tan(phase) would turn it into a loud click in one channel."""

    ENV_MIN   = 0.1            # the envelope below a tenth of the carrier
    PHASE_MAX = 1.3            # radians (75 degrees): C-QUAM needs about 1.1 for a full-scale
                               # left or right alone; the decoder clips at 1.2 anyway
    PRE_S, POST_S, RAMP_S = 0.001, 0.002, 0.0005

    def __init__(self, rate, lead, tau_s=15.0):
        self.rate, self.lead = float(rate), int(lead)
        self.pre = max(1, int(round(self.PRE_S * rate)))
        self.post = max(1, int(round(self.POST_S * rate)))
        self.ramp = max(2, int(round(self.RAMP_S * rate)))
        if self.pre + self.ramp > self.lead:
            raise ValueError('the gate needs more lead than it has')
        self._hist_n = self.lead + self.post + 2 * self.ramp
        self._flags = np.zeros(self._hist_n, bool)
        self._was_shut = False
        self.on = False
        self.counting = False          # set while the stereo is heard: only then is a glitch news
        self.glitches = EventRate(tau_s)
        self.n_glitches = 0
        self._lock = threading.Lock()

    def process(self, lr, env, ph):
        lr = np.asarray(lr, np.float32)
        n = len(lr)
        if n == 0:
            return lr
        env = np.asarray(env, np.float32)
        ph = np.asarray(ph, np.float32)
        with self._lock:
            new = ((env < self.ENV_MIN) | (np.abs(ph) > self.PHASE_MAX)) if self.on else np.zeros(n, bool)
            fl = np.concatenate((self._flags, new))
            self._flags = fl[-self._hist_n:]
            H = self._hist_n
            j0 = H - self.lead                   # lr[j] lines up with fl[j0 + j]
            if not fl[j0 - self.post - self.ramp:j0 + n + self.pre + self.ramp].any():
                self._was_shut = False
                self.glitches.add(0, n / self.rate)
                return lr
            cs = np.concatenate(([0], np.cumsum(fl, dtype=np.int64)))
            a = np.arange(j0 - self.ramp, j0 + n + self.ramp)
            shut = (cs[np.minimum(a + self.pre + 1, len(fl))] - cs[np.maximum(a - self.post, 0)]) > 0
            g = 1.0 - shut.astype(np.float32)
            gate = np.convolve(g, np.ones(2 * self.ramp + 1) / (2 * self.ramp + 1), 'valid')[:n]
            s = shut[self.ramp:self.ramp + n]
            d = np.diff(np.concatenate(([1 if self._was_shut else 0], s.view(np.int8))))
            new_events = int(np.sum(d == 1))
            self._was_shut = bool(s[-1])
            if self.counting:
                self.n_glitches += new_events
                self.glitches.add(new_events, n / self.rate)
            else:
                self.glitches.add(0, n / self.rate)
            return (lr * gate).astype(np.float32)


if gr is not None:
    class BlankerBlock(gr.sync_block):
        """complex in -> complex out, through BlankerCore, `core.delay` samples late."""

        def __init__(self, rate, **kw):
            gr.sync_block.__init__(self, name='blanker', in_sig=[np.complex64],
                                   out_sig=[np.complex64])
            self.core = BlankerCore(rate, **kw)

        def work(self, input_items, output_items):
            y = self.core.process(input_items[0])
            output_items[0][:len(y)] = y
            return len(y)

    class GlitchGateBlock(gr.sync_block):
        """(difference signal, envelope over carrier, carrier phase) -> gated difference."""

        def __init__(self, rate, lead, **kw):
            gr.sync_block.__init__(self, name='glitch_gate',
                                   in_sig=[np.float32, np.float32, np.float32],
                                   out_sig=[np.float32])
            self.core = GlitchGateCore(rate, lead, **kw)

        def work(self, input_items, output_items):
            y = self.core.process(input_items[0], input_items[1], input_items[2])
            output_items[0][:len(y)] = y
            return len(y)
else:
    BlankerBlock = GlitchGateBlock = None


__all__ = ['BlankerCore', 'BlankerBlock', 'GlitchGateCore', 'GlitchGateBlock', 'EventRate', 'describe']
