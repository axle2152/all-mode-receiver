#!/usr/bin/env python3
"""
FM Stereo Receiver - GNU Radio 3.10
===================================
RTL-SDR FM stereo receiver with car-radio style weak signal handling.

Signal quality ("circle" CNR)
    FM is constant envelope, so clean IQ sits on a circle. Noise, multipath
    and front-end intermod all make the circle fuzzy. The M2M4 estimator turns
    the 2nd and 4th moments of |IQ| over 20 ms into a CNR in dB that does not
    depend on audio level, RDS or SCA. On an RTL-SDR it tops out around
    27-28 dB on strong stations (the dongle's own filtering), so all
    thresholds sit below that.

Blend / high-cut / soft-mute
    Driven by the CNR in dB through smooth S-curves. Attack is effectively
    instant (two consecutive 20 ms windows must agree), then a short hold so
    picket-fencing cannot pump it, then a release at a fixed dB-per-second rate.

Software AGC (tuner AGC is never used)
    Wideband guard: samples at the ADC rails and composite level, measured on
    the raw IQ. Clipping drops gain at once. Narrowband judge: after a step up,
    the circle CNR must improve or the step is undone and locked out for a while,
    so the gain settles at the most gain that is still useful.

RDS
    57 kHz BPSK is mixed down, carrier-tracked with a Costas loop and
    resampled to exactly 8 samples per bit; a small Python block finds bit
    timing, block sync (CRC + offset words) and decodes PS (0A/0B) and
    RadioText (2A/2B). Read tb.rds_ps / tb.rds_rt / tb.rds_pi.

Channel equalizer (CMA)
    A 31-tap constant-modulus equalizer runs on the channel IQ all the time.
    It undoes multipath echoes by making the FM circle round again. Its output
    is used only when it measurably beats the raw signal (auto), on demand
    (on), or never (off); switching is a short crossfade, so no clicks.

Tone controls
    Bass/treble first-order shelves at 48 kHz, +/-12 dB, with a fixed 12 dB
    of digital headroom so a full boost can never clip.

Channels
    Channel 87.7 is received on 87.75 MHz (channel 6 TV audio). The mapping
    lives only here, so callers, presets and displays always use 87.7.

Usage:
    python fm_stereo.py --freq 90.5e6
    python fm_stereo.py --freq 90.5e6 --no-agc --gain 7.7
    python fm_stereo.py --freq 90.5e6 --log drive.csv
    python fm_stereo.py --freq 90.5e6 --record-iq drive.cs8   (4.8 MB/s!)
"""

import argparse
import datetime
import math
import threading
import time
from collections import deque

import numpy as np
from gnuradio import gr, blocks, filter, analog, audio, digital
from gnuradio.filter import firdes
import osmosdr
try:                                     # optional read-only meters for the monitor UI
    import rx_meters
    if rx_meters.AudioMeterBlock is None:
        rx_meters = None
except ImportError:
    rx_meters = None


# R820T / R820T2 / R828D gain steps in dB (used if the driver does not report them)
R82XX_GAINS = [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7,
               20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2,
               42.1, 43.4, 43.9, 44.5, 48.0, 49.6]


# ---------------------------------------------------------------------------
# RDS / RBDS decoder
# ---------------------------------------------------------------------------
# Input: complex baseband at 9500 Hz = 8 samples per RDS bit, carrier already
# tracked (data on the real axis, sign ambiguity allowed).
#   bit timing : correlate with a biphase template, keep the sample phase
#                with the most energy (tracks dongle clock drift)
#   bits       : differential decoding removes the 180 degree ambiguity
#   blocks     : 26 bits = 16 data + 10 check; checkword XOR CRC = offset word
#   groups     : 0A/0B -> PS (8 chars), 2A/2B -> RadioText (64/32 chars)
# ---------------------------------------------------------------------------

def _crc10_table():
    """Checkword (before the offset word) for every 16-bit info word."""
    g = 0x5B9                                    # x^10+x^8+x^7+x^5+x^4+x^3+1
    d = np.arange(65536, dtype=np.uint32) << 10
    for bit in range(25, 9, -1):
        d ^= ((d >> bit) & 1) * np.uint32(g << (bit - 10))
    return (d & 0x3FF).astype(np.uint16)


# RDS character table rows 0x80/0x90 folded onto plain letters (the dot-matrix
# font is ASCII); anything else outside printable ASCII shows as a space.
_RDS_FOLD = dict(zip(range(0x80, 0xA0),
                     "aaeeiioouuNCSB!Jaaeeiioouuncsgij"))


def _rds_char(c):
    if 0x20 <= c <= 0x7E:
        return chr(c)
    return _RDS_FOLD.get(c, ' ')


# RBDS PI code -> US call letters (NRSC-4-A Annex D.6)
_RBDS_3LETTER = {
    0x99A5: 'KBW', 0x99A6: 'KCY', 0x9990: 'KDB', 0x99A7: 'KDF', 0x9950: 'KEX', 0x9951: 'KFH',
    0x9952: 'KFI', 0x9953: 'KGA', 0x9991: 'KGB', 0x9954: 'KGO', 0x9955: 'KGU', 0x9956: 'KGW',
    0x9957: 'KGY', 0x99AA: 'KHQ', 0x9958: 'KID', 0x9959: 'KIT', 0x995A: 'KJR', 0x995B: 'KLO',
    0x995C: 'KLZ', 0x995D: 'KMA', 0x995E: 'KMJ', 0x995F: 'KNX', 0x9960: 'KOA', 0x99AB: 'KOB',
    0x9992: 'KOY', 0x9993: 'KPQ', 0x9964: 'KQV', 0x9994: 'KSD', 0x9965: 'KSL', 0x9966: 'KUJ',
    0x9995: 'KUT', 0x9967: 'KVI', 0x9968: 'KWG', 0x9996: 'KXL', 0x9997: 'KXO', 0x996B: 'KYW',
    0x9999: 'WBT', 0x996D: 'WBZ', 0x996E: 'WDZ', 0x996F: 'WEW', 0x999A: 'WGH', 0x9971: 'WGL',
    0x9972: 'WGN', 0x9973: 'WGR', 0x999B: 'WGY', 0x9975: 'WHA', 0x9976: 'WHB', 0x9977: 'WHK',
    0x9978: 'WHO', 0x999C: 'WHP', 0x999D: 'WIL', 0x997A: 'WIP', 0x99B3: 'WIS', 0x997B: 'WJR',
    0x99B4: 'WJW', 0x99B5: 'WJZ', 0x997C: 'WKY', 0x997D: 'WLS', 0x997E: 'WLW', 0x999E: 'WMC',
    0x999F: 'WMT', 0x9981: 'WOC', 0x99A0: 'WOI', 0x9983: 'WOL', 0x9984: 'WOR', 0x99A1: 'WOW',
    0x99B9: 'WRC', 0x99A2: 'WRR', 0x99A3: 'WSB', 0x99A4: 'WSM', 0x9988: 'WWJ', 0x9989: 'WWL',
}


