"""
radio/blanker.py - the impulse and noise blankers (AM broadcast, shortwave, SSB and CW)
=====================================================================================
A noise pulse (a spark, an ignition, a static crash) is very short and very tall. Patched out
before a filter, it costs a few microseconds of signal; left in, every filter after it stretches
it into a tick or a crash as long as the filter's own response. So the blankers work ahead of
the filters, on the samples themselves:

  detect   a sample whose magnitude stands more than K times over the reference, the median
           magnitude of the last 10 ms or so (a median, so the pulses themselves can't lift it).
           It rises with the signal, a little late (so a signal rising through the line isn't
           cut short where the samples happen to be chopped up), and falls back over RELEASE_S
  measure  a pulse is the stretch around it over half the line (so a signal rising slowly
           through the line, with the noise flicking it back and forth across, is one long
           stretch, not a string of little pulses)
  spare    a stretch longer than MAX_RUN samples isn't a pulse (a strong signal arriving, a
           syllable, a fade coming up): it's left alone
  widen    blank from PRE samples before a pulse's stretch to POST samples after it, to take
           in its rise and its ringing; the output runs a little late so the blanker can see a
           pulse coming
  patch    the blanked samples are replaced by a straight line from the last good sample before
           them to the first good one after (on the complex signal, so the carrier and the
           stations near it carry on through the gap instead of dropping to nothing)
  back off with a BACKOFF_ABOVE share of the time blanked, the threshold rises by itself until
           it's under that again, so a blanker set too keen can't eat the program

BlankerCore is that arithmetic, numpy only (tests/test_blanker.py runs it without GNU Radio);
BlankerBlock puts it in a flowgraph. The receivers use it twice over:

  noise blanker    on the dongle's full stream (960 ksps), where a pulse is tallest against
                   the signals (the wider the bandwidth, the taller and shorter a pulse);
                   LOW, MED and HIGH set K
  impulse blanker  AM: on the 48 kHz channel ahead of the carrier lock, envelope over +300% of
                   the carrier (no legal program goes there). HF (SSB and CW have no carrier
                   to measure against): on the full stream, only pulses far over the band

GlitchGateCore / GlitchGateBlock mute the AM stereo difference signal for a moment around a
glitch in the carrier (the envelope all but gone, or the phase further round than C-QUAM ever
puts it), which tan(phase) would otherwise turn into a loud click in one channel.
"""

import collections
import math
import threading

import numpy as np

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


