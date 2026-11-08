"""
radio/ui/crt.py - The monitor's scope screen.
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .art import down2d, glass_layers
from .constants import GONIO_FILL, SCOPE_DECAY
from .fonts import draw_text, font
from .theme import COLOR_DEFAULTS, CRT_BG, CRT_BOX, SS, rgb_of


# ---------------------------------------------------------------------------
# CRT: a small green X-Y tube. Left: goniometer with phase correlation.
# Right: channel before / after the equalizer, composite spectrum, or the
# multipath echo profile. Drawn as phosphor that fades between frames.
# ---------------------------------------------------------------------------

class Crt:
    BOX = CRT_BOX
    GON = (9, 4, 105, 100)                # square, relative to CRT_BOX
    CORR = (13, 106, 101, 111)            # correlation scale under it, -1 .. +1
    PLOT = (128, 12, 306, 102)

    def __init__(self, s):
        self.s = s
        self.k = k = s * SS
        x0, y0, x1, y1 = self.BOX
        self.bw, self.bh = x1 - x0, y1 - y0
        self.w, self.h = round(self.bw * s), round(self.bh * s)
        T, A = glass_layers(self.w * SS, self.h * SS, k, seed=5, curved=True)
        self.T = down2d(T)[..., None]
        self.A = down2d(A)
        self.buf = np.zeros((self.h, self.w), np.float32)
        self.set_colors({})
        self.f_lab = font('vfd', 7 * k)
        self.f_small = font('vfd', 6 * k)
        self._grat = {}

    def set_colors(self, overrides):
        c = lambda g: rgb_of(overrides.get(g) or COLOR_DEFAULTS[g])
        self.trace, self.scale_col, self.text_col = c('crt_trace'), c('crt_scale'), c('crt_text')

    # ---- the scale printed on the inside of the tube face
    def graticule(self, mode, band):
        key = (mode, band)
        if key in self._grat:
            return self._grat[key]
        k = self.k
        W, H = self.w * SS, self.h * SS
        im = Image.new('L', (W, H), 0)            # lines, in the dim scale colour
        tim = Image.new('L', (W, H), 0)           # lettering, kept bright enough to read
        d, td = ImageDraw.Draw(im), ImageDraw.Draw(tim)
        lw = max(1, int(0.5 * k))
        line = lambda pts, v=120: d.line([(x * k, y * k) for x, y in pts], fill=v, width=lw)
        text = lambda x, y, t, v=210, a='l', f=None: draw_text(td, x * k, y * k, t, f or self.f_small,
                                                              min(255, int(v * 1.15)),
                                                              tracking=0.3 * k, align=a)

        gx0, gy0, gx1, gy1 = self.GON
        cx, cy, r = (gx0 + gx1) / 2, (gy0 + gy1) / 2, (gx1 - gx0) / 2
        d.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], outline=80, width=lw)
        line([(cx, gy0 + 2), (cx, gy1 - 2)], 120)
        line([(gx0 + 2, cy), (gx1 - 2, cy)], 70)
        dd = r * 0.92 * 0.7071
        line([(cx - dd, cy - dd), (cx + dd, cy + dd)], 70)
        line([(cx + dd, cy - dd), (cx - dd, cy + dd)], 70)
        text(cx - r * 0.78, cy - r * 0.62, 'L', 200, 'm')
        text(cx + r * 0.78, cy - r * 0.62, 'R', 200, 'm')
        text(cx + 3, gy0 + 8, 'M')
        text(gx1 - 7, cy - 2, 'S')
        x0, ya, x1, yb = self.CORR
        line([(x0, yb), (x1, yb)], 130)
        for v in (-1.0, -0.5, 0.0, 0.5, 1.0):
            x = x0 + (v + 1) / 2 * (x1 - x0)
            line([(x, ya + (2 if abs(v) == 0.5 else 0)), (x, yb)], 130)
        for v, t in ((-1.0, '-1'), (0.0, '0'), (1.0, '+1')):
            text(x0 + (v + 1) / 2 * (x1 - x0), 119.5, t, 230, 'm')
        line([(116, 6), (116, 116)], 60)

        px0, py0, px1, py1 = self.PLOT
        div = 4 if mode == 'ECHO' else 6                # 10 dB steps: 40 dB for echoes, 60 otherwise
        for i in range(div + 1):
            y = py0 + (py1 - py0) * i / div
            line([(px0, y), (px1, y)], 45 if 0 < i < div else 90)
        fx = lambda frac: px0 + (px1 - px0) * frac
        title, marks = '', []
        if mode == 'IF' and band == 'FM':
            title = 'IF   before  /  after EQ'
            marks = [(-100, '-100'), (0, '0'), (100, '+100 kHz')]
            pos = lambda v: (v + 120) / 240
        elif mode == 'IF':
            title = 'IF   carrier +/- 24 kHz'
            marks = [(-20, '-20'), (0, '0'), (20, '+20 kHz')]
            pos = lambda v: (v + 24) / 48
        elif mode == 'MPX' and band == 'FM':
            title = 'MPX   dB re 75 kHz'
            marks = [(19, '19'), (38, '38'), (57, '57'), (67, '67'), (92, '92')]
            pos = lambda v: v / 100
        elif mode == 'MPX':
            title = 'AUDIO   dB re 100%'
            marks = [(0, '0'), (5, '5'), (10, '10 kHz')]
            pos = lambda v: v / 12
        elif band == 'FM':
            title = 'ECHO   dB re main path'
            marks = [(0, '0'), (50, '50'), (100, '100'), (150, '150'), (200, '200 us')]
            pos = lambda v: (v + 20) / 270
        else:
            title = 'ECHO   FM only'
            pos = None
        for v, t in marks:
            x = fx(pos(v))
            line([(x, py0), (x, py1)], 70)
            text(x, py1 + 9, t, 190, 'm' if not t.endswith('Hz') and not t.endswith('us') else 'm')
        text(px0, py0 - 3, title, 230, f=self.f_lab)
        g = (down2d(np.asarray(im, np.float32) / 255.0), down2d(np.asarray(tim, np.float32) / 255.0))
        self._grat[key] = g
        return g

    def _plot_xy(self, xf, yf):
        px0, py0, px1, py1 = self.PLOT
        return (px0 + (px1 - px0) * xf) * self.k, (py1 - (py1 - py0) * yf) * self.k

    def frame(self, on, mode, band, gonio=None, corr=None, traces=(), stems=None, frozen=False):
        """traces: [(x 0..1 array, y 0..1 array, brightness 0..1)]; stems: (x, y) for bars."""
        if not frozen:
            self.buf *= SCOPE_DECAY if on else 0.3
            if on:
                k = self.k
                add = Image.new('L', (self.w * SS, self.h * SS), 0)
                d = ImageDraw.Draw(add)
                lw = max(1, int(0.65 * k))
                if gonio is not None and gonio.shape[1] > 1:
                    L, R = gonio[0].astype(np.float64), gonio[1].astype(np.float64)
                    rms = math.sqrt(float(np.mean(L * L + R * R)) / 2)
                    if rms > 1e-4:
                        g = GONIO_FILL / rms
                        m = np.clip((L + R) * 0.7071 * g, -1, 1)
                        sd = np.clip((R - L) * 0.7071 * g, -1, 1)
                        gx0, gy0, gx1, gy1 = self.GON
                        cx, cy, r = (gx0 + gx1) / 2, (gy0 + gy1) / 2, (gx1 - gx0) / 2 - 1
                        pts = np.column_stack(((cx + sd * r) * k, (cy - m * r) * k)).ravel().tolist()
                        d.line(pts, fill=95, width=lw)
                if corr is not None:                  # a pointer riding the -1 .. +1 scale
                    x0, ya, x1, yb = self.CORR
                    x = (x0 + (max(-1.0, min(1.0, corr)) + 1) / 2 * (x1 - x0)) * k
                    d.rectangle([x - 1.0 * k, (ya - 3) * k, x + 1.0 * k, (yb + 0.5) * k], fill=255)
                for xs, ys, b in traces:
                    if len(xs) > 1:
                        px, py = self._plot_xy(np.clip(xs, 0, 1), np.clip(ys, 0, 1))
                        d.line(np.column_stack((px, py)).ravel().tolist(), fill=int(255 * b), width=lw)
                if stems is not None:
                    xs, ys = stems
                    for xf, yf in zip(xs, ys):
                        if yf > 0:
                            xa, ya_ = self._plot_xy(xf, 0)
                            _, yb_ = self._plot_xy(xf, min(1.0, yf))
                            d.line([(xa, ya_), (xa, yb_)], fill=255, width=max(1, int(1.2 * k)))
                self.buf += down2d(np.asarray(add, np.float32) / 255.0) * 1.8
        lvl = 1 - np.exp(-self.buf * 1.6)
        glow = Image.fromarray((lvl * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.8 * self.s))
        glow = np.asarray(glow, np.float32)[..., None] / 255.0
        rgb = CRT_BG + lvl[..., None] * self.trace + glow * self.trace * 0.55
        if on:
            lines, letters = self.graticule(mode, band)
            rgb = rgb + lines[..., None] * self.scale_col + letters[..., None] * self.text_col
        rgb = rgb * self.T + self.A
        return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