def pi_to_callsign(pi):
    """US call letters from an RBDS PI code, or None (Canada, Mexico, reserved...)."""
    if pi is None:
        return None
    if pi >> 8 == 0xAF:                       # P1 P2 0 0 was sent as A F P1 P2
        pi = (pi & 0xFF) << 8
    if pi >> 12 == 0xA:                       # P1 0 P3 P4 was sent as A P1 P3 P4
        pi = ((pi & 0x0F00) << 4) | (pi & 0xFF)
    if pi in _RBDS_3LETTER:
        return _RBDS_3LETTER[pi]
    if 0x1000 <= pi <= 0x54A7:
        first, n = 'K', pi - 4096
    elif 0x54A8 <= pi <= 0x994F:
        first, n = 'W', pi - 21672
    elif pi >> 12 == 0xB and (pi & 0xFF) in (0x01, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E):
        return 'NPR'                          # nationally linked: NPR-1 ... NPR-6
    else:
        return None
    a = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    return first + a[n // 676] + a[(n // 26) % 26] + a[n % 26]


def pi_call_candidates(pi):
    """Call signs a PI could mean if its first hex digit was replaced, e.g. by
    an encoder set to international RDS mode (first digit = country code).
    WUBL should send 899D but sends 199D: same last three digits."""
    if pi is None:
        return []
    std = pi_to_callsign(pi)
    out = []
    for nib in range(1, 10):
        c = pi_to_callsign((nib << 12) | (pi & 0xFFF))
        if c and c != std and len(c) == 4 and c not in out:
            out.append(c)
    return out


class RdsDecoder(gr.sync_block):

    SPB = 8                                       # samples per bit at 9500 Hz
    VLEN = 256                                    # samples per call (about 27 ms), keeps Python overhead low
    _H = np.array([1, 1, 1, 1, -1, -1, -1, -1], dtype=np.float32)
    _POS = {0x0FC: 0, 0x198: 1, 0x168: 2, 0x350: 2, 0x1B4: 3}   # A, B, C, C', D
    _CPRIME = 0x350

    def __init__(self):
        gr.sync_block.__init__(self, name="rds_decoder",
                               in_sig=[(np.complex64, self.VLEN)], out_sig=None)
        self._crc = _crc10_table().tolist()
        self._reset_req = False
        self._clear()

    def request_reset(self):
        """Thread-safe: the decoder clears itself at the next work() call."""
        self._reset_req = True

    def _clear(self):
        # published state
        self.ps = ""
        self.rt = ""
        self.pi = None
        self.call = None
        self.pty = None
        self.synced = False
        self._pi_cand = None
        self._clear_extras()
        self._g_times = deque()                    # group arrival times, last 5 s
        self.blocks_ok = self.blocks_all = 0
        # timing
        self._buf = np.zeros(0, np.float32)
        self._n0 = 0
        self._next = None
        self._energy = np.zeros(self.SPB)
        # bits / blocks
        self._prev = 0
        self._reg = 0
        self._nbits = 0
        self._sync = False
        self._last_hit = None
        self._pos = 0
        self._cnt = 0
        self._bad_run = 0
        self._blocks = [None] * 4
        self._cprime = False
        # text
        self._pi_cur = None
        self._ps_buf = [' '] * 8
        self._ps_seen = 0
        self._rt_buf = [None] * 64
        self._rt_ab = None

    def work(self, input_items, output_items):
        nvec = len(input_items[0])
        x = input_items[0].reshape(-1)
        if self._reset_req:
            self._reset_req = False
            self._clear()

        buf = np.concatenate((self._buf, x.real.astype(np.float32)))
        if len(buf) < 2 * self.SPB:
            self._buf = buf
            return nvec

        y = np.correlate(buf, self._H, 'valid')          # y[i]: a bit starting at n0+i
        g = self._n0 + np.arange(len(y))
        e = np.bincount(g % self.SPB, weights=y.astype(np.float64) ** 2, minlength=self.SPB)
        a = min(1.0, len(y) / 2000.0)                    # about 0.2 s of memory
        self._energy = (1 - a) * self._energy + a * e / max(len(y) / self.SPB, 1.0)
        best = int(np.argmax(self._energy))

        start = self._n0 if self._next is None else self._next - self.SPB // 2
        first = start + ((best - start) % self.SPB)
        idx = np.arange(first - self._n0, len(y), self.SPB)
        for v in y[idx]:
            self._bit(1 if v > 0 else 0)
        if len(idx):
            self._next = self._n0 + int(idx[-1]) + self.SPB

        nxt = self._next if self._next is not None else self._n0 + len(y)
        keep = max(0, nxt - self.SPB - self._n0)
        self._buf = buf[keep:]
        self._n0 += keep
        return nvec

    def _bit(self, b):
        d = b ^ self._prev
        self._prev = b
        reg = ((self._reg << 1) | d) & 0x3FFFFFF
        self._reg = reg
        self._nbits += 1

        if not self._sync:
            pos = self._POS.get((reg & 0x3FF) ^ self._crc[reg >> 10])
            if pos is None:
                return
            if self._last_hit is not None:
                n_prev, p_prev = self._last_hit
                dn = self._nbits - n_prev
                if dn % 26 == 0 and 0 < dn <= 104 and (p_prev + dn // 26) % 4 == pos:
                    self._sync = self.synced = True
                    self._pos, self._cnt, self._bad_run = pos, 0, 0
                    self._blocks = [None] * 4
                    self._store(pos, reg)
                    return
            self._last_hit = (self._nbits, pos)
            return

        self._cnt += 1
        if self._cnt < 26:
            return
        self._cnt = 0
        self._pos = (self._pos + 1) % 4
        if self._pos == 0:
            self._blocks = [None] * 4
        off = (reg & 0x3FF) ^ self._crc[reg >> 10]
        if self._POS.get(off) == self._pos:
            self._bad_run = 0
            self._store(self._pos, reg, off)
        else:
            self._bad_run += 1
            if self._bad_run >= 10:                      # lost it (slip or fade): re-acquire
                self._sync = self.synced = False
                self._last_hit = None
                return
        if self._pos == 3:
            self._group()

    def _clear_extras(self):
        """Station details beyond PS / RT (cleared on a new PI)."""
        self.tp = self.ta = self.ms = None
        self.af = frozenset()                      # alternative frequencies, MHz
        self.ct = None                             # station clock (local time), datetime
        self.ptyn = ''
        self._ptyn_buf, self._ptyn_ab = [' '] * 8, None
        self.group_counts = {}

    def _store(self, pos, reg, off=None):
        self._blocks[pos] = reg >> 10
        if pos == 2:
            self._cprime = (off == self._CPRIME)

    def _group(self):
        A, B, C, D = self._blocks
        now = time.monotonic()
        self._g_times.append(now)
        while now - self._g_times[0] > 5.0:
            self._g_times.popleft()
        self.blocks_all += 4
        self.blocks_ok += sum(b is not None for b in self._blocks)
        if self.blocks_all > 4000:                 # keep it a recent figure
            self.blocks_all //= 2
            self.blocks_ok //= 2
        if A is not None and A != self._pi_cur:
            # A new PI must repeat before it is believed (a corrupted block can
            # occasionally pass the CRC); then it is a new station: start over.
            if A != self._pi_cand:
                self._pi_cand = A
                return
            self._pi_cur = A
            self.pi, self.call = A, pi_to_callsign(A)
            self._ps_buf, self._ps_seen = [' '] * 8, 0
            self._rt_buf, self._rt_ab = [None] * 64, None
            self.ps = self.rt = ""
            self._clear_extras()
        if self._pi_cur is None or B is None:
            return
        gtype, ver = B >> 12, (B >> 11) & 1
        self.pty = (B >> 5) & 0x1F
        self.tp = bool((B >> 10) & 1)
        name = f"{gtype}{'AB'[ver]}"
        self.group_counts[name] = self.group_counts.get(name, 0) + 1

        if gtype == 0:                                   # TA, music/speech, AF list
            self.ta, self.ms = bool((B >> 4) & 1), bool((B >> 3) & 1)
            if ver == 0 and C is not None:
                new = {round(87.5 + 0.1 * c, 1) for c in (C >> 8, C & 0xFF) if 1 <= c <= 204}
                if not new <= self.af:
                    self.af = frozenset(self.af | new)
        elif gtype == 4 and ver == 0 and C is not None and D is not None:   # clock time
            mjd = ((B & 3) << 15) | (C >> 1)
            hh, mm = ((C & 1) << 4) | (D >> 12), (D >> 6) & 0x3F
            off = 30 * (D & 0x1F) * (-1 if (D >> 5) & 1 else 1)
            if hh < 24 and mm < 60 and 50000 < mjd < 80000:
                self.ct = (datetime.datetime(1858, 11, 17)
                           + datetime.timedelta(days=mjd, hours=hh, minutes=mm + off))
        elif gtype == 10 and ver == 0:                   # programme type name
            seg, ab = B & 1, (B >> 4) & 1
            if ab != self._ptyn_ab:
                self._ptyn_buf, self._ptyn_ab = [' '] * 8, ab
            for i, word in ((0, C), (2, D)):
                if word is not None:
                    self._ptyn_buf[4 * seg + i] = _rds_char(word >> 8)
                    self._ptyn_buf[4 * seg + i + 1] = _rds_char(word & 0xFF)
            self.ptyn = ''.join(self._ptyn_buf).strip()

        if gtype == 0 and D is not None:                 # PS name, 2 chars per group
            # Commit only a complete in-order run of segments 0,1,2,3, so a
            # missed group can't stitch two frames of a scrolling PS together.
            seg = B & 3
            if seg == 0:
                self._ps_seen = 0
            if seg == self._ps_seen:
                self._ps_buf[2 * seg] = _rds_char(D >> 8)
                self._ps_buf[2 * seg + 1] = _rds_char(D & 0xFF)
                self._ps_seen += 1
                if self._ps_seen == 4:
                    self.ps = ''.join(self._ps_buf)
                    self._ps_seen = 0
            else:
                self._ps_seen = 0

        elif gtype == 2:                                 # RadioText
            seg, ab = B & 0xF, (B >> 4) & 1
            if ab != self._rt_ab:
                self._rt_buf, self._rt_ab = [None] * 64, ab
            if ver == 0:
                if C is not None and not self._cprime:
                    self._rt_buf[4 * seg], self._rt_buf[4 * seg + 1] = C >> 8, C & 0xFF
                if D is not None:
                    self._rt_buf[4 * seg + 2], self._rt_buf[4 * seg + 3] = D >> 8, D & 0xFF
                length = 64
            else:
                if D is not None:
                    self._rt_buf[2 * seg], self._rt_buf[2 * seg + 1] = D >> 8, D & 0xFF
                length = 32
            text = self._rt_buf[:length]
            end = text.index(0x0D) if 0x0D in text else length
            if end and all(c is not None for c in text[:end]):
                self.rt = ''.join(_rds_char(c) for c in text[:end]).strip()


def smoothstep(x, lo, hi):
    """0 at or below lo, 1 at or above hi, smooth S-curve between."""
    t = min(max((x - lo) / (hi - lo), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


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


class FMStereoReceiver(gr.top_block):

    # ------------------------------------------------------------------
    # Tuning constants. Circle CNR is in dB; see module docstring.
    # ------------------------------------------------------------------
    STEREO_FULL_DB    = 20.0   # full stereo at or above
    STEREO_MONO_DB    = 4.0   # full mono at or below
    TREBLE_START_DB   = 9.0   # high-cut begins below this
    TREBLE_FULL_DB    = 1.0    # full high-cut at or below
    SOFT_MUTE         = False  # volume cut on weak signals (off: the drive showed it
                               # active half the time with no antenna)
    MUTE_START_DB     = 6.0    # soft mute begins below this (if enabled)
    MUTE_FLOOR        = 1.0    # volume at 0 dB CNR and below (if enabled)

    ATTACK_CONFIRM    = 1      # consecutive 20 ms windows that must agree on a drop
    HOLD_S            = 0.01   # hold after any drop
    RELEASE_DB_PER_S  = 30.0   # recovery rate after the hold
    # Release follows a percentile of the last few hundred ms instead of the
    # worst 20 ms window, so short dips while moving don't keep blend/hi-cut clamped
    QUAL_WINDOW_S     = 0.4
    QUAL_PCT          = 65.0
    RELEASE_FAST_DB_S = 80.0
    # Dip hi-cut: the instant the MPX fills with noise above 70 kHz (a fade or a
    # multipath burst), pull the stereo toward mono first, then cut the highs and
    # upper mids where the static lives, like a tape dropout; let go fast
    DIP_HICUT         = True
    DIP_ATTACK_MS     = 2.0
    DIP_RELEASE_MS    = 20.0
    DIP_MULTIPATH     = True    # static cut also pulls stereo on fast multipath bursts
    DIP_MP_WINDOW_MS  = 2.0     # ...judged by the circle CNR over this short a window,
    DIP_MP_MARGIN_DB  = 3.0     # ...given this much benefit of the doubt (2 ms readings jitter)
    DIP_HICUT_HZ      = 2500.0  # what's left of the top end in a full dip
    DIP_STEREO_X      = 2.0     # stereo collapses this many times faster than the top end
    DIP_ON_DB         = 8.0     # a noise burst this far above its slow average starts it...
    DIP_FULL_DB       = 16.0    # ...and this far is a full dip
    DIP_FLOOR         = 1e-5    # so a clean station's near-silence never triggers
    # Noise reduction: Wiener-style spectral NR on L+R and L-R separately. The noise
    # is measured in the MPX itself (the empty 16-18.5 kHz gap: flat below threshold,
    # rising with f^2 above it), so music can't be mistaken for hiss. Engages as CNR drops.
    NOISE_REDUCTION   = True
    NR_FFT            = 1024    # 21 ms frames: best on talk, no worse on music than longer
    NR_MAX_DB         = 8.0     # never cut more than this: the hiss left over masks the
                                # warble ("musical noise") that deeper cuts expose
    NR_FULL_CNR_DB    = 5.0     # full depth at or below this CNR...
    NR_OFF_CNR_DB     = 16.0    # ...none at or above this
    NR_CLOSE_MS       = 100.0   # gains fall this slowly (rise within ~5 ms), so they can't flutter
    # Deviation-aware IF: measured on a separate demodulator ahead of the IF filter
    DEV_CREST         = 2.5    # peak deviation ~ this x the loud-window RMS deviation
    DEV_MIN_SNAPS     = 15     # about 3 s of measurements before it may narrow
    BLEND_SLEW_DOWN   = 20.0   # max blend change per second going down (~70 ms full swing)
    BLEND_SLEW_UP     = 20.0    # and going up

    PILOT_ON          = 0.035  # coherent pilot amplitude, MPX units (about 3.9% injection)
    PILOT_OFF         = 0.025
    PILOT_MIN_CNR_DB  = 6.0    # below this (1 s average) the PLL can lock onto noise
    INDICATOR_ON      = 0.01   # STEREO lights as soon as any stereo is blended in
    INDICATOR_OFF     = 0.001  # and goes out at full mono (0.0 / 0.0 = pilot only)
    INDICATOR_DWELL_S = 0.05   # minimum time between indicator changes

    AGC_CLIP_FRAC     = 1e-4   # fraction of samples at the rails that counts as overload
    AGC_TARGET_HI     = -8.0   # composite dBFS: above this, back off
    AGC_TARGET_LO     = -14.0  # composite dBFS: below this, consider more gain
    AGC_UP_WAIT_S     = 2.0    # clean time required before trying more gain
    AGC_SETTLE_S      = 0.25   # USB latency plus one window after any gain change
    TUNE_SETTLE_S     = 0.15   # samples from the old channel still in flight after a retune
    AGC_EVAL_S        = 0.60   # time spent judging a step up
    AGC_MIN_IMPROVE   = 1.0    # dB of CNR a step up must earn to be kept
    AGC_GOOD_DB       = 16.0   # CNR already this good: no need for more gain
    AGC_LOCKOUT_S     = 15.0   # after a useless step up, stay put this long
    AGC_MIN_STEP_DB   = 2.0    # smallest gain move (low R82xx steps are under 1 dB)
    AGC_MAX_DB        = 20.0   # the AGC never goes above this (19.7 dB on an R82xx):
                               # past ~20 dB the RTL gains little and its front end
                               # overloads on strong neighbours. Manual gain can still go higher.
    AGC_STEADY_DB     = 1.5    # CNR spread (std, last 2 s) above which we're "moving":
                               # fading then swamps the 1 dB test, so the AGC only
                               # keeps the composite level in range
    AGC_MOVING_LO     = -20.0  # while moving, step up only below this composite level

    # Channel equalizer (CMA)
    EQ_TAPS           = 63     # reaches back ~260 us at 240 kHz (127: ~530 us, long terrain echoes);
                               # 63 halves the CPU and still covers ~260 us
    EQ_MU             = 5e-4   # adaptation step (127 taps settle in ~0.5 s)
    EQ_AGC_RATE       = 1e-4   # level normalisation ahead of the CMA (~40 ms)
    EQ_ENGAGE_DB      = 1.5    # auto: switch to the equalizer when it wins by this (1 s average)
    EQ_RELEASE_DB     = 0.5    # and back to the raw signal below this
    EQ_FADE_S         = 0.04   # crossfade time when switching
    EQ_DIVERGE_DB     = 3.0    # equalizer this much worse than raw for 2 s: reset it
    # Adaptive IF bandwidth: on a weak station, pass only the part of the
    # spectrum where the station stands above the noise (what DSP car tuners do).
    # Strong stations stay wide, so stereo and RDS are never touched.
    IF_TAPS           = 131    # fixed length, so the delay never changes as it slides
    IF_WIDE_KHZ       = 95.0   # at or above this: no IF filter at all
    IF_MIN_KHZ        = 30.0   # narrowest it will go (25 trims loud syllables on real talk)
    IF_NARROW_KHZ     = 35.0   # the manual "narrow" setting
    IF_MARGIN_KHZ     = 5.0    # added to where the station meets the noise
    IF_EDGE_DECAY     = 3.0    # kHz/s: the edge follows loud moments at once, lets go slowly
    IF_ADAPT_SNR_DB   = 8.0    # station SNR (200 kHz) at or below this: pass only where the
                               # station beats the noise (below FM threshold, noise = crunch)
    IF_FULL_SNR_DB    = 20.0   # at or above this: always wide. In between it eases open:
                               # above threshold, extra noise is just hiss the de-emphasis
                               # takes care of, while trimming sidebands distorts loud syllables
    IF_SLEW_IN        = 15.0   # kHz per second, narrowing
    IF_SLEW_OUT       = 40.0   # kHz per second, widening (get stereo back quickly)
    IF_STEP_KHZ       = 1.0    # redesign the filter when it has moved this far
    EQ_MP_ON_DB       = 4.0    # the circle this much worse than the noise alone explains
    EQ_MP_OFF_DB      = 2.0    # means multipath: the equalizer may engage (auto)
    CIRCLE_CEILING_DB = 27.0   # the best circle an RTL ever shows (its own IF filter turns a
                               # little FM into AM), so a strong station isn't "missing" CNR
    EQ_CREDIT_CAP     = True   # control CNR (blend, hi-cut, soft mute, NR, pilot gate): judge the
                               # player (what is demodulated, not the learner), and credit the
                               # equalizer only what it shows it fixes on program, held through
                               # pauses, where a near-bare carrier flatters any filter
    EQ_CREDIT_MARGIN_DB = 2.0  # ...plus this much
    EQ_CREDIT_SPEC_CAP = False # also cap at the IF-spectrum CNR (q_exp)? Off: crowded bands
                               # fool that estimate low (94.9 read 8.7 dB with 15 dB delivered)
    EQ_LEARN_PROOF_DB = 4.0    # ...or the learner itself beats raw by this much on program,
                               # with a real main tap: proof of an echo the spectrum missed
                               # (crowded bands and wide stations fool the noise floor)
    EQ_GATE_MIN_DB    = 8.0    # ...judged only when the noise allows a circle this good:
                               # below it the circle reading is too noisy to tell, and
                               # the IF filter does better than the equalizer anyway
    EQ_MIN_MAIN_SHARE = 0.35   # a real echo canceller keeps this much energy in its main
                               # tap; a filter that is just narrowing the band does not
    EQ_PLAYER         = True   # listen through a fixed copy of the equalizer's last good taps
                               # (updated only while the station modulates and the copy is
                               # beaten); False = listen to the adaptive equalizer directly
    EQ_COPY_MARGIN_DB = 0.3    # the learner must beat what you hear by this to be copied...
    EQ_COPY_RUN       = 5      # ...over this many 20 ms windows of continuous program
    EQ_MOD_GATE_KHZ   = 6.0    # only judge tap drift while the program modulates at
                               # least this much (L+R RMS deviation, every 20 ms window):
                               # in pauses the equalizer has nothing to learn from and
                               # its taps drift without the car moving. Measured on the
                               # L+R audio band, so noise on weak signals can't fake it.

    # Tone controls
    TONE_BASS_HZ      = 120.0  # first-order shelf corners
    TONE_TREBLE_HZ    = 4000.0
    TONE_RANGE_DB     = 12.0
    HEADROOM_DB       = 12.0   # fixed attenuation so a full boost can't clip (TONE_MAKEUP off)
    TONE_MAKEUP       = True   # attenuate only by the biggest boost (+ TONE_SAFETY_DB):
                               # flat tone plays at full level, +6 dB bass gives back 6 dB
    TONE_SAFETY_DB    = 1.0

    STALL_S           = 2.0    # no samples for this long = dongle gone (raises, so a
                               # GUI can reconnect; e.g. after the laptop sleeps)

    LO_NUDGE          = 2000.0 # tune the RTL 2 kHz above the station so the DC canceller
                               # can never mistake the station's own carrier for DC

    # Channels received on a different RF frequency (kHz -> kHz).
    # 87.7 is channel 6 TV audio, which sits on 87.75 MHz.
    CHANNEL_RF_KHZ    = {87700: 87750}

    def __init__(self, freq=90.5e6, rf_gain=8.7, audio_rate=48000, agc=True,
                 lmr_only=False, lmr_gain=1.0, phase_trim_deg=0.0,
                 tuner_bw=200e3, audio_device="", device_args="numchan=1 rtl=0",
                 log_path=None, record_iq=None, **legacy):
        # legacy kwargs (stereo_exp, treble_exp, phase_offset, phase_flip) are
        # accepted so older front ends keep working; they are no longer used.
        gr.top_block.__init__(self, "FM Stereo Receiver")

        self.freq        = freq
        self.samp_rate   = 2_400_000
        self.mpx_rate    = 240_000
        self.audio_rate  = audio_rate
        self.lmr_only    = lmr_only
        self.target_lmr  = lmr_gain
        self.phase_trim  = phase_trim_deg
        self._lock       = threading.RLock()

        # Integration windows (samples) for the probes
        self._wb_n    = self.samp_rate // 50     # 20 ms at 2.4 Msps
        self._dc_n    = self.samp_rate // 100    # 10 ms
        self._circ_n  = self.mpx_rate // 50      # 20 ms at 240 k
        self._pilot_n = self.mpx_rate // 100     # 10 ms = 380 whole cycles of 38 kHz

        ##################################################
        # RTL-SDR source (tuner AGC always off)
        ##################################################
        self.src = osmosdr.source(args=device_args)
        self.src.set_sample_rate(self.samp_rate)
        self.src.set_center_freq(self.rf_freq(freq) + self.LO_NUDGE)
        self.src.set_freq_corr(0)
        self.src.set_dc_offset_mode(0)
        self.src.set_iq_balance_mode(0)
        self.src.set_gain_mode(False)
        self.src.set_antenna("")
        self.src.set_bandwidth(tuner_bw)   # 200 kHz request = R82xx's narrowest IF filter (~350 kHz)
        self.rtlsdr_source = self.src      # legacy name

        try:
            vals = list(self.src.get_gain_range().values())
        except Exception:
            vals = []
        self.gains = sorted(set(round(v, 1) for v in vals)) if len(vals) >= 5 else list(R82XX_GAINS)

        ##################################################
        # Wideband front end: DC canceller, overload stats, optional recording
        ##################################################
        self.dc_sub   = blocks.add_const_cc(0j)
        self.dc_int   = blocks.integrate_cc(self._dc_n)
        self.dc_probe = blocks.probe_signal_c()

        self.wb_c2f   = blocks.complex_to_float(1)
        self.wb_abs_i = blocks.abs_ff(1)
        self.wb_abs_q = blocks.abs_ff(1)
        self.wb_max   = blocks.max_ff(1)
        self.wb_rail  = blocks.threshold_ff(0.992, 0.992, 0)   # 1 when I or Q sits on a rail
        self.wb_clips = blocks.integrate_ff(self._wb_n)
        self.wb_mag2  = blocks.complex_to_mag_squared(1)
        self.wb_pow   = blocks.integrate_ff(self._wb_n)
        self.wb_pack  = blocks.float_to_complex(1)            # (power sum, clip count), aligned
        self.wb_probe = blocks.probe_signal_c()

        ##################################################
        # Channel filter 2.4 MHz -> 240 kHz (station sits LO_NUDGE below the LO)
        ##################################################
        ch_taps = firdes.low_pass(1.0, self.samp_rate, 100e3, 20e3, filter.window.WIN_HAMMING)
        self.rf_filter = filter.freq_xlating_fir_filter_ccf(10, ch_taps, -self.LO_NUDGE, self.samp_rate)
        # (time-domain FIR: its taps can change without dropping samples;
        # GNU Radio's FFT filter loses its overlap buffer on every set_taps)
        self.if_filter = filter.fir_filter_ccf(1, self._if_taps(None))

        # Channel equalizer: level-normalise, CMA, crossfade raw <-> equalized
        self.eq_agc     = analog.agc_cc(self.EQ_AGC_RATE, 1.0, 1.0)
        self.eq_alg     = digital.adaptive_algorithm_cma(
            digital.constellation_qpsk().base(), self.EQ_MU, 1.0)
        self.eq         = digital.linear_equalizer(self.EQ_TAPS, 1, self.eq_alg, True, [], '')
        self._eq_init   = [0j] * (self.EQ_TAPS - 1) + [1 + 0j]   # spike on the last tap = no delay
        # The player: what you hear. Same filter as the equalizer (its taps run
        # in reverse order here), but fixed between updates, so pauses in speech,
        # when the equalizer has nothing to learn from, can't disturb it.
        self.eq_play    = filter.fir_filter_ccc(1, self._eq_init[::-1])   # glitch-free set_taps
        self._play_taps = list(self._eq_init[::-1])
        self.pl_mag2    = blocks.complex_to_mag_squared(1)
        self.pl_mag4    = blocks.multiply_ff(1)
        self.eq_mix_raw = blocks.multiply_const_cc(1.0)
        self.eq_mix_eq  = blocks.multiply_const_cc(0.0)
        self.eq_sum     = blocks.add_cc(1)

        # Circle CNR of the equalizer output (same 20 ms windows)
        self.eq_mag2    = blocks.complex_to_mag_squared(1)
        self.eq_mag4    = blocks.multiply_ff(1)
        self.eq_int2    = blocks.integrate_ff(self._circ_n)
        self.eq_int4    = blocks.integrate_ff(self._circ_n)
        self.eq_pack    = blocks.float_to_complex(1)
        self.eq_probe   = blocks.probe_signal_c()
        self.pl_int2    = blocks.integrate_ff(self._circ_n)
        self.pl_int4    = blocks.integrate_ff(self._circ_n)
        self.pl_pack    = blocks.float_to_complex(1)
        self.pl_probe   = blocks.probe_signal_c()

        # Circle CNR: integrate |z|^2 and |z|^4 over the same 20 ms windows
        self.ch_mag2  = blocks.complex_to_mag_squared(1)
        self.ch_mag4  = blocks.multiply_ff(1)
        self.ch_int2  = blocks.integrate_ff(self._circ_n)
        self.ch_int4  = blocks.integrate_ff(self._circ_n)
        self.ch_pack  = blocks.float_to_complex(1)
        self.circ_probe = blocks.probe_signal_c()

        ##################################################
        # FM demodulation (scale kept from the original so listening level matches)
        ##################################################
        fm_gain = self.mpx_rate / (2 * np.pi * 75000) * 1.8
        self.fm_demod = analog.quadrature_demod_cf(fm_gain)
        # dip detector: ultrasonic MPX noise against its own slow average
        nb_taps = firdes.high_pass(1.0, self.mpx_rate, 72e3, 10e3, filter.window.WIN_HAMMING)
        self.nb_hp    = filter.fir_filter_fff(1, nb_taps)
        self.nb_sq    = blocks.multiply_ff(1)
        self.nb_fast  = blocks.moving_average_ff(120, 1.0 / 120, 4000)          # 0.5 ms
        self.nb_slow  = filter.single_pole_iir_filter_ff(1 - math.exp(-1 / (0.3 * self.mpx_rate)))
        self.nb_floor = blocks.add_const_ff(self.DIP_FLOOR)
        self.nb_ratio = blocks.divide_ff(1)
        self.dip_keep = blocks.keep_one_in_n(gr.sizeof_float, 120)              # 2 kHz control rate
        self.dip_ctl  = _DipControl(2000, self.DIP_ATTACK_MS, self.DIP_RELEASE_MS,
                                    self.DIP_ON_DB, self.DIP_FULL_DB, self.DIP_STEREO_X,
                                    self.STEREO_MONO_DB, self.STEREO_FULL_DB, self.DIP_MP_MARGIN_DB)
        self.dip_ctl.enabled = self.DIP_HICUT
        self.dip_ctl.multipath = self.DIP_MULTIPATH
        nmp = max(1, int(self.mpx_rate * self.DIP_MP_WINDOW_MS / 1000))
        self.mp_mag2  = blocks.complex_to_mag_squared(1)                        # on what is demodulated
        self.mp_mag4  = blocks.multiply_ff(1)
        self.mp_avg2  = blocks.moving_average_ff(nmp, 1.0 / nmp, 4000)         # fast circle moments
        self.mp_avg4  = blocks.moving_average_ff(nmp, 1.0 / nmp, 4000)
        self.mp_keep2 = blocks.keep_one_in_n(gr.sizeof_float, 120)
        self.mp_keep4 = blocks.keep_one_in_n(gr.sizeof_float, 120)
        self.dip_up_s = blocks.repeat(gr.sizeof_float, self.audio_rate // 2000)
        self.dip_dly_s = blocks.delay(gr.sizeof_float, self.NR_FFT)
        self.dip_up   = blocks.repeat(gr.sizeof_float, self.audio_rate // 2000)  # to audio rate
        self.dip_dly  = blocks.delay(gr.sizeof_float, self.NR_FFT)            # line up with the NR delay
        nr_taps = firdes.low_pass(1.0, self.mpx_rate, 22800, 1200, filter.window.WIN_HAMMING)   # to 22 kHz
        self.nr_ref_m = filter.fir_filter_fff(self.mpx_rate // self.audio_rate, nr_taps)
        self.nr_ref_s = filter.fir_filter_fff(self.mpx_rate // self.audio_rate, nr_taps)
        dec = self.mpx_rate // self.audio_rate
        d_audio = 3 * 144                                                     # lpr/bp + matching delay + audio decimator
        d_nr = (len(nr_taps) - 1) // 2
        self.nr_dly_m = blocks.delay(gr.sizeof_float, max(0, (d_audio - d_nr) // dec))
        self.nr_dly_s = blocks.delay(gr.sizeof_float, max(0, (d_audio - 144 - d_nr) // dec))
        self.nr = _NoiseReducer(self.NR_FFT, self.audio_rate, close_ms=self.NR_CLOSE_MS)
        self.dip_sinv = blocks.multiply_const_ff(-1.0)
        self.dip_skeep = blocks.add_const_ff(1.0)                                # 1 - stereo pull
        # mid / side at audio rate, side scaled down, back to L / R, then the dip low-pass
        self.dip_m_sum, self.dip_s_dif = blocks.add_ff(1), blocks.sub_ff(1)
        self.dip_m, self.dip_s = blocks.multiply_const_ff(0.5), blocks.multiply_const_ff(0.5)
        self.dip_s_pull = blocks.multiply_ff(1)
        self.dip_l, self.dip_r = blocks.add_ff(1), blocks.sub_ff(1)
        a_lp = 1 - math.exp(-2 * math.pi * self.DIP_HICUT_HZ / self.audio_rate)
        for ch in ("l", "r"):
            setattr(self, f"dip_lp_{ch}", filter.single_pole_iir_filter_ff(a_lp))
            setattr(self, f"dip_hp_{ch}", blocks.sub_ff(1))
            setattr(self, f"dip_hpg_{ch}", blocks.multiply_ff(1))
            setattr(self, f"dip_out_{ch}", blocks.sub_ff(1))
        # measuring demodulator ahead of the IF filter (kHz of deviation)
        self.wb_demod = analog.quadrature_demod_cf(self.mpx_rate / (2 * math.pi * 1000.0))
        self.dev_vec  = blocks.stream_to_vector(gr.sizeof_float, 4096)
        self.dev_keep = blocks.keep_one_in_n(gr.sizeof_float * 4096, max(1, int(self.mpx_rate / 4096 / 5)))
        self.dev_probe = blocks.probe_signal_vf(4096)

        ##################################################
        # MPX paths. Every MPX filter is 289 taps, so the pilot path and the
        # L-R path arrive at the multiplier with identical delay; the 38 kHz
        # reference then needs only a fixed 90 degree rotation.
        ##################################################
        W = filter.window.WIN_HAMMING
        lpr_taps   = firdes.low_pass(1.0, self.mpx_rate, 15000, 2000, W)
        pilot_taps = firdes.band_pass(1.0, self.mpx_rate, 18500, 19500, 2000, W)
        lmr_taps   = firdes.band_pass(1.0, self.mpx_rate, 23000, 53000, 2000, W)
        lmrlp_taps = firdes.low_pass(1.0, self.mpx_rate, 15000, 2000, W)
        assert len(lpr_taps) == len(pilot_taps) == len(lmr_taps) == len(lmrlp_taps)
        ntap = len(lpr_taps)

        self.lpr_filter    = filter.fft_filter_fff(1, lpr_taps)
        self.mod_sq        = blocks.multiply_ff(1)          # L+R squared
        self.mod_int       = blocks.integrate_ff(self._circ_n)
        self.mod_probe     = blocks.probe_signal_f()
        self.lpr_delay     = blocks.delay(gr.sizeof_float, (ntap - 1) // 2)
        self.pilot_filter  = filter.fft_filter_fff(1, pilot_taps)
        self.lmr_bp_filter = filter.fft_filter_fff(1, lmr_taps)
        self.lmr_lp_filter = filter.fft_filter_fff(1, lmrlp_taps)

        # Pilot PLL -> doubled -> rotated, amplitude 2 (coherent DSB/SSB demod needs 2x)
        self.f2c       = blocks.float_to_complex(1)
        self.pilot_pll = analog.pll_refout_cc(2 * np.pi * 50 / self.mpx_rate,
                                              2 * np.pi * 19500 / self.mpx_rate,
                                              2 * np.pi * 18500 / self.mpx_rate)
        self.freq_doubler = blocks.multiply_cc(1)
        self.ref_rotate   = blocks.multiply_const_cc(self._ref_phasor())
        self.ref_to_real  = blocks.complex_to_real(1)
        self.lmr_demod    = blocks.multiply_ff(1)

        # Coherent pilot detector: pilot x conj(PLL), averaged over 10 ms
        self.pilot_mix   = blocks.multiply_conjugate_cc(1)
        self.pilot_int   = blocks.integrate_cc(self._pilot_n)
        self.pilot_probe = blocks.probe_signal_c()

        ##################################################
        # RDS: 57 kHz -> 12 kHz complex, AGC + Costas (BPSK), -> 9500 Hz
        # (exactly 8 samples per 1187.5 bit/s RDS bit), then the decoder
        ##################################################
        rds_taps = firdes.low_pass(1.0, self.mpx_rate, 2600, 1200, W)
        self.rds_xlate  = filter.freq_xlating_fir_filter_fcf(20, rds_taps, 57000, self.mpx_rate)
        self.rds_agc    = analog.agc_cc(1e-3, 1.0, 1.0)
        self.rds_costas = digital.costas_loop_cc(0.005, 2)
        self.rds_resamp = filter.rational_resampler_ccc(19, 24)
        self.rds_vec    = blocks.stream_to_vector(gr.sizeof_gr_complex, RdsDecoder.VLEN)
        self.rds        = RdsDecoder()

        ##################################################
        # Spectrum snapshots for a GUI: one block of FFT_N contiguous samples
        # of the channel IQ and of the MPX, about ten times a second. The FFT
        # itself is done by whoever reads them, only when it wants them.
        ##################################################
        self.FFT_N = 2048
        keep = max(1, int(self.mpx_rate / self.FFT_N / 10))
        self.snap_iq_vec   = blocks.stream_to_vector(gr.sizeof_gr_complex, self.FFT_N)
        self.snap_iq_keep  = blocks.keep_one_in_n(gr.sizeof_gr_complex * self.FFT_N, keep)
        self.snap_iq       = blocks.probe_signal_vc(self.FFT_N)
        # the whole 2.4 MHz, a few times a second: mostly empty spectrum between
        # stations, so a low percentile of it is the true noise floor
        self.WB_N = 4096
        self.snap_wb_vec   = blocks.stream_to_vector(gr.sizeof_gr_complex, self.WB_N)
        self.snap_wb_keep  = blocks.keep_one_in_n(gr.sizeof_gr_complex * self.WB_N,
                                                  max(1, int(self.samp_rate / self.WB_N / 4)))
        self.snap_wb       = blocks.probe_signal_vc(self.WB_N)
        self.snap_pre_vec  = blocks.stream_to_vector(gr.sizeof_gr_complex, self.FFT_N)
        self.snap_pre_keep = blocks.keep_one_in_n(gr.sizeof_gr_complex * self.FFT_N, keep)
        self.snap_pre      = blocks.probe_signal_vc(self.FFT_N)
        self.snap_post_vec  = blocks.stream_to_vector(gr.sizeof_gr_complex, self.FFT_N)
        self.snap_post_keep = blocks.keep_one_in_n(gr.sizeof_gr_complex * self.FFT_N, keep)
        self.snap_post      = blocks.probe_signal_vc(self.FFT_N)
        self.snap_mpx_vec  = blocks.stream_to_vector(gr.sizeof_float, self.FFT_N)
        self.snap_mpx_keep = blocks.keep_one_in_n(gr.sizeof_float * self.FFT_N, keep)
        self.snap_mpx      = blocks.probe_signal_vf(self.FFT_N)

        ##################################################
        # Stereo matrix, de-emphasis, high-cut, limiter, soft mute, audio
        ##################################################
        self.lmr_blend_l = blocks.multiply_const_ff(0.0)
        self.lmr_blend_r = blocks.multiply_const_ff(0.0)
        self.matrix_l = blocks.add_ff(1)
        self.matrix_r = blocks.sub_ff(1)
        self.scale_l  = blocks.multiply_const_ff(0.5)
        self.scale_r  = blocks.multiply_const_ff(0.5)

        tau = 75e-6
        w_c, k = 1.0 / tau, 2.0 * self.mpx_rate
        b0 = w_c / (k + w_c)
        a1 = (w_c - k) / (w_c + k)
        self.deemph_l = filter.iir_filter_ffd([b0, b0], [1.0, a1], False)
        self.deemph_r = filter.iir_filter_ffd([b0, b0], [1.0, a1], False)

        # High-cut: crossfade between a delay-matched dry path and a 6 kHz low-pass.
        # Matching the delay removes the comb notch the old crossfade produced.
        tr_taps = firdes.low_pass(1.0, self.mpx_rate, 6000, 5000, filter.window.WIN_HANN)
        tr_delay = (len(tr_taps) - 1) // 2
        self._tr_delay = tr_delay
        self.treble_dry_l = blocks.delay(gr.sizeof_float, tr_delay)
        self.treble_dry_r = blocks.delay(gr.sizeof_float, tr_delay)
        self.treble_full_l = blocks.multiply_const_ff(1.0)
        self.treble_full_r = blocks.multiply_const_ff(1.0)
        self.treble_lp_l = filter.fft_filter_fff(1, tr_taps)
        self.treble_lp_r = filter.fft_filter_fff(1, tr_taps)
        self.treble_lp_scale_l = blocks.multiply_const_ff(0.0)
        self.treble_lp_scale_r = blocks.multiply_const_ff(0.0)
        self.treble_sum_l = blocks.add_ff(1)
        self.treble_sum_r = blocks.add_ff(1)

        self.limiter_l  = analog.rail_ff(-0.95, 0.95)
        self.limiter_r  = analog.rail_ff(-0.95, 0.95)
        self.vol_gain_l = blocks.multiply_const_ff(1.0)
        self.vol_gain_r = blocks.multiply_const_ff(1.0)

        audio_decim = self.mpx_rate // self.audio_rate
        audio_taps = firdes.low_pass(1.0, self.mpx_rate, 16000, 2000, W)
        self.audio_decim_l = filter.fir_filter_fff(audio_decim, audio_taps)
        self.audio_decim_r = filter.fir_filter_fff(audio_decim, audio_taps)
        # Tone controls at the audio rate: x + (gb-1)*LP(x) + (gt-1)*(x - LP'(x))
        # are exact first-order shelves; only gains change, so no clicks.
        ab = 1 - math.exp(-2 * math.pi * self.TONE_BASS_HZ / self.audio_rate)
        at = 1 - math.exp(-2 * math.pi * self.TONE_TREBLE_HZ / self.audio_rate)
        hr = 10 ** (-self._tone_atten_db(0, 0) / 20)
        for ch in ("l", "r"):
            setattr(self, f"tone_bass_lp_{ch}", filter.single_pole_iir_filter_ff(ab))
            setattr(self, f"tone_bass_k_{ch}", blocks.multiply_const_ff(0.0))
            setattr(self, f"tone_tre_lp_{ch}", filter.single_pole_iir_filter_ff(at))
            setattr(self, f"tone_tre_hp_{ch}", blocks.sub_ff(1))
            setattr(self, f"tone_tre_k_{ch}", blocks.multiply_const_ff(0.0))
            setattr(self, f"tone_sum_{ch}", blocks.add_ff(1))
            setattr(self, f"headroom_{ch}", blocks.multiply_const_ff(hr))
        self.audio_sink = audio.sink(self.audio_rate, audio_device, True)

        ##################################################
        # Monitoring taps (read only, see rx_meters.py): station audio after
        # de-emphasis, ahead of treble cut / limiter / volume, and the composite.
        # 0 dBFS on the audio meter = 75 kHz of L+R deviation (1.8 x 0.5).
        # Nothing here feeds the audio path.
        ##################################################
        self.audio_meter = self.mod_meter = None
        if rx_meters is not None:
            kb1, ka1, kb2, ka2 = rx_meters.k_weighting(self.audio_rate)
            for ch in ("l", "r"):
                setattr(self, f"meter_decim_{ch}", filter.fir_filter_fff(audio_decim, audio_taps))
                setattr(self, f"kw1_{ch}", filter.iir_filter_ffd(kb1, ka1, False))
                setattr(self, f"kw2_{ch}", filter.iir_filter_ffd(kb2, ka2, False))
            self.audio_meter = rx_meters.AudioMeterBlock(self.audio_rate, ref=0.9)
            # Deviation gets its own path. The main channel filter trims FM sidebands
            # (74 kHz read as 90+), and a wide path lets first-adjacent stations in
            # (one at -6 dB read 120 kHz). This one is +/-110 kHz at 480 ksps, then the
            # composite is cut at 60 kHz: main channel, pilot and RDS (no SCAs), where
            # the beats and noise from outside the channel mostly fall above 60 kHz.
            # Tested on a clipped 100% station: 74.7 clean, 75.2 with a -6 dB first
            # adjacent, 74.0 on lightly processed program (true peak 75.0 in each).
            mod_rate = self.samp_rate / 5
            self.mod_xlate = filter.freq_xlating_fir_filter_ccf(
                5, firdes.low_pass(1.0, self.samp_rate, 200e3, 160e3, W), -self.LO_NUDGE, self.samp_rate)
            self.mod_chan = filter.fir_filter_ccf(1, firdes.low_pass(1.0, mod_rate, 110e3, 20e3, W))
            self.mod_demod = analog.quadrature_demod_cf(mod_rate / (2 * math.pi * 1000.0))   # kHz
            self.mod_lp = filter.fir_filter_fff(2, firdes.low_pass(1.0, mod_rate, 61e3, 8e3, W))
            self.mod_meter = rx_meters.ModMeterBlock(scale=1.0, over_pos=75.0, over_neg=75.0)

        ##################################################
        # Optional raw IQ recording (interleaved int8, lossless for an RTL)
        ##################################################
        self.record_iq = record_iq
        if record_iq:
            self.rec_conv = blocks.complex_to_interleaved_char(False, 128.0)
            self.rec_sink = blocks.file_sink(gr.sizeof_char, record_iq, False)
            self.rec_sink.set_unbuffered(False)

        self._connect_flowgraph()
        self._init_control(rf_gain, agc, log_path)

    # ------------------------------------------------------------------
    def _ref_phasor(self):
        """2 x e^(j(90 deg + trim)), times a small droop correction.

        Amplitude 2 is what coherent DSB/SSB demodulation needs. 90 degrees
        because the PLL locks to the pilot's cosine phase while the stereo
        subcarrier is a sine. The quadrature demodulator measures phase change
        across one sample, which rolls off as sinc(f/fs): about 4% low at
        38 kHz, enough to cost several dB of separation, so it is undone here."""
        droop = float(np.sinc(38000.0 / self.mpx_rate))
        ph = math.radians(90.0 + self.phase_trim)
        a = 2.0 / droop
        return complex(a * math.cos(ph), a * math.sin(ph))

    def _connect_flowgraph(self):
        c = self.connect

        # Front end
        c(self.src, self.dc_sub, self.rf_filter, self.if_filter, self.eq_agc)
        c(self.dc_sub, self.snap_wb_vec, self.snap_wb_keep, self.snap_wb)
        c(self.eq_agc, self.eq_mix_raw, (self.eq_sum, 0))
        if self.EQ_PLAYER:
            c(self.eq_agc, self.eq)                                  # the learner
            c(self.eq_agc, self.eq_play, self.eq_mix_eq, (self.eq_sum, 1))
            c(self.eq_play, self.pl_mag2)
            c(self.pl_mag2, (self.pl_mag4, 0))
            c(self.pl_mag2, (self.pl_mag4, 1))
            c(self.pl_mag2, self.pl_int2, (self.pl_pack, 0))
            c(self.pl_mag4, self.pl_int4, (self.pl_pack, 1))
            c(self.pl_pack, self.pl_probe)
        else:
            c(self.eq_agc, self.eq, self.eq_mix_eq, (self.eq_sum, 1))
        c(self.eq_sum, self.fm_demod)
        c(self.eq, self.eq_mag2)
        c(self.eq_mag2, (self.eq_mag4, 0))
        c(self.eq_mag2, (self.eq_mag4, 1))
        c(self.eq_mag2, self.eq_int2, (self.eq_pack, 0))
        c(self.eq_mag4, self.eq_int4, (self.eq_pack, 1))
        c(self.eq_pack, self.eq_probe)
        c(self.src, self.dc_int, self.dc_probe)
        c(self.src, self.wb_c2f)
        c((self.wb_c2f, 0), self.wb_abs_i, (self.wb_max, 0))
        c((self.wb_c2f, 1), self.wb_abs_q, (self.wb_max, 1))
        c(self.wb_max, self.wb_rail, self.wb_clips, (self.wb_pack, 1))
        c(self.src, self.wb_mag2, self.wb_pow, (self.wb_pack, 0))
        c(self.wb_pack, self.wb_probe)
        if self.record_iq:
            c(self.src, self.rec_conv, self.rec_sink)

        # Circle CNR on the channel IQ
        c(self.if_filter, self.ch_mag2)          # the raw channel circle, ahead of the equalizer
        c(self.ch_mag2, (self.ch_mag4, 0))
        c(self.ch_mag2, (self.ch_mag4, 1))
        c(self.ch_mag2, self.ch_int2, (self.ch_pack, 0))
        c(self.ch_mag4, self.ch_int4, (self.ch_pack, 1))
        c(self.ch_pack, self.circ_probe)

        # MPX
        c(self.fm_demod, self.nb_hp)
        c(self.nb_hp, (self.nb_sq, 0))
        c(self.nb_hp, (self.nb_sq, 1))
        c(self.nb_sq, self.nb_fast, (self.nb_ratio, 0))
        c(self.nb_sq, self.nb_slow, self.nb_floor, (self.nb_ratio, 1))
        c(self.nb_ratio, self.dip_keep, self.dip_ctl, self.dip_up)
        c(self.dip_up, self.dip_dly)
        c(self.eq_sum, self.mp_mag2)             # after the equalizer: what is demodulated
        c(self.mp_mag2, (self.mp_mag4, 0))
        c(self.mp_mag2, (self.mp_mag4, 1))
        c(self.mp_mag2, self.mp_avg2, self.mp_keep2, (self.dip_ctl, 1))
        c(self.mp_mag4, self.mp_avg4, self.mp_keep4, (self.dip_ctl, 2))
        c((self.dip_ctl, 1), self.dip_up_s, self.dip_dly_s, self.dip_sinv, self.dip_skeep)
        c(self.fm_demod, self.nr_ref_m, self.nr_dly_m, (self.nr, 0))
        c(self.lmr_demod, self.nr_ref_s, self.nr_dly_s, (self.nr, 1))
        c(self.rf_filter, self.wb_demod, self.dev_vec, self.dev_keep, self.dev_probe)
        c(self.fm_demod, self.lpr_filter, self.lpr_delay)
        c(self.lpr_filter, (self.mod_sq, 0))
        c(self.lpr_filter, (self.mod_sq, 1))
        c(self.mod_sq, self.mod_int, self.mod_probe)
        c(self.fm_demod, self.pilot_filter, self.f2c, self.pilot_pll)
        c(self.pilot_pll, (self.freq_doubler, 0))
        c(self.pilot_pll, (self.freq_doubler, 1))
        c(self.freq_doubler, self.ref_rotate, self.ref_to_real)
        c(self.f2c, (self.pilot_mix, 0))
        c(self.pilot_pll, (self.pilot_mix, 1))
        c(self.pilot_mix, self.pilot_int, self.pilot_probe)
        c(self.fm_demod, self.lmr_bp_filter, (self.lmr_demod, 0))
        c(self.ref_to_real, (self.lmr_demod, 1))
        c(self.lmr_demod, self.lmr_lp_filter)

        # Spectrum snapshots
        c(self.rf_filter, self.snap_iq_vec, self.snap_iq_keep, self.snap_iq)
        c(self.eq_agc, self.snap_pre_vec, self.snap_pre_keep, self.snap_pre)
        c(self.eq_play if self.EQ_PLAYER else self.eq,       # the constellation shows what you hear
          self.snap_post_vec, self.snap_post_keep, self.snap_post)
        c(self.fm_demod, self.snap_mpx_vec, self.snap_mpx_keep, self.snap_mpx)

        # RDS
        c(self.fm_demod, self.rds_xlate, self.rds_agc, self.rds_costas,
          self.rds_resamp, self.rds_vec, self.rds)

        if self.lmr_only:
            print("*** DIAGNOSTIC MODE: L-R path only (centered content silent) ***")
            c(self.lpr_delay, blocks.null_sink(gr.sizeof_float))
            c(self.lmr_lp_filter, self.lmr_blend_l, self.deemph_l)
            c(self.lmr_lp_filter, self.lmr_blend_r, self.deemph_r)
        else:
            c(self.lmr_lp_filter, self.lmr_blend_l)
            c(self.lmr_lp_filter, self.lmr_blend_r)
            c(self.lpr_delay, (self.matrix_l, 0))
            c(self.lmr_blend_l, (self.matrix_l, 1))
            c(self.lpr_delay, (self.matrix_r, 0))
            c(self.lmr_blend_r, (self.matrix_r, 1))
            c(self.matrix_l, self.scale_l, self.deemph_l)
            c(self.matrix_r, self.scale_r, self.deemph_r)

        for ch in ("l", "r"):
            g = lambda n: getattr(self, f"{n}_{ch}")
            c(g("deemph"), g("treble_dry"), g("treble_full"), (g("treble_sum"), 0))
            c(g("deemph"), g("treble_lp"), g("treble_lp_scale"), (g("treble_sum"), 1))
            c(g("treble_sum"), g("limiter"), g("vol_gain"), g("audio_decim"))
        c(self.audio_decim_l, (self.dip_m_sum, 0))
        c(self.audio_decim_r, (self.dip_m_sum, 1))
        c(self.audio_decim_l, (self.dip_s_dif, 0))
        c(self.audio_decim_r, (self.dip_s_dif, 1))
        c(self.dip_m_sum, self.dip_m, (self.nr, 2))
        c(self.dip_s_dif, self.dip_s, (self.nr, 3))
        c((self.nr, 1), (self.dip_s_pull, 0))
        c(self.dip_skeep, (self.dip_s_pull, 1))
        c((self.nr, 0), (self.dip_l, 0))
        c(self.dip_s_pull, (self.dip_l, 1))
        c((self.nr, 0), (self.dip_r, 0))
        c(self.dip_s_pull, (self.dip_r, 1))
        for ch, stage in (("l", self.dip_l), ("r", self.dip_r)):
            g = lambda n: getattr(self, f"{n}_{ch}")
            c(stage, (g("dip_hp"), 0))
            c(stage, g("dip_lp"), (g("dip_hp"), 1))
            c(g("dip_hp"), (g("dip_hpg"), 0))
            c(self.dip_dly, (g("dip_hpg"), 1))
            c(stage, (g("dip_out"), 0))
            c(g("dip_hpg"), (g("dip_out"), 1))
        for idx, ch in enumerate(("l", "r")):
            g = lambda n: getattr(self, f"{n}_{ch}")
            src = getattr(self, f"dip_out_{ch}")
            c(src, (g("tone_sum"), 0))
            c(src, g("tone_bass_lp"), g("tone_bass_k"), (g("tone_sum"), 1))
            c(src, (g("tone_tre_hp"), 0))
            c(src, g("tone_tre_lp"), (g("tone_tre_hp"), 1))
            c(g("tone_tre_hp"), g("tone_tre_k"), (g("tone_sum"), 2))
            c(g("tone_sum"), g("headroom"), (self.audio_sink, idx))

        # Monitoring taps (listen only)
        if self.audio_meter is not None:
            for idx, ch in enumerate(("l", "r")):
                g = lambda n: getattr(self, f"{n}_{ch}")
                c(g("deemph"), g("meter_decim"), (self.audio_meter, idx))
                c(g("meter_decim"), g("kw1"), g("kw2"), (self.audio_meter, idx + 2))
            c(self.dc_sub, self.mod_xlate, self.mod_chan, self.mod_demod, self.mod_lp, self.mod_meter)

    # ------------------------------------------------------------------
    # Control state
    # ------------------------------------------------------------------
    def _init_control(self, rf_gain, agc, log_path):
        now = time.monotonic()
        self.agc_enabled   = bool(agc)
        self._gain_idx     = self._nearest_gain_idx(rf_gain)
        self.gain_db       = self.gains[self._gain_idx]
        self.src.set_gain(self.gain_db)

        self._t_last       = now
        self._blank_until  = now + self.AGC_SETTLE_S
        self._snap_next    = True
        self._last_circ    = None
        self._last_wb      = None
        self._q_hist       = deque(maxlen=self.ATTACK_CONFIRM)
        self._wb_seen      = now              # last time a fresh wideband window arrived
        self._q_log        = deque()          # (t, q_raw) for AGC judging
        self._q_ctrl       = 0.0
        self._q_slow       = 0.0
        self._q_slow_snap  = True
        self._hold_until   = now
        self._dc           = 0j
        self._dc_fresh     = True

        self._last_clip_t  = now
        self._agc_probe    = None             # (start_t, prev_idx, baseline_db)
        self._agc_lockout  = now
        self._agc_next_ok  = now

        self.blend_enabled = True
        self.hicut_enabled = True
        self.eq_mode       = 'auto'           # 'off' | 'on' | 'auto'
        self._eq_w         = 0.0              # 0 = raw signal, 1 = equalized
        self._eq_diff      = 0.0              # 1 s average of CNR(equalized) - CNR(raw)
        self._eq_bad_since = None
        self._last_eqp     = None
        self._last_plp     = None
        self.if_mode       = 'auto'           # 'auto' | 'wide' | 'narrow'
        self.if_khz        = self.IF_WIDE_KHZ # applied half-bandwidth (IF_WIDE_KHZ = wide)
        self.if_target_khz = self.IF_WIDE_KHZ
        self._if_cur       = self.IF_WIDE_KHZ
        self._if_psd       = None             # channel spectrum, averaged
        self._if_last_iq   = None
        self._if_last_wb   = None
        self._n0_hz        = None             # noise floor, power per Hz
        self._if_t         = 0.0
        self._if_win       = np.hanning(self.FFT_N)
        self._if_wp        = float(np.sum(self._if_win ** 2))
        self._wb_win       = np.hanning(self.WB_N)
        self._wb_wp        = float(np.sum(self._wb_win ** 2))
        fr = np.abs(np.fft.fftshift(np.fft.fftfreq(self.FFT_N, 1 / self.mpx_rate)))
        self._if_af        = fr
        self._if_nb95      = int(np.sum(fr < 95e3))
        self._if_nb200     = int(np.sum(fr < 100e3))
        self._if_rings     = [(fr >= k - 2e3) & (fr < k + 2e3) for k in np.arange(0, 96e3, 1e3)]
        self.spec_snr_db   = None             # station SNR in 200 kHz, from the spectrum
        self.occupied_khz  = None             # where the station meets the noise
        self._edge_hold    = 0.0              # ...held from the loud moments
        self._snr_inst     = deque(maxlen=20)   # per-snapshot SNR, last ~2 s
        self._q_win        = deque()          # (t, q) for the smart release
        self.dip_hicut     = self.DIP_HICUT
        self.noise_reduction = self.NOISE_REDUCTION
        self._dev_snaps    = deque(maxlen=25) # (program RMS deviation kHz, L-R share), ~5 s
        self._dev_last     = None
        self.dev_khz       = None             # estimated peak deviation of this station
        self.lr_program    = None             # carries real L-R (stereo program)?
        self.audio_top_khz = 15.0             # highest audio frequency with real energy
        self.if_snr_db     = None             # what the IF eases open by: the dips, not the average
        self.mp_excess_db  = 0.0              # circle CNR shortfall vs the noise: multipath
        self.eq_gate       = False            # auto: multipath present, the EQ may engage
        self._gate_t       = 0.0
        self._q_pre_slow   = None
        self.learn_share   = 1.0
        self._learn_adv    = 0.0              # learner minus raw circle, during program
        self._prog_gain    = 0.0              # player minus raw circle, program only (credit)
        self._mp_held      = 0.0              # multipath excess, held from the last program window
        self.q_exp_db      = None             # IF-spectrum CNR (None when not available)
        self.eq_ctrl_cap_db = None            # credit cap in force (None when not capping)
        self.modulating    = False
        self.learn_cnr_db  = 0.0              # the adaptive equalizer itself (EQ_PLAYER)
        self.eq_copies     = 0                # times the player took the learner's taps
        self._run          = 0                # consecutive modulated 20 ms windows
        self._run_diff     = 0.0              # learner minus player CNR over this run
        self._play_bad_since = None
        self.eq_cnr_db     = 0.0
        self.pre_cnr_db    = 0.0
        self.eq_gain_db    = 0.0
        self.eq_active     = False
        self._last_mod     = None
        self.mod_khz       = 0.0              # L+R RMS deviation, latest 20 ms window
        self._muted        = False
        self._out_gain     = (1.0, 1.0)       # user volume x balance, left / right
        self._tune_cnr     = []               # raw CNR windows since the last tune (for seek)
        self._rds_reset_pending = False

        self._pilot_on     = False
        self._ind_on       = False
        self._ind_changed  = now

        # Public read-outs
        self.cnr_db = self.raw_cnr_db = 0.0
        self.channel_dbfs = self.composite_dbfs = -120.0
        self.clip_pct = 0.0
        self.pilot_level = 0.0
        self.pilot_present = False
        self.current_blend = 0.0
        self.current_treble = 1.0
        self.current_vol = 1.0
        self.stereo_indicator = False

        self._log = None
        if log_path:
            # append, so a dongle reconnect in the GUI does not wipe the drive log
            self._log = open(log_path, "a", buffering=1)
            if self._log.tell() == 0:
                self._log.write("t,freq_mhz,center_mhz,agc,gain_db,clip_pct,composite_dbfs,"
                                "channel_dbfs,cnr_raw_db,cnr_ctrl_db,pilot,stereo,blend,treble,vol,"
                                "cnr_pre_db,cnr_eq_db,cnr_learn_db,eq_cap_db,q_exp_db,modulating,eq_active,"
                                "static_cut,dip_multipath\n")
        self._t0 = now

    def _nearest_gain_idx(self, g):
        return int(np.argmin([abs(x - g) for x in self.gains]))

    def _apply_gain_idx(self, idx, now):
        idx = int(min(max(idx, 0), len(self.gains) - 1))
        if idx == self._gain_idx:
            return False
        self._gain_idx = idx
        self.gain_db = self.gains[idx]
        self.src.set_gain(self.gain_db)
        self._blank_until = now + self.AGC_SETTLE_S
        self._dc_fresh = True                 # LO leakage changes with gain
        return True

    def _step_idx(self, direction, min_db):
        """Index at least min_db away from the current gain in the given direction
        (upward moves stop at the AGC ceiling)."""
        cur = self.gains[self._gain_idx]
        idx = self._gain_idx
        while 0 <= idx + direction < len(self.gains):
            if direction > 0 and self.gains[idx + 1] > self.AGC_MAX_DB:
                break
            idx += direction
            if abs(self.gains[idx] - cur) >= min_db:
                break
        return idx

    # ------------------------------------------------------------------
    # Public controls (safe to call from a GUI thread)
    # ------------------------------------------------------------------
    @property
    def is_stereo(self):
        return self.stereo_indicator

    @property
    def pilot_snr(self):          # legacy read-out names
        return self.cnr_db

    @property
    def raw_snr(self):
        return self.raw_cnr_db

    @property
    def sig_quality_db(self):
        return self.channel_dbfs

    def rf_freq(self, freq):
        """RF frequency actually received for a channel (87.7 -> 87.75 MHz)."""
        khz = int(round(freq / 1e3))
        return self.CHANNEL_RF_KHZ.get(khz, khz) * 1e3

    @property
    def rds_ps(self):
        return self.rds.ps

    @property
    def rds_rt(self):
        return self.rds.rt

    @property
    def rds_pi(self):
        return self.rds.pi

    @property
    def rds_call(self):
        """US call letters derived from the PI code (None if not a US call)."""
        return self.rds.call

    @property
    def rds_pty(self):
        return self.rds.pty

    def snapshot(self, kind):
        """Latest FFT_N samples: kind 'iq' = channel IQ (complex, 240 kHz),
        'mpx' = demodulated composite (float, 1.8 = 75 kHz deviation)."""
        if kind == 'iq':
            return np.asarray(self.snap_iq.level(), dtype=np.complex64)
        if kind == 'pre':                       # level-normalised, before the equalizer
            return np.asarray(self.snap_pre.level(), dtype=np.complex64)
        if kind == 'post':                      # after the equalizer
            return np.asarray(self.snap_post.level(), dtype=np.complex64)
        return np.asarray(self.snap_mpx.level(), dtype=np.float32)

    @property
    def rds_synced(self):
        return self.rds.synced

    # the rest of RDS: traffic flags, music/speech, AF list, clock, PTYN, link health
    @property
    def rds_tp(self):
        return self.rds.tp

    @property
    def rds_ta(self):
        return self.rds.ta

    @property
    def rds_ms(self):
        return self.rds.ms

    @property
    def rds_af(self):
        return sorted(self.rds.af)

    @property
    def rds_ct(self):
        return self.rds.ct

    @property
    def rds_ptyn(self):
        return self.rds.ptyn

    @property
    def rds_rate(self):
        """RDS groups per second (about 11.4 when every group gets through)."""
        t = list(self.rds._g_times)
        return (len(t) - 1) / (t[-1] - t[0]) if len(t) > 2 and t[-1] > t[0] else 0.0

    @property
    def rds_bler(self):
        """Share of RDS blocks that failed their check, percent."""
        r = self.rds
        return 100.0 * (1 - r.blocks_ok / r.blocks_all) if r.blocks_all else 0.0

    def cnr_since_tune(self):
        """Raw CNR windows measured since the last set_freq() (settle time excluded)."""
        with self._lock:
            return list(self._tune_cnr)

    def set_output_gain(self, left, right):
        """User volume and balance as linear gains (applied after the soft mute)."""
        with self._lock:
            self._out_gain = (max(0.0, float(left)), max(0.0, float(right)))

    def set_mute(self, muted):
        """Hard mute (used while seeking); audio returns at the normal soft-mute level."""
        with self._lock:
            self._muted = bool(muted)
            if self._muted:
                self.vol_gain_l.set_k(0.0)
                self.vol_gain_r.set_k(0.0)

    def set_enhancements(self, blend=None, hicut=None):
        """Turn stereo blend / high-cut on or off (off = always full stereo /
        full treble whenever there is a pilot / signal)."""
        with self._lock:
            if blend is not None:
                self.blend_enabled = bool(blend)
            if hicut is not None:
                self.hicut_enabled = bool(hicut)

    def set_eq_mode(self, mode):
        """'off', 'on' or 'auto' (use the equalizer only when it measurably helps;
        it drops out by itself when it stops helping, parked or moving)."""
        with self._lock:
            if mode in ('off', 'on', 'auto'):
                self.eq_mode = mode

    def eq_reset(self):
        self.eq.set_taps(self._eq_init)
        self._play_taps = list(self._eq_init[::-1])
        self.eq_play.set_taps(self._play_taps)
        self._run, self._run_diff = 0, 0.0
        self._eq_diff, self._eq_bad_since = 0.0, None
        self._prog_gain, self._mp_held = 0.0, 0.0

    def set_tone(self, bass_db, treble_db):
        """Bass / treble in dB (+/-TONE_RANGE_DB). For a cut the shelf corner
        moves (bass up by 1/g, treble down by g) so cut mirrors boost exactly;
        the one-pole filters keep their state, so nothing clicks."""
        r = self.TONE_RANGE_DB
        gb = 10 ** (min(max(bass_db, -r), r) / 20)
        gt = 10 ** (min(max(treble_db, -r), r) / 20)
        fb = self.TONE_BASS_HZ / (gb if gb < 1 else 1.0)
        ft = self.TONE_TREBLE_HZ * (gt if gt < 1 else 1.0)
        ab = 1 - math.exp(-2 * math.pi * fb / self.audio_rate)
        at = 1 - math.exp(-2 * math.pi * ft / self.audio_rate)
        hr = 10 ** (-self._tone_atten_db(bass_db, treble_db) / 20)
        for ch in ("l", "r"):
            getattr(self, f"tone_bass_lp_{ch}").set_taps(ab)
            getattr(self, f"tone_bass_k_{ch}").set_k(gb - 1)
            getattr(self, f"tone_tre_lp_{ch}").set_taps(at)
            getattr(self, f"tone_tre_k_{ch}").set_k(gt - 1)
            getattr(self, f"headroom_{ch}").set_k(hr)

    def _tone_atten_db(self, bass_db, treble_db):
        """Attenuation after the tone controls. With TONE_MAKEUP, just enough
        for the biggest boost to fit (plus a little), so the level stays put."""
        if not self.TONE_MAKEUP:
            return self.HEADROOM_DB
        r = self.TONE_RANGE_DB
        return max(0.0, min(bass_db, r), min(treble_db, r)) + self.TONE_SAFETY_DB

    def set_agc(self, enabled):
        with self._lock:
            self.agc_enabled = bool(enabled)
            self._agc_probe = None
            self._agc_lockout = time.monotonic()

    def set_manual_gain(self, gain_db):
        """Manual override: turns AGC off and sets the nearest tuner step."""
        with self._lock:
            self.agc_enabled = False
            self._agc_probe = None
            self._apply_gain_idx(self._nearest_gain_idx(gain_db), time.monotonic())

    def set_gain(self, gain_db):
        self.set_manual_gain(gain_db)

    def set_freq(self, freq):
        with self._lock:
            now = time.monotonic()
            self.freq = freq
            self.src.set_center_freq(self.rf_freq(freq) + self.LO_NUDGE)
            self._tune_cnr = []
            self.rds.request_reset()
            self._rds_reset_pending = True        # and again once stale samples have passed
            self._blank_until = now + self.TUNE_SETTLE_S
            self._snap_next = True
            self._q_slow_snap = True
            self._q_hist.clear()
            self._q_log.clear()
            self._agc_probe = None
            self._agc_lockout = now
            self._last_clip_t = now - self.AGC_UP_WAIT_S + 0.5   # allow a quick first look
            self._pilot_on = False
            self.eq_reset()
            self._eq_w = 0.0
            self.eq_mix_raw.set_k(1.0)
            self.eq_mix_eq.set_k(0.0)
            self._if_psd, self._q_pre_slow, self._edge_hold = None, None, 0.0
            self._snr_inst.clear()
            self._dev_snaps.clear()
            self.dev_khz, self.lr_program = None, None
            self._q_win.clear()
            self._if_apply(self.IF_WIDE_KHZ if self.if_mode != 'narrow' else self.IF_NARROW_KHZ)
            self.eq_gate, self._gate_t = False, now

    def _if_failed(self, e):
        """The IF measurement hit something unexpected: fall back to wide, keep the
        equalizer gate closed, say so once, and let the radio play on."""
        if not getattr(self, '_if_fail_said', False):
            self._if_fail_said = True
            print(f"IF bandwidth: {type(e).__name__}: {e} (staying wide)")
        try:
            if self.if_khz < self.IF_WIDE_KHZ and self.if_mode == 'auto':
                self._if_apply(self.IF_WIDE_KHZ)
        except Exception:
            pass
        self.eq_gate = False

    def set_noise_reduction(self, on):
        self.noise_reduction = bool(on)

    @property
    def nr_reduction_db(self):
        return self.nr.reduction_db

    def set_dip_hicut(self, on):
        """Fast hi-cut and stereo pull during dips (the tape-dropout treatment)."""
        self.dip_hicut = bool(on)
        self.dip_ctl.enabled = self.dip_hicut

    @property
    def dip_activity(self):
        return self.dip_ctl.activity

    @property
    def dip_multipath(self):
        """How much stereo the fast multipath trigger is pulling right now (0..1)."""
        return self.dip_ctl.mp_activity

    def _dev_update(self):
        """Station deviation and stereo content, from the demodulator ahead of the
        IF (so a narrow IF can't make it read low). Noise is taken out using the
        empty 16-18.5 kHz gap (FM noise rises with frequency squared)."""
        v = self.dev_probe.level()
        if len(v) != 4096 or (self._dev_last is not None and v[0] == self._dev_last) or not np.any(np.asarray(v)):
            return
        self._dev_last = v[0]
        x = np.asarray(v)
        w = self._if_win if len(self._if_win) == 4096 else np.hanning(4096)
        P = np.abs(np.fft.rfft((x - np.mean(x)) * w)) ** 2
        P *= 2.0 / (np.sum(w ** 2) * len(x))                        # power per bin, kHz^2
        f = np.fft.rfftfreq(len(x), 1 / self.mpx_rate)
        band = lambda a, b: float(np.sum(P[(f >= a) & (f < b)]))
        gap = band(16000, 18500)
        k3 = lambda a, b: (b ** 3 - a ** 3)
        g3 = k3(16.0, 18.5)
        lpr = max(band(50, 15000) - 0.8 * gap * k3(0.05, 15.0) / g3, 1e-6)
        lmr = max(band(23000, 53000) - 0.8 * gap * k3(23.0, 53.0) / g3, 0.0)
        # Is there program above 5 kHz, or only the noise the 16-18.5 kHz gap predicts?
        # (speech has nothing up there; music does) -> highest modulating frequency
        # (noise scaled flat from the gap: on weak signals it's mostly threshold clicks,
        # which are flat, and flat is the cautious assumption for this test)
        hf_ratio = band(5000, 15000) / max(gap * (10.0 / 2.5), 1e-12)
        self._dev_snaps.append((math.sqrt(lpr + lmr), lmr / (lpr + lmr), 15.0 if hf_ratio > 1.6 else 5.0))
        if len(self._dev_snaps) >= self.DEV_MIN_SNAPS:
            rms = np.percentile([a for a, _, _ in self._dev_snaps], 90)
            self.dev_khz = float(min(75.0, self.DEV_CREST * rms))
            self.lr_program = float(np.median([b for _, b, _ in self._dev_snaps])) > 0.05
            self.audio_top_khz = float(np.median([c for _, _, c in self._dev_snaps]))

    def set_if_mode(self, mode):
        """'auto' (follows the station), 'wide' (never narrows) or 'narrow'."""
        if mode in ('auto', 'wide', 'narrow'):
            with self._lock:
                self.if_mode = mode
                if mode == 'wide':
                    self._if_apply(self.IF_WIDE_KHZ)
                elif mode == 'narrow':
                    self._if_apply(self.IF_NARROW_KHZ)

    def _if_taps(self, khz):
        """Windowed-sinc low-pass of fixed length (so a fixed delay); None or
        IF_WIDE_KHZ and up = a pure delay (no filtering at all)."""
        n, m = self.IF_TAPS, (self.IF_TAPS - 1) // 2
        if khz is None or khz >= self.IF_WIDE_KHZ:
            h = np.zeros(n)
            h[m] = 1.0
        else:
            fc = khz * 1e3 / self.mpx_rate
            h = 2 * fc * np.sinc(2 * fc * (np.arange(n) - m)) * np.kaiser(n, 6.0)
            h /= np.sum(h)
        return [float(v) for v in h]

    def _if_apply(self, khz):
        self._if_cur = khz
        self.if_khz = khz
        self.if_filter.set_taps(self._if_taps(khz))

    def _if_update(self, now, dt):
        """Spectrum and noise floor -> how wide the station really is -> slide
        the IF filter toward that (rate-limited, so it morphs rather than jumps)."""
        iq = self.snap_iq.level()
        if len(iq) == self.FFT_N and (self._if_last_iq is None or iq[0] != self._if_last_iq) \
                and np.any(np.asarray(iq)):                       # all zeros = no samples yet
            self._if_last_iq = iq[0]
            X = np.abs(np.fft.fftshift(np.fft.fft(np.asarray(iq) * self._if_win))) ** 2 / self._if_wp
            self._if_psd = X if self._if_psd is None else 0.9 * self._if_psd + 0.1 * X
            if getattr(self, '_if_n0', 0.0) > 0:                 # this snapshot's SNR (fades show up)
                si = float(np.sum(X[self._if_af < 95e3])) - self._if_n0 * self._if_nb95
                self._snr_inst.append(10 * math.log10(max(si, 1e-20) / (self._if_n0 * self._if_nb200)))
        wb = self.snap_wb.level()
        if len(wb) == self.WB_N and (self._if_last_wb is None or wb[0] != self._if_last_wb) \
                and np.any(np.asarray(wb)):
            self._if_last_wb = wb[0]
            P = np.abs(np.fft.fftshift(np.fft.fft(np.asarray(wb) * self._wb_win))) ** 2 / self._wb_wp
            k = self.WB_N // 10                                   # ignore the tuner's band edges
            n0 = float(np.percentile(P[k:-k], 30)) / (self.samp_rate / self.WB_N)
            if n0 > 0 and math.isfinite(n0):
                self._n0_hz = n0 if self._n0_hz is None else 0.8 * self._n0_hz + 0.2 * n0
        if now - self._if_t < 0.25:
            return
        self._if_t = now
        if self._if_psd is None or self._n0_hz is None or now < self._blank_until:
            return
        af, psd = self._if_af, self._if_psd
        n0 = self._n0_hz * self.mpx_rate / self.FFT_N                 # per bin, from the whole band
        # The tuner's noise is a little higher near where it's tuned. If the outer
        # part of the channel is within 6 dB of the band's floor, that's noise
        # (a weak station): use it. Much higher is a strong station's sidebands.
        n0_local = float(np.median(psd[(af > 60e3) & (af < 88e3)]))
        if 0 < n0_local <= n0 * 4.0:
            n0 = n0_local
        if not (n0 > 0 and math.isfinite(n0)):                   # nothing to measure against yet
            return
        S = float(np.sum((psd - n0)[af < 95e3]))
        self._if_S, self._if_n0 = S, n0
        self.spec_snr_db = 10 * math.log10(max(S, 1e-20) / (n0 * np.sum(af < 100e3)))
        exc = np.maximum(psd - n0, 0.0)
        above = [np.mean(exc[r]) >= n0 for r in self._if_rings]   # 1 kHz steps outward
        edge, gap = 0, 0
        for i, a in enumerate(above):                           # contiguous from the centre:
            if a:                                               # stop at the first real gap
                edge, gap = i, 0
            else:
                gap += 1
                if gap >= 3:
                    break
        if edge >= self._edge_hold:
            self._edge_hold = float(edge)
        else:
            self._edge_hold = max(float(edge), self._edge_hold - 0.25 * self.IF_EDGE_DECAY)
        self.occupied_khz = self._edge_hold
        if self.if_mode == 'wide':
            tgt = self.IF_WIDE_KHZ
        elif self.if_mode == 'narrow':
            tgt = self.IF_NARROW_KHZ
        elif self.spec_snr_db >= self.IF_FULL_SNR_DB and (
                len(self._snr_inst) < 5 or np.percentile(self._snr_inst, 20) >= self.IF_FULL_SNR_DB):
            tgt = self.IF_WIDE_KHZ
        else:
            edge_t = min(max(self.occupied_khz + self.IF_MARGIN_KHZ, self.IF_MIN_KHZ), self.IF_WIDE_KHZ)
            # never narrower than the station needs (Carson: deviation + top modulating
            # frequency); unknown deviation = stay wide. Music at 75 kHz never narrows.
            if self.dev_khz is None:
                edge_t = self.IF_WIDE_KHZ
            else:
                ftop = 53.0 if self.lr_program else max(getattr(self, 'audio_top_khz', 15.0), 3.0)
                edge_t = min(max(edge_t, self.dev_khz + ftop + self.IF_MARGIN_KHZ), self.IF_WIDE_KHZ)
            # ease open by the low points of the last 2 s, so fades still get the narrow filter
            snr_r = float(np.percentile(self._snr_inst, 20)) if len(self._snr_inst) >= 5 else self.spec_snr_db
            self.if_snr_db = min(snr_r, self.spec_snr_db)
            frac = (self.if_snr_db - self.IF_ADAPT_SNR_DB) / (self.IF_FULL_SNR_DB - self.IF_ADAPT_SNR_DB)
            tgt = edge_t + (self.IF_WIDE_KHZ - edge_t) * min(max(frac, 0.0), 1.0)
        self.if_target_khz = tgt
        cur = self._if_cur
        rate = self.IF_SLEW_IN if tgt < cur else self.IF_SLEW_OUT
        step = 0.25 * rate
        new = cur + max(-step, min(step, tgt - cur))
        if abs(new - self.if_khz) >= self.IF_STEP_KHZ or (new >= self.IF_WIDE_KHZ) != (self.if_khz >= self.IF_WIDE_KHZ):
            self._if_apply(new)
        else:
            self._if_cur = new

    def set_phase_trim(self, degrees):
        self.phase_trim = degrees
        self.ref_rotate.set_k(self._ref_phasor())

    def monitor(self):
        """Meter readings since the last call: dict(audio=..., mod=...) with
        mod in kHz of deviation, or None if rx_meters.py isn't installed."""
        if self.audio_meter is None:
            return None
        return dict(audio=self.audio_meter.read(), mod=self.mod_meter.read())

    def audio_lag(self):
        """How far the sound card trails what this receiver measures, in seconds:
        (meters, front end). The samples GNU Radio has queued between the meter
        tap (de-emphasis) or the channel filter and the audio sink, plus the fixed
        delays after the tap (noise reducer, treble path). The sound card's own
        buffer, or Bluetooth, isn't included. None if the counters aren't there."""
        try:
            played = self.audio_sink.nitems_read(0)
            to_audio = self.audio_rate / self.mpx_rate
            tap = self.deemph_l.nitems_written(0) * to_audio
            front = self.rf_filter.nitems_written(0) * to_audio
        except Exception:
            return None
        fixed = self.NR_FFT + getattr(self, '_tr_delay', 0) * to_audio
        r = float(self.audio_rate)
        return max(0.0, tap - played + fixed) / r, max(0.0, front - played + fixed) / r

    def eq_taps(self):
        """The equalizer as heard: (taps with the main path near tap 0, mix
        weight 0 = raw .. 1 = equalized). Taps are spaced 1 / mpx_rate."""
        with self._lock:
            return np.asarray(self._play_taps, np.complex64), float(self._eq_w)

    def close(self):
        if self._log:
            self._log.close()
            self._log = None

    # ------------------------------------------------------------------
    # Control step: call every ~20 ms (any rate works, timing is measured)
    # ------------------------------------------------------------------
    def update_blend(self):
        with self._lock:
            now = time.monotonic()
            dt = min(max(now - self._t_last, 0.0), 0.2)
            self._t_last = now

            # ---- wideband: overload and composite level ----
            wb = self.wb_probe.level()
            new_wb = wb != self._last_wb
            self._last_wb = wb
            if new_wb:
                self._wb_seen = now
            elif now - self._wb_seen > self.STALL_S:
                raise RuntimeError(f"no samples from the dongle for {now - self._wb_seen:.1f} s")
            pw = max(wb.real / self._wb_n, 1e-12)
            clip_frac = wb.imag / self._wb_n
            self.composite_dbfs = 10 * math.log10(pw)
            self.clip_pct = 100.0 * clip_frac

            # ---- DC canceller (estimate on the raw stream, subtract) ----
            d = self.dc_probe.level() / self._dc_n
            a = 0.3 if self._dc_fresh else 0.02
            self._dc = (1 - a) * self._dc + a * d
            if self._dc_fresh and now > self._blank_until:
                self._dc_fresh = False
            self.dc_sub.set_k(-self._dc)

            # ---- IF bandwidth (an enhancement: if it ever fails, go wide and carry on) ----
            try:
                self._dev_update()
                self._if_update(now, dt)
            except Exception as e:
                self._if_failed(e)

            # ---- circle CNR ----
            cp = self.circ_probe.level()
            new_window = cp != self._last_circ
            self._last_circ = cp
            m2 = cp.real / self._circ_n
            m4 = cp.imag / self._circ_n
            self.channel_dbfs = 10 * math.log10(max(m2, 1e-12))
            s = math.sqrt(max(2 * m2 * m2 - m4, 0.0))
            n = max(m2 - s, 1e-15)
            q_raw = min(max(10 * math.log10(max(s, 1e-15) / n), -10.0), 40.0)

            # ---- multipath? The circle can only be as good as the noise allows;
            # if it is clearly worse than that, the rest is echo distortion,
            # which the equalizer can fix. Otherwise it would only narrow the band.
            # (the slow circle comes from averaged moments, not averaged dB: at low
            # CNR single readings clamp and would drag a dB average down)
            q_exp_now = None
            try:
                if new_window and now >= self._blank_until and m2 > 0:
                    kr = m4 / (m2 * m2)
                    self._q_pre_slow = kr if self._q_pre_slow is None else \
                        self._q_pre_slow + 0.05 * (kr - self._q_pre_slow)
                if self._q_pre_slow is not None and getattr(self, '_if_S', None) is not None \
                        and getattr(self, '_if_n0', 0.0) > 0:
                    r_ = math.sqrt(max(2.0 - self._q_pre_slow, 0.0))
                    q_slow = min(max(10 * math.log10(max(r_, 1e-9) / max(1.0 - r_, 1e-9)), -20.0), 40.0)
                    bw = self.if_khz if self.if_khz < self.IF_WIDE_KHZ else 100.0
                    nb = 2 * bw * 1e3 / (self.mpx_rate / self.FFT_N)
                    q_exp = 10 * math.log10(max(self._if_S, 1e-20) / (self._if_n0 * nb))
                    q_exp_now = q_exp
                    self.mp_excess_db = min(q_exp, self.CIRCLE_CEILING_DB) - q_slow
                    trusted = q_exp >= self.EQ_GATE_MIN_DB
                    want = trusted and (self.mp_excess_db >= self.EQ_MP_ON_DB if not self.eq_gate
                                        else self.mp_excess_db > self.EQ_MP_OFF_DB)
                    # the learner's own result counts as evidence too
                    proven = self.learn_share >= self.EQ_MIN_MAIN_SHARE and (
                        self._learn_adv >= self.EQ_LEARN_PROOF_DB if not self.eq_gate
                        else self._learn_adv > self.EQ_LEARN_PROOF_DB / 2)
                    want = want or proven
                    if want != self.eq_gate:
                        if now - self._gate_t >= (1.0 if want else 2.0):
                            self.eq_gate, self._gate_t = want, now
                            if not want and self.eq_mode == 'auto':
                                self.eq_reset()           # start clean next time
                    else:
                        self._gate_t = now
            except Exception as e:                # never a reason to stop the radio
                self._if_failed(e)
            self.q_exp_db = q_exp_now

            # ---- equalizer: its own circle CNR, then raw vs equalized ----
            ep = self.eq_probe.level()
            new_eqw = ep != self._last_eqp
            self._last_eqp = ep
            e2, e4 = ep.real / self._circ_n, ep.imag / self._circ_n
            es = math.sqrt(max(2 * e2 * e2 - e4, 0.0))
            q_eq = min(max(10 * math.log10(max(es, 1e-15) / max(e2 - es, 1e-15)), -10.0), 40.0)
            self.learn_cnr_db = q_eq
            q_hear = q_eq
            if self.EQ_PLAYER:                    # what you hear: the player
                pp = self.pl_probe.level()
                self._last_plp = pp
                p2, p4 = pp.real / self._circ_n, pp.imag / self._circ_n
                ps = math.sqrt(max(2 * p2 * p2 - p4, 0.0))
                q_hear = min(max(10 * math.log10(max(ps, 1e-15) / max(p2 - ps, 1e-15)), -10.0), 40.0)
            self.eq_cnr_db = q_hear
            # Is the station modulating right now (this 20 ms)? In a pause the
            # signal is little more than a bare carrier: a perfect circle, so the
            # equalizer can "win" by narrowing down onto the carrier and the
            # circle reading flatters it. Nothing is judged or copied then.
            mp = self.mod_probe.level()
            if mp != self._last_mod:
                self._last_mod = mp
                self.mod_khz = math.sqrt(max(mp, 0.0) / self._circ_n) / 1.8 * 75.0
            modulating = self.mod_khz >= self.EQ_MOD_GATE_KHZ
            self.modulating = modulating
            if modulating:
                self._mp_held = self.mp_excess_db
            if new_eqw and now >= self._blank_until and modulating:
                self._prog_gain += 0.05 * ((q_hear - q_raw) - self._prog_gain)   # ~1 s of program
            if new_eqw and now >= self._blank_until and self.EQ_PLAYER:
                if not modulating:
                    self._run, self._run_diff = 0, 0.0
                else:
                    if self._run == 0:            # words again: restart the learner from what you hear
                        self.eq.set_taps(list(self._play_taps[::-1]))
                    self._run += 1
                    if self._run > 2:             # give it a moment before judging
                        self._run_diff += 0.3 * ((q_eq - q_hear) - self._run_diff)
                    w_ = np.asarray(self.eq.taps())
                    self.learn_share = float(np.max(np.abs(w_)) ** 2 / max(float(np.sum(np.abs(w_) ** 2)), 1e-12))
                    allowed = self.eq_gate or self.eq_mode == 'on'
                    if allowed and self._run >= self.EQ_COPY_RUN and self._run_diff >= self.EQ_COPY_MARGIN_DB \
                            and self.learn_share >= self.EQ_MIN_MAIN_SHARE:
                        self._play_taps = list(np.asarray(self.eq.taps())[::-1])
                        self.eq_play.set_taps(self._play_taps)
                        self.eq_copies += 1
                        self._run_diff = 0.0
            if new_eqw and now >= self._blank_until and (modulating or not self.EQ_PLAYER):
                self._eq_diff += 0.05 * ((q_hear - q_raw) - self._eq_diff)   # ~1 s of program
                self._learn_adv += 0.05 * ((q_eq - q_raw) - self._learn_adv)
                if q_eq < q_raw - self.EQ_DIVERGE_DB:                       # learner gone wrong
                    self._eq_bad_since = self._eq_bad_since or now
                    if now - self._eq_bad_since > 2.0:
                        self._eq_bad_since = None
                        if self.EQ_PLAYER:            # restart it from what you hear
                            self.eq.set_taps(list(self._play_taps[::-1]))
                        else:
                            self.eq_reset()
                else:
                    self._eq_bad_since = None
                if self.EQ_PLAYER and q_hear < q_raw - self.EQ_DIVERGE_DB:  # player stale (moved on)
                    self._play_bad_since = self._play_bad_since or now
                    if now - self._play_bad_since > 2.0:
                        self._play_bad_since = None
                        self._play_taps = list(self._eq_init[::-1])
                        self.eq_play.set_taps(self._play_taps)
                else:
                    self._play_bad_since = None
            self.eq_gain_db = self._eq_diff

            if self.eq_mode == 'on':
                eq_t = 1.0
            elif self.eq_mode == 'off':
                eq_t = 0.0
            elif not self.eq_gate:                # no multipath to fix: leave the signal alone
                eq_t = 0.0
            else:
                eq_t = self._eq_w
                if self._eq_diff > self.EQ_ENGAGE_DB:
                    eq_t = 1.0
                elif self._eq_diff < self.EQ_RELEASE_DB:
                    eq_t = 0.0
            step = dt / self.EQ_FADE_S
            self._eq_w += max(-step, min(step, eq_t - self._eq_w))
            self.eq_mix_raw.set_k(1.0 - self._eq_w)
            self.eq_mix_eq.set_k(self._eq_w)
            self.eq_active = self._eq_w > 0.5
            q_pre = q_raw                         # raw channel (the AGC judges this one)
            self.pre_cnr_db = q_pre
            self.eq_ctrl_cap_db = None
            if self.eq_active:
                if self.EQ_CREDIT_CAP:
                    credit = max(self._prog_gain, self._mp_held, 0.0)
                    cap = q_pre + credit + self.EQ_CREDIT_MARGIN_DB
                    if self.EQ_CREDIT_SPEC_CAP and self.q_exp_db is not None:
                        cap = min(cap, max(self.q_exp_db + self.EQ_CREDIT_MARGIN_DB, q_pre))
                    self.eq_ctrl_cap_db = cap
                    q_raw = min(q_hear, cap)      # the player is what is demodulated
                else:
                    q_raw = q_eq                  # judge what is actually being demodulated

            blanked = now < self._blank_until

            # slow CNR (about 1 s) for gating the pilot detector
            if new_window and not blanked:
                if self._q_slow_snap:
                    self._q_slow, self._q_slow_snap = q_raw, False
                else:
                    self._q_slow += 0.02 * (q_raw - self._q_slow)

            # ---- pilot ----
            # At very low CNR the PLL locks onto noise and the coherent detector
            # reads a fake pilot, so a usable signal is required as well.
            p = self.pilot_probe.level() / self._pilot_n
            self.pilot_level = 2 * abs(p)
            if self.pilot_level > self.PILOT_ON and self._q_slow >= self.PILOT_MIN_CNR_DB:
                self._pilot_on = True
            elif self.pilot_level < self.PILOT_OFF or self._q_slow < self.PILOT_MIN_CNR_DB - 3.0:
                self._pilot_on = False
            self.pilot_present = self._pilot_on

            if not blanked and self._rds_reset_pending:
                self._rds_reset_pending = False
                self.rds.request_reset()

            if new_window and not blanked:
                self._q_win.append((now, q_raw))
            while self._q_win and now - self._q_win[0][0] > self.QUAL_WINDOW_S:
                self._q_win.popleft()
            if new_window and not blanked:
                self.raw_cnr_db = q_raw
                if len(self._tune_cnr) < 500:
                    self._tune_cnr.append(q_raw)
                self._q_hist.append(q_raw)
                self._q_log.append((now, q_pre))
            while self._q_log and now - self._q_log[0][0] > 2.0:
                self._q_log.popleft()

            # ---- noise reduction depth follows the CNR, like the blend ----
            depth = 0.0
            if self.noise_reduction:
                k = (self.NR_OFF_CNR_DB - self.cnr_db) / (self.NR_OFF_CNR_DB - self.NR_FULL_CNR_DB)
                depth = self.NR_MAX_DB * min(max(k, 0.0), 1.0)
            self.nr.depth_db += max(-0.5, min(0.5, depth - self.nr.depth_db))   # eased

            # ---- AGC ----
            if self.agc_enabled and new_wb:
                self._agc_step(now, clip_frac)

            # ---- quality tracker: confirmed attack, hold, dB/s release ----
            if self._q_win and not blanked:
                stat = float(np.percentile([v for _, v in self._q_win], self.QUAL_PCT))
                if self._snap_next or stat < self._q_ctrl:
                    self._q_ctrl = stat
                    self._snap_next = False
                else:
                    self._q_ctrl = min(stat, self._q_ctrl + self.RELEASE_FAST_DB_S * dt)
            elif self._q_hist and not blanked:
                q_att = max(self._q_hist)          # both recent windows must be bad
                if self._snap_next:
                    self._q_ctrl = q_att
                    self._snap_next = False
                elif q_att < self._q_ctrl:
                    self._q_ctrl = q_att
                    self._hold_until = now + self.HOLD_S
                elif now >= self._hold_until:
                    self._q_ctrl = min(q_att, self._q_ctrl + self.RELEASE_DB_PER_S * dt)
            q = self._q_ctrl
            self.cnr_db = q

            # ---- targets ----
            if not self._pilot_on:
                blend_t = 0.0
            elif not self.blend_enabled:
                blend_t = 1.0
            else:
                blend_t = smoothstep(q, self.STEREO_MONO_DB, self.STEREO_FULL_DB)
            treble_t = (smoothstep(q, self.TREBLE_FULL_DB, self.TREBLE_START_DB)
                        if self.hicut_enabled else 1.0)
            vol_t    = (self.MUTE_FLOOR + (1 - self.MUTE_FLOOR) * smoothstep(q, 0.0, self.MUTE_START_DB)
                        if self.SOFT_MUTE else 1.0)

            def slew(cur, tgt):
                lim = (self.BLEND_SLEW_DOWN if tgt < cur else self.BLEND_SLEW_UP) * dt
                return cur + max(-lim, min(lim, tgt - cur))

            self.current_blend  = slew(self.current_blend, blend_t)
            self.dip_ctl.blend  = self.current_blend
            self.current_treble = slew(self.current_treble, treble_t)
            self.current_vol    = slew(self.current_vol, vol_t)

            k = self.target_lmr * (1.0 if self.lmr_only else self.current_blend)
            self.lmr_blend_l.set_k(k)
            self.lmr_blend_r.set_k(k)
            self.treble_full_l.set_k(self.current_treble)
            self.treble_full_r.set_k(self.current_treble)
            self.treble_lp_scale_l.set_k(1.0 - self.current_treble)
            self.treble_lp_scale_r.set_k(1.0 - self.current_treble)
            vk = 0.0 if self._muted else self.current_vol
            self.vol_gain_l.set_k(vk * self._out_gain[0])
            self.vol_gain_r.set_k(vk * self._out_gain[1])

            # ---- STEREO indicator with hysteresis and dwell ----
            # Lit while any stereo is blended in; INDICATOR_ON/OFF at 0 = pilot only.
            want = self._ind_on
            if not self._pilot_on or self.current_blend < self.INDICATOR_OFF:
                want = False
            elif self.current_blend >= self.INDICATOR_ON:
                want = True
            if want != self._ind_on and now - self._ind_changed >= self.INDICATOR_DWELL_S:
                self._ind_on = want
                self._ind_changed = now
            self.stereo_indicator = self._ind_on

            if self._log:
                self._log.write(
                    f"{now - self._t0:.3f},{self.freq / 1e6:.3f},{(self.freq + self.LO_NUDGE) / 1e6:.4f},"
                    f"{int(self.agc_enabled)},{self.gain_db:.1f},{self.clip_pct:.4f},"
                    f"{self.composite_dbfs:.1f},{self.channel_dbfs:.1f},{self.raw_cnr_db:.1f},"
                    f"{self.cnr_db:.1f},{self.pilot_level:.3f},{int(self.stereo_indicator)},"
                    f"{self.current_blend:.3f},{self.current_treble:.3f},{self.current_vol:.3f},"
                    f"{self.pre_cnr_db:.1f},{self.eq_cnr_db:.1f},{self.learn_cnr_db:.1f},"
                    f"{'' if self.eq_ctrl_cap_db is None else format(self.eq_ctrl_cap_db, '.1f')},"
                    f"{'' if self.q_exp_db is None else format(self.q_exp_db, '.1f')},"
                    f"{int(self.modulating)},{int(self.eq_active)},"
                    f"{self.dip_activity:.3f},{self.dip_multipath:.3f}\n")

            return (self.pilot_level, self.clip_pct, self.composite_dbfs,
                    self.cnr_db, self.current_blend, self.current_treble)

    def _agc_step(self, now, clip_frac):
        if now < self._blank_until:
            return

        # 0) Above the ceiling (e.g. AGC switched on after a high manual gain): come down
        if self.gains[self._gain_idx] > self.AGC_MAX_DB:
            cap = max(i for i, g in enumerate(self.gains) if g <= self.AGC_MAX_DB)
            self._agc_probe = None
            self._apply_gain_idx(cap, now)
            return

        # 1) Overload guard: immediate, bigger jump for heavy clipping
        if clip_frac > self.AGC_CLIP_FRAC:
            drop = 6.0 if clip_frac > 0.05 else 3.0 if clip_frac > 0.005 else self.AGC_MIN_STEP_DB
            if self._agc_probe:                   # the step up caused it: undo and lock out
                self._agc_lockout = now + self.AGC_LOCKOUT_S
                self._agc_probe = None
            self._apply_gain_idx(self._step_idx(-1, drop), now)
            self._last_clip_t = now
            return

        # 2) Too hot even without clipping: back off one step
        if self.composite_dbfs > self.AGC_TARGET_HI:
            self._agc_probe = None
            self._apply_gain_idx(self._step_idx(-1, self.AGC_MIN_STEP_DB), now)
            self._last_clip_t = now
            return

        # 3) Judge a step up that is in progress
        if self._agc_probe:
            t0, prev_idx, baseline = self._agc_probe
            if now - t0 >= self.AGC_SETTLE_S + self.AGC_EVAL_S:
                after = [q for (t, q) in self._q_log if t >= t0 + self.AGC_SETTLE_S]
                self._agc_probe = None
                if not after or np.median(after) < baseline + self.AGC_MIN_IMPROVE:
                    self._apply_gain_idx(prev_idx, now)
                    self._agc_lockout = now + self.AGC_LOCKOUT_S
            return

        # 4) Consider a step up
        if (self.composite_dbfs < self.AGC_TARGET_LO
                and now - self._last_clip_t >= self.AGC_UP_WAIT_S
                and now >= self._agc_lockout
                and self._gain_idx < len(self.gains) - 1
                and self.gains[self._gain_idx + 1] <= self.AGC_MAX_DB):
            recent = [q for (t, q) in self._q_log if now - t <= 0.5]
            if len(recent) < 5:
                return
            spread = [q for (_, q) in self._q_log]      # last 2 s
            if len(spread) >= 20 and float(np.std(spread)) > self.AGC_STEADY_DB:
                # Moving: fading swamps any before/after CNR comparison, so just
                # keep the level in range. Step up only when well below target.
                if self.composite_dbfs < self.AGC_MOVING_LO:
                    step = 6.0 if self.composite_dbfs < self.AGC_TARGET_LO - 10.0 else self.AGC_MIN_STEP_DB
                    if self._apply_gain_idx(self._step_idx(+1, step), now):
                        self._last_clip_t = now                 # pace: one move per UP_WAIT
                return
            baseline = float(np.median(recent))
            if baseline >= self.AGC_GOOD_DB:
                return
            prev = self._gain_idx
            # far below target (weak station, quiet band): climb in bigger steps
            step = 6.0 if self.composite_dbfs < self.AGC_TARGET_LO - 10.0 else self.AGC_MIN_STEP_DB
            if self._apply_gain_idx(self._step_idx(+1, step), now):
                self._agc_probe = (now, prev, baseline)


def control_thread(tb, stop_event, verbose=True, period=0.02):
    last_print = 0.0
    while not stop_event.is_set():
        try:
            tb.update_blend()
            now = time.monotonic()
            if verbose and now - last_print > 0.1:
                last_print = now
                print(f"\r{'AGC' if tb.agc_enabled else 'MAN'} {tb.gain_db:4.1f}dB  "
                      f"clip {tb.clip_pct:6.3f}%  comp {tb.composite_dbfs:6.1f}dBFS  "
                      f"CNR {tb.raw_cnr_db:5.1f}/{tb.cnr_db:5.1f}dB  "
                      f"pilot {tb.pilot_level:.3f}  blend {tb.current_blend:.2f}  "
                      f"treble {tb.current_treble:.2f}  vol {tb.current_vol:.2f}  "
                      f"[{'STEREO' if tb.stereo_indicator else 'MONO  '}] "
                      f"{tb.rds_ps:8s} {tb.rds_rt[:40]}",
                      end='', flush=True)
        except Exception as e:
            if verbose:
                print(f"\ncontrol error: {e}")
        time.sleep(period)


def main():
    p = argparse.ArgumentParser(description='FM Stereo Receiver')
    p.add_argument('--freq', type=float, default=90.5e6)
    p.add_argument('--gain', type=float, default=8.7,
                   help='manual gain, or AGC starting point (dB, snapped to tuner steps)')
    p.add_argument('--no-agc', action='store_true', help='fixed manual gain')
    p.add_argument('--tuner-bw', type=float, default=200e3,
                   help='tuner IF bandwidth request; 200e3 = narrowest R82xx filter, 0 = auto')
    p.add_argument('--audio-rate', type=int, default=48000)
    p.add_argument('--audio-device', default='')
    p.add_argument('--lmr-only', action='store_true')
    p.add_argument('--lmr-gain', type=float, default=1.0, help='L-R trim (1.0 = correct)')
    p.add_argument('--phase-trim', type=float, default=0.0,
                   help='38 kHz reference trim in degrees (should not be needed)')
    p.add_argument('--log', default=None, help='write a CSV of metrics every control step')
    p.add_argument('--record-iq', default=None,
                   help='record raw IQ as interleaved int8 at 2.4 Msps (about 4.8 MB/s)')
    p.add_argument('--quiet', action='store_true')
    a = p.parse_args()

    tb = FMStereoReceiver(freq=a.freq, rf_gain=a.gain, agc=not a.no_agc,
                          audio_rate=a.audio_rate, audio_device=a.audio_device,
                          lmr_only=a.lmr_only, lmr_gain=a.lmr_gain,
                          phase_trim_deg=a.phase_trim, tuner_bw=a.tuner_bw,
                          log_path=a.log, record_iq=a.record_iq)
    print(f"FM Stereo Receiver  {a.freq / 1e6:.1f} MHz  "
          f"{'AGC' if not a.no_agc else 'manual'} start {tb.gain_db:.1f} dB")
    tb.start()
    stop = threading.Event()
    threading.Thread(target=control_thread, args=(tb, stop, not a.quiet), daemon=True).start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    stop.set()
    tb.stop()
    tb.wait()
    tb.close()
    print("\nStopped.")


if __name__ == "__main__":
    main()