class BlankerCore:
    """The blanker's arithmetic. process(x) takes complex samples and returns as many, patched,
    `delay` samples late. set_k(k) sets the threshold (None: off, the samples pass untouched)."""

    BACKOFF_ABOVE = 0.01        # blanking more than this share of the time raises the threshold,
    BACKOFF_BELOW = 0.005       # below this it settles back
    BACKOFF_MAX   = 4.0         # at most this far (12 dB)
    REF_EVERY     = 4           # the median looks at every 4th sample: plenty, and cheaper

    def __init__(self, rate, pre, post, max_run, release_s=0.1, backoff=False, tau_s=15.0):
        self.rate = float(rate)
        self.pre, self.post, self.max_run = int(pre), int(post), int(max_run)
        # Look-ahead: enough to see every sample a pulse's window reaches, whether its run is
        # short enough to be a pulse, and the good sample after its gap
        self.delay = 2 * self.pre + self.post + self.max_run + 1
        self._hist_n = self.delay + self.post
        self._hist = np.zeros(self._hist_n, np.complex64)
        self._hhi = np.zeros(self._hist_n, bool)    # over the line, over half of it
        self._hlo = np.zeros(self._hist_n, bool)
        self._run_in = 0                     # length of a stretch over half before the history
        self._last_good, self._carry = 0j, 0  # last good output, and blanked outputs since
        self.release_s = float(release_s)
        self.backoff_on = bool(backoff)
        self.backoff = 1.0
        self.k = None                        # the threshold over the reference (None: off)
        self.k_big = None                    # pulses over this count as impulses (HF)
        self.ref = None
        self._ref_was = collections.deque()  # (reference, samples) of the last calls
        self._ref_n = 0                      # samples they cover
        self.frac = 0.0                      # share of the time blanked, about the last second
        self.pulses = EventRate(tau_s)       # pulses patched a minute
        self.big = EventRate(tau_s)          # of those, over k_big
        self.n_pulses = self.n_big = self.n_blanked = 0     # running totals (tests)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- controls
    def set_k(self, k, k_big=None):
        with self._lock:
            self.k = None if k is None else float(k)
            self.k_big = None if k_big is None else float(k_big)
            self.backoff = 1.0

    def reset(self):
        """A retune: the level is the new frequency's, from the next samples on."""
        with self._lock:
            self.ref = None
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
        dt = n / self.rate
        mag = np.abs(x)
        med = float(np.median(mag[::self.REF_EVERY]))
        if self.ref is None or med > self.ref:
            self.ref = med
        else:
            self.ref += (1.0 - math.exp(-dt / self.release_s)) * (med - self.ref)
        # the line rises late: no higher than the reference was over the last 4 x MAX_RUN samples
        # (at least the last call), so a stretch begun under the old line can show it's long
        ref, back = self.ref, 0
        for r, m in reversed(self._ref_was):
            ref = min(ref, r)
            back += m
            if back >= 4 * self.max_run:
                break
        self._ref_was.append((self.ref, n))
        self._ref_n += n
        while self._ref_n - self._ref_was[0][1] >= 4 * self.max_run:    # still covered without it
            self._ref_n -= self._ref_was.popleft()[1]
        k = self.threshold()
        if k is not None and ref > 0:
            hi_new, lo_new = mag > k * ref, mag > 0.5 * k * ref
        else:
            hi_new = lo_new = np.zeros(n, bool)

        P, pre, post = self._hist_n, self.pre, self.post
        buf = np.concatenate((self._hist, x))
        hi = np.concatenate((self._hhi, hi_new))
        lo = np.concatenate((self._hlo, lo_new))
        L = P + n
        out = buf[post:post + n].copy()
        blanked = 0
        run_in = 0
        if hi.any():
            eff, run_in = self._pulses_only(hi, lo, L, P)
            # blank from PRE before a pulse's stretch to POST after; worked out past the output
            # too, where the window still fits, to find the good sample after a gap
            cs = np.concatenate(([0], np.cumsum(eff, dtype=np.int64)))
            ext = np.arange(post, L - pre)
            mask = (cs[ext + pre + 1] - cs[ext - post]) > 0
            m_out = mask[:n]
            if m_out.any():
                out = self._patch(buf[post:L - pre], mask, n, out)
                blanked = int(m_out.sum())
                self._count(m_out, buf[post:post + n])
                self._carry = (self._carry + n) if m_out.all() else \
                    int(n - 1 - np.flatnonzero(~m_out)[-1])
            else:
                self._carry = 0
            if self._carry == 0:
                self._last_good = complex(out[-1])
            elif not m_out.all():
                self._last_good = complex(out[n - 1 - self._carry])
        else:
            if lo[L - P] and lo[L - P - 1]:             # a stretch over half carries on
                run_in = self._stretch_before(lo, L - P)
            self._carry = 0
            self._last_good = complex(out[-1])
        self._run_in = run_in
        self._hist, self._hhi, self._hlo = buf[-P:], hi[-P:], lo[-P:]

        self.n_blanked += blanked
        a = 1.0 - math.exp(-dt / 1.0)
        self.frac += a * (blanked / n - self.frac)
        if self.backoff_on and self.k is not None:
            if self.frac > self.BACKOFF_ABOVE:
                self.backoff = min(self.BACKOFF_MAX, self.backoff * (1.0 + 2.0 * dt))
            elif self.frac < self.BACKOFF_BELOW and self.backoff > 1.0:
                self.backoff = max(1.0, self.backoff * (1.0 - 0.1 * dt))
        return out

    def _stretch_before(self, lo, h0):
        """How long the stretch over half running through sample h0 has been going before it."""
        below = np.flatnonzero(~lo[:h0])
        s = int(below[-1]) + 1 if len(below) else 0
        return h0 - s + (self._run_in if s == 0 else 0)

    def _pulses_only(self, hi, lo, L, P):
        """The pulses: stretches over half the line that reach the line somewhere and are no
        longer than MAX_RUN; and how long a stretch running into the next call's history has
        been going."""
        d = np.diff(np.concatenate(([0], lo.view(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        lengths = ends - starts
        if len(starts) and starts[0] == 0:
            lengths[0] += self._run_in
        chi = np.concatenate(([0], np.cumsum(hi, dtype=np.int64)))
        pulse = ((chi[ends] - chi[starts]) > 0) & (lengths <= self.max_run)
        rid = np.cumsum(d[:-1] == 1) - 1                # the stretch each sample belongs to
        eff = lo & pulse[np.maximum(rid, 0)] if len(starts) else lo
        h0 = L - P
        run_in = self._stretch_before(lo, h0) if (lo[h0] and lo[h0 - 1]) else 0
        return eff, run_in

    def _patch(self, vals, mask, n, out):
        """A straight line across each gap, from the good sample before to the good one after
        (held level when the one after isn't in sight)."""
        M = len(vals)
        idx = np.arange(M)
        good = ~mask
        prev = np.maximum.accumulate(np.where(good, idx, -1))
        nxt = np.minimum.accumulate(np.where(good, idx, M)[::-1])[::-1]
        b = np.flatnonzero(mask[:n])
        p, q = prev[b], nxt[b]
        left = np.where(p >= 0, vals[np.maximum(p, 0)], np.complex64(self._last_good))
        ppos = np.where(p >= 0, p, -1 - self._carry).astype(np.float64)
        have = q < M
        right = np.where(have, vals[np.minimum(q, M - 1)], left)
        qpos = np.where(have, q, b + 1).astype(np.float64)
        t = (b - ppos) / np.maximum(qpos - ppos, 1.0)
        out[b] = (left + (right - left) * t).astype(np.complex64)
        return out

    def _count(self, m_out, raw):
        """Pulses begun in this output: gaps that start here."""
        d = np.diff(np.concatenate(([1 if self._carry > 0 else 0], m_out.view(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        n_new = len(starts)
        big = 0
        if self.k_big is not None and n_new and self.ref:
            line = self.k_big * self.ref
            for s in starts:                    # each gap's tallest sample (a few a call at most)
                e = ends[np.searchsorted(ends, s, side='right')]
                big += int(np.abs(raw[s:e]).max() > line)
        dt = len(m_out) / self.rate
        self.n_pulses += n_new
        self.n_big += big
        self.pulses.add(n_new, dt)
        self.big.add(big, dt)

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
