"""
radio/fm/rds.py - RDS / RBDS decoding for the FM receiver
=========================================================
RdsDecoder (a GNU Radio sink block) and the RBDS call-letter tables.
"""
import datetime
import time
from collections import deque

import numpy as np
from gnuradio import gr


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
