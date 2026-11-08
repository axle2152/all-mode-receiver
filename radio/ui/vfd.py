"""
radio/ui/vfd.py - The vacuum fluorescent displays: the tuner and the monitor unit.
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .art import SEGMENTS, down2d, glass_layers, segment_polys
from .constants import (
    BLOOM_A1, BLOOM_A2, BLOOM_A3, BLOOM_R1, BLOOM_R2, BLOOM_R3, GLOW_DEFAULT, SIG_STEPS,
    _ASSET_DIR)
from .fonts import draw_text, font
from .theme import (
    ALWAYS_LIT, CH_H, CH_LABEL_X, CH_T, CH_TOP, CH_W, CH_X, COLOR_DEFAULTS, DIG_H, DIG_SLANT,
    DIG_T, DIG_TOP, DIG_W, DIG_X, DOT_BLUR, DOT_FIELDS, DOT_PITCH, DOT_SIZE, DOT_TOP, DP_X,
    GLASS_EDGE, GLASS_MID, HALF_X, KHZ_Y, LEGENDS, LEG_Y, MB_ROWS, MB_SCALE, MB_SEGS, MB_X0,
    MB_X1, MHZ_Y, MON_STATS, NUMS, NUM_H, NUM_PITCH, NUM_T, NUM_W, PHOSPHOR, SCALES, SIG_BOT,
    SIG_H0, SIG_H1, SIG_LABEL_X, SIG_LABEL_Y, SIG_PITCH, SIG_W, SIG_X, SS, STAT_Y, UNIT_X,
    VFD_BOX, ghost_of, rgb_of)


# 5x7 dot-matrix characters (the Delco's table)
FONT_5X7 = {
    ' ': '00 00 00 00 00 00 00', '!': '04 04 04 04 00 00 04', '"': '0A 0A 0A 00 00 00 00',
    '#': '0A 0A 1F 0A 1F 0A 0A', '$': '04 0F 14 0E 05 1E 04', '%': '18 19 02 04 08 13 03',
    '&': '0C 12 14 08 15 12 0D', "'": '0C 04 08 00 00 00 00', '(': '02 04 08 08 08 04 02',
    ')': '08 04 02 02 02 04 08', '*': '00 04 15 0E 15 04 00', '+': '00 04 04 1F 04 04 00',
    ',': '00 00 00 00 0C 04 08', '-': '00 00 00 1F 00 00 00', '.': '00 00 00 00 00 0C 0C',
    '/': '00 01 02 04 08 10 00', '0': '0E 11 13 15 19 11 0E', '1': '04 0C 04 04 04 04 0E',
    '2': '0E 11 01 02 04 08 1F', '3': '1F 02 04 02 01 11 0E', '4': '02 06 0A 12 1F 02 02',
    '5': '1F 10 1E 01 01 11 0E', '6': '06 08 10 1E 11 11 0E', '7': '1F 01 02 04 08 08 08',
    '8': '0E 11 11 0E 11 11 0E', '9': '0E 11 11 0F 01 02 0C', ':': '00 0C 0C 00 0C 0C 00',
    ';': '00 0C 0C 00 0C 04 08', '<': '02 04 08 10 08 04 02', '=': '00 00 1F 00 1F 00 00',
    '>': '08 04 02 01 02 04 08', '?': '0E 11 01 02 04 00 04', '@': '0E 11 01 0D 15 15 0E',
    'A': '0E 11 11 11 1F 11 11', 'B': '1E 11 11 1E 11 11 1E', 'C': '0E 11 10 10 10 11 0E',
    'D': '1C 12 11 11 11 12 1C', 'E': '1F 10 10 1E 10 10 1F', 'F': '1F 10 10 1E 10 10 10',
    'G': '0E 11 10 17 11 11 0F', 'H': '11 11 11 1F 11 11 11', 'I': '0E 04 04 04 04 04 0E',
    'J': '07 02 02 02 02 12 0C', 'K': '11 12 14 18 14 12 11', 'L': '10 10 10 10 10 10 1F',
    'M': '11 1B 15 15 11 11 11', 'N': '11 11 19 15 13 11 11', 'O': '0E 11 11 11 11 11 0E',
    'P': '1E 11 11 1E 10 10 10', 'Q': '0E 11 11 11 15 12 0D', 'R': '1E 11 11 1E 14 12 11',
    'S': '0F 10 10 0E 01 01 1E', 'T': '1F 04 04 04 04 04 04', 'U': '11 11 11 11 11 11 0E',
    'V': '11 11 11 11 11 0A 04', 'W': '11 11 11 15 15 15 0A', 'X': '11 11 0A 04 0A 11 11',
    'Y': '11 11 11 0A 04 04 04', 'Z': '1F 01 02 04 08 10 1F', '[': '0E 08 08 08 08 08 0E',
    '\\': '00 10 08 04 02 01 00', ']': '0E 02 02 02 02 02 0E', '^': '04 0A 11 00 00 00 00',
    '_': '00 00 00 00 00 00 1F', '`': '08 04 02 00 00 00 00', 'a': '00 00 0E 01 0F 11 0F',
    'b': '10 10 16 19 11 11 1E', 'c': '00 00 0E 10 10 11 0E', 'd': '01 01 0D 13 11 11 0F',
    'e': '00 00 0E 11 1F 10 0E', 'f': '06 09 08 1C 08 08 08', 'g': '00 0F 11 11 0F 01 0E',
    'h': '10 10 16 19 11 11 11', 'i': '04 00 0C 04 04 04 0E', 'j': '02 00 06 02 02 12 0C',
    'k': '10 10 12 14 18 14 12', 'l': '0C 04 04 04 04 04 0E', 'm': '00 00 1A 15 15 11 11',
    'n': '00 00 16 19 11 11 11', 'o': '00 00 0E 11 11 11 0E', 'p': '00 00 1E 11 1E 10 10',
    'q': '00 00 0D 13 0F 01 01', 'r': '00 00 16 19 10 10 10', 's': '00 00 0E 10 0E 01 1E',
    't': '08 08 1C 08 08 09 06', 'u': '00 00 11 11 11 13 0D', 'v': '00 00 11 11 11 0A 04',
    'w': '00 00 11 11 15 15 0A', 'x': '00 00 11 0A 04 0A 11', 'y': '00 00 11 11 0F 01 0E',
    'z': '00 00 1F 02 04 08 1F', '{': '02 04 04 08 04 04 02', '|': '04 04 04 04 04 04 04',
    '}': '08 04 04 02 04 04 08', '~': '00 00 08 15 02 00 00',
    '\u00b0': '0C 12 12 0C 00 00 00',          # degree sign (temperatures in station text)
}
_GLYPHS = {ch: [int(v, 16) for v in rows.split()] for ch, rows in FONT_5X7.items()}


def _plain(ch):
    """The font has no accents: show \u00c9 as E, \u00f1 as n (the Advanced window shows the real text)."""
    import unicodedata
    return unicodedata.normalize('NFKD', ch)[:1] or ' '


# ---------------------------------------------------------------------------
# VFD engine. Every element (segment, legend, bar) is drawn once at SS
# resolution; its lit look (phosphor through the grid mesh, plus glow, minus
# the dark glass it covers, all seen through the front glass) is kept as a
# small cropped array. Glow and downsampling are linear, so a frame is the
# unlit tube plus the sum of the lit elements' arrays.
# ---------------------------------------------------------------------------

class VfdBase:
    """The display tube. Each element's look is cached in colour-free parts at
    start-up (glow and phosphor shape seen through the glass, and the patch of
    tube it covers), so a frame is the unlit tube plus the lit elements' arrays,
    and a colour change only rescales the cache (milliseconds, no re-blurring):

        lit element = colour * shape  -  tube under it  -  its own ghost tint
        unlit tube  = tube + sum over colour groups of ghost tint * group mask
    """
    BOX = VFD_BOX
    SEED = 3

    def __init__(self, s, colors=None):
        """colors: {group: '#rrggbb'} overrides of COLOR_DEFAULTS."""
        self.s = s
        self.k = k = s * SS
        x0, y0, x1, y1 = self.BOX
        self.bw, self.bh = x1 - x0, y1 - y0
        self.w, self.h = round(self.bw * s), round(self.bh * s)
        self.W, self.H = self.w * SS, self.h * SS
        self.blur = 3.2 * k
        self._setup()

        yy = np.linspace(-1, 1, self.H, dtype=np.float32)[:, None]
        xx = np.linspace(-1, 1, self.W, dtype=np.float32)[None, :]
        v = np.clip(1 - 0.55 * yy ** 2 - 0.25 * xx ** 2, 0, 1)[..., None]
        self.tube = GLASS_EDGE + (GLASS_MID - GLASS_EDGE) * v

        X, Y = np.meshgrid(np.arange(self.W, dtype=np.float32), np.arange(self.H, dtype=np.float32))
        p, lw = 3.2 * k, 0.55 * k
        mesh = np.ones((self.H, self.W), np.float32)
        mesh[((X + Y) % p) < lw] = 0.74
        mesh[((X - Y) % p) < lw] = 0.74
        for fy in (0.34, 0.72):                         # filament wires
            yc = int(self.H * fy)
            mesh[yc:yc + max(1, int(0.7 * k))] *= 0.62
        self.mesh = mesh
        T, A = glass_layers(self.W, self.H, k, seed=self.SEED)
        self.T = T

        masks = {}                                      # unlit segments, per colour group
        for key in self._all_keys(ghost=True):
            g = self._group(key)
            if g not in masks:
                im = Image.new('L', (self.W, self.H), 0)
                masks[g] = (im, ImageDraw.Draw(im))
            im, gd = masks[g]
            self._draw_element(gd, im, key)
        self._ghost_layers = {g: down2d(np.asarray(im, np.float32) / 255.0 * T) for g, (im, _) in masks.items()}
        self._base0 = down2d(self.tube * T[..., None] + A)

        self.glow = GLOW_DEFAULT
        self._raw = {}
        self._scratch = Image.new('L', (self.W, self.H), 0)
        for key in self._all_keys():                    # the slow part, once
            self._raw_for(key)
        self.set_colors(colors)

    def set_colors(self, colors):
        """Apply {group: '#rrggbb'} overrides: cheap, everything is cached."""
        self.overrides = dict(colors or {})
        self._col = {g: rgb_of(c) for g, c in COLOR_DEFAULTS.items() if c}
        self._col.update({g: rgb_of(c) for g, c in self.overrides.items() if c})
        base = self._base0.copy()
        for g, layer in self._ghost_layers.items():
            base += layer[..., None] * ghost_of(self._col.get(g, PHOSPHOR))
        self.base = base
        self._delta = {}
        for key, (y, x, shape, under, cover) in self._raw.items():
            if shape.size == 0:
                self._delta[key] = (y, x, np.zeros((0, 0, 3), np.float32))
                continue
            ghost = ghost_of(self._col.get(self._group(key), PHOSPHOR))
            self._delta[key] = (y, x, (shape[..., None] * self._colour(key) - under
                                       - cover[..., None] * ghost).astype(np.float32))

    # subclasses provide these
    def _setup(self):
        pass

    def _all_keys(self, ghost=False):
        return []

    def _lit_keys(self, st):
        return []

    def _draw_element(self, d, img, key):
        pass

    def _group(self, key):
        return None

    def _colour(self, key):
        return self._col.get(self._group(key), PHOSPHOR)

    def _blur_for(self, key):
        return self.blur

    def _raw_for(self, key):
        """(y, x, shape, tube under it, coverage), all through the glass, at screen resolution."""
        got = self._raw.get(key)
        if got is not None:
            return got
        m = self._scratch
        m.paste(0, (0, 0, self.W, self.H))
        self._draw_element(ImageDraw.Draw(m), m, key)
        box = m.getbbox()
        if box is None:
            got = (0, 0, np.zeros((0, 0), np.float32), np.zeros((0, 0, 3), np.float32), np.zeros((0, 0), np.float32))
            self._raw[key] = got
            return got
        blur = self._blur_for(key)
        pad = int(np.ceil(3 * blur))
        x0 = max(0, (box[0] - pad) // SS * SS)
        y0 = max(0, (box[1] - pad) // SS * SS)
        x1 = min(self.W, -(-(box[2] + pad) // SS) * SS)
        y1 = min(self.H, -(-(box[3] + pad) // SS) * SS)
        crop = m.crop((x0, y0, x1, y1))
        L = np.asarray(crop, np.float32) / 255.0
        glow = np.asarray(crop.filter(ImageFilter.GaussianBlur(blur)), np.float32) / 255.0
        T = self.T[y0:y1, x0:x1]
        shape = down2d((glow * 0.55 + L * self.mesh[y0:y1, x0:x1]) * T)
        under = down2d(self.tube[y0:y1, x0:x1] * (L * T)[..., None])
        cover = down2d(L * T)
        got = (y0 // SS, x0 // SS, shape.astype(np.float32), under.astype(np.float32), cover.astype(np.float32))
        self._raw[key] = got
        return got

    def render(self, st):
        """st: None (off: the unlit tube behind its glass) or the unit's state dict."""
        out = self.base.copy()
        if st is not None:
            for key in self._lit_keys(st):
                y, x, a = self._delta[key]
                out[y:y + a.shape[0], x:x + a.shape[1]] += a
            if self.glow > 0:                           # the light, scattered in the glass
                light = Image.fromarray(np.clip(out - self.base, 0, 255).astype(np.uint8))
                halo = np.asarray(light.filter(ImageFilter.GaussianBlur(BLOOM_R1 * self.s)), np.float32)
                half = light.resize((max(1, self.w // 2), max(1, self.h // 2)), Image.BILINEAR)
                wide = (BLOOM_A2 * np.asarray(half.filter(ImageFilter.GaussianBlur(BLOOM_R2 * self.s / 2)), np.float32)
                        + BLOOM_A3 * np.asarray(half.filter(ImageFilter.GaussianBlur(BLOOM_R3 * self.s / 2)), np.float32))
                wide = np.asarray(Image.fromarray(np.clip(wide, 0, 255).astype(np.uint8)).resize(
                    (self.w, self.h), Image.BILINEAR), np.float32)
                out += self.glow * (BLOOM_A1 * halo + wide)
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


class TunerVfd(VfdBase):
    DIGIT_SLOTS = {('big', i): (x, DIG_TOP, DIG_W, DIG_H, DIG_T) for i, x in enumerate(DIG_X)}
    DIGIT_SLOTS.update({('ch', i): (x, CH_TOP, CH_W, CH_H, CH_T) for i, x in enumerate(CH_X)})

    def _setup(self):
        k = self.k
        self.leg_font = font('vfd', 9.5 * k)
        self.unit_font = font('vfd', 11.5 * k)
        self.digit_masks = self._load_digit_images()

    def _load_digit_images(self):
        folder = os.path.join(_ASSET_DIR, 'vfd')
        names = [os.path.join(folder, f'vfd_{n}.png') for n in range(10)]
        if not all(os.path.isfile(p) for p in names):
            return None
        out = {str(n): self._mask_from(p) for n, p in enumerate(names)}
        g = os.path.join(folder, 'vfd_ghost.png')
        out['ghost'] = self._mask_from(g) if os.path.isfile(g) else out['8']
        return out

    @staticmethod
    def _mask_from(path):
        im = Image.open(path)
        if im.mode in ('RGBA', 'LA') or 'transparency' in im.info:
            return im.convert('RGBA').split()[3]
        return im.convert('L')

    def _digit_keys(self, slot, ch, ghost=False):
        avail = 'abcdeg' if slot == ('ch', 0) else 'abcdefg'
        if self.digit_masks and avail == 'abcdefg':
            c = 'ghost' if ghost else ch
            return [('img',) + slot + (c,)] if c in self.digit_masks else []
        return [('seg',) + slot + (sg,) for sg in SEGMENTS.get(ch, '') if sg in avail]

    def _all_keys(self, ghost=False):
        keys = [('leg', n) for n, _ in LEGENDS] + [('leg', n) for n in ALWAYS_LIT]
        keys += [('unit', 'kHz'), ('unit', 'MHz'), ('half',), ('dp',)]
        keys += [('bar', i) for i in range(SIG_STEPS)]
        for slot in self.DIGIT_SLOTS:
            keys += self._digit_keys(slot, '8', ghost)
        keys += [('dot', f, c, r, k) for f, _, n in DOT_FIELDS for c in range(n) for r in range(7) for k in range(5)]
        return keys

    def _blur_for(self, key):
        return DOT_BLUR * self.k if key[0] == 'dot' else self.blur

    def _group(self, key):
        kind = key[0]
        if kind == 'leg':
            return {'CH': 'ch_label', 'SIGNAL': 'sig_label'}.get(key[1], 'leg_' + key[1])
        if kind == 'unit':
            return 'units'
        if kind in ('half', 'dp'):
            return 'freq'
        if kind in ('seg', 'img'):
            return 'freq' if key[1] == 'big' else 'ch_digits'
        if kind == 'bar':
            return 'signal'
        if kind == 'dot':
            return 'rds_' + key[1]
        return None

    @staticmethod
    def _dot_keys(field, text):
        keys = []
        for c, ch in enumerate(text):
            rows = _GLYPHS.get(ch) or _GLYPHS.get(_plain(ch), _GLYPHS[' '])
            for r in range(7):
                bits = rows[r]
                for k in range(5):
                    if (bits >> (4 - k)) & 1:
                        keys.append(('dot', field, c, r, k))
        return keys

    def _lit_keys(self, st):
        if st.get('all'):
            return self._all_keys()
        keys = [('leg', n) for n in st['legends']]
        if st['fixed']:
            keys += [('leg', n) for n in ALWAYS_LIT]
        if st['unit']:
            keys.append(('unit', st['unit']))
        half, digits = st['digits']
        if half == '1':
            keys.append(('half',))
        for i, c in enumerate(digits):
            keys += self._digit_keys(('big', i), c)
        if st['dp']:
            keys.append(('dp',))
        keys += [('bar', i) for i in range(st['sig'])]
        for i, c in enumerate(st['ch']):
            keys += self._digit_keys(('ch', i), c)
        for (field, _, n), text in zip(DOT_FIELDS, st.get('rds', ('', '', ''))):
            keys += self._dot_keys(field, text[:n])
        return keys

    def _draw_element(self, d, img, key):
        k = self.k
        kind = key[0]
        if kind == 'dot':
            _, field, c, r, col = key
            fx = dict((f, x) for f, x, _ in DOT_FIELDS)[field]
            x = fx + (c * 6 + col) * DOT_PITCH
            y = DOT_TOP + r * DOT_PITCH
            d.rectangle([x * k, y * k, (x + DOT_SIZE) * k, (y + DOT_SIZE) * k], fill=255)
            return
        if kind == 'leg':
            name = key[1]
            if name == 'CH':
                x, y = CH_LABEL_X, DIG_TOP + DIG_H
            elif name == 'SIGNAL':
                x, y = SIG_LABEL_X, SIG_LABEL_Y
            else:
                x, y = dict(LEGENDS)[name], LEG_Y
            draw_text(d, x * k, y * k, name, self.leg_font, 255, tracking=0.6 * k)
        elif kind == 'unit':
            y = KHZ_Y if key[1] == 'kHz' else MHZ_Y
            draw_text(d, UNIT_X * k, y * k, key[1], self.unit_font, 255, tracking=0.4 * k)
        elif kind == 'half':
            for poly in segment_polys(HALF_X * k, DIG_TOP * k, 12 * k, DIG_H * k, DIG_T * k,
                                      DIG_SLANT, 'bc'):
                d.polygon(poly, fill=255)
        elif kind == 'dp':
            sz = 5.0
            d.rectangle([DP_X * k, (DIG_TOP + DIG_H - sz) * k, (DP_X + sz) * k,
                         (DIG_TOP + DIG_H) * k], fill=255)
        elif kind == 'bar':
            i = key[1]
            bh = SIG_H0 + (SIG_H1 - SIG_H0) * i / (SIG_STEPS - 1)
            x = SIG_X + i * SIG_PITCH
            d.rectangle([x * k, (SIG_BOT - bh) * k, (x + SIG_W) * k, SIG_BOT * k], fill=255)
        elif kind == 'seg':
            x, y, w, h, t = self.DIGIT_SLOTS[(key[1], key[2])]
            for poly in segment_polys(x * k, y * k, w * k, h * k, t * k, DIG_SLANT, key[3]):
                d.polygon(poly, fill=255)
        elif kind == 'img':
            x, y, w, h, _ = self.DIGIT_SLOTS[(key[1], key[2])]
            m = self.digit_masks[key[3]]
            size = (round((w + h * DIG_SLANT) * k), round(h * k))
            img.paste(255, (round(x * k), round(y * k)), m.resize(size, Image.LANCZOS))


class MonitorVfd(VfdBase):
    """Bar meters (L, R, modulation, momentary loudness) and six readouts."""
    SEED = 8

    def _setup(self):
        k = self.k
        self.lab_font = font('vfd', 8 * k)
        self.small_font = font('vfd', 7 * k)
        self.pitch = (MB_X1 - MB_X0) / MB_SEGS

    @staticmethod
    def seg_index(scale, v):
        """How many segments a value lights on a scale."""
        sc = SCALES[scale]
        if v is None or not math.isfinite(v):
            return 0
        return int(max(0, min(MB_SEGS, math.floor((v - sc['lo']) / sc['step'] + 1e-9))))

    def _all_keys(self, ghost=False):
        keys = [('bar', r, i) for r, _, _ in MB_ROWS for i in range(MB_SEGS)]
        if not ghost:                                   # hold markers sit on the bar segments
            keys += [('hold', r, i) for r, _, _ in MB_ROWS for i in range(MB_SEGS)]
        keys += [('rowlab', r) for r, _, _ in MB_ROWS]
        keys += [('scale', sc, v) for sc, d in SCALES.items() for v in d['marks']]
        keys += [('stat', n) for n, _ in MON_STATS]
        for name, _, _, _, units in NUMS:
            keys.append(('nlab', name))
            keys += [('nunit', name, u) for u in units]
            keys += [('nseg', name, p, sg) for p in range(4) for sg in 'abcdefg']
            keys.append(('ndp', name))
        return keys

    def _lit_keys(self, st):
        if st.get('all'):
            return self._all_keys()
        keys = [('rowlab', r) for r, _, _ in MB_ROWS]
        keys += [('scale', sc, v) for sc, d in SCALES.items() for v in d['marks']]
        keys += [('nlab', n) for n, _, _, _, _ in NUMS]
        for row, (n, hold) in st['bars'].items():
            keys += [('bar', row, i) for i in range(n)]
            if hold is not None and n <= hold < MB_SEGS:
                keys.append(('hold', row, hold))
        keys += [('stat', n) for n in st['stats']]
        for name, (chars, dp, unit) in st['nums'].items():
            for p, c in enumerate(chars):
                keys += [('nseg', name, p, sg) for sg in SEGMENTS.get(c, '')]
            if dp:
                keys.append(('ndp', name))
            if unit:
                keys.append(('nunit', name, unit))
        return keys

    def _group(self, key):
        kind = key[0]
        if kind in ('bar', 'hold'):
            row, i = key[1], key[2]
            sc = SCALES[MB_SCALE[row]]
            hot = sc['hot'] is not None and sc['lo'] + sc['step'] * (i + 1) > sc['hot'] + 1e-9
            return {'L': 'lr', 'R': 'lr', 'MOD': 'mod', 'LUFS': 'lufs'}[row] + ('_hot' if hot else '')
        return {'rowlab': 'rowlab', 'scale': 'scale', 'nlab': 'num_labels', 'nunit': 'num_units'}.get(
            kind, 'stat_' + key[1] if kind == 'stat' else 'num_' + key[1] if kind in ('nseg', 'ndp') else None)

    def _colour(self, key):
        if key[0] == 'hold' and 'hold' in self._col:    # its own colour, else the bar's
            return self._col['hold']
        return self._col.get(self._group(key), PHOSPHOR)

    def _draw_element(self, d, img, key):
        k = self.k
        kind = key[0]
        rows = {r: (y0, y1) for r, y0, y1 in MB_ROWS}
        if kind in ('bar', 'hold'):
            y0, y1 = rows[key[1]]
            x = MB_X0 + key[2] * self.pitch
            d.rectangle([x * k, y0 * k, (x + self.pitch - 2.4) * k, y1 * k], fill=255)
        elif kind == 'rowlab':
            y0, y1 = rows[key[1]]
            draw_text(d, 4 * k, y1 * k, key[1], self.lab_font, 255, tracking=0.4 * k)
        elif kind == 'scale':
            sc = SCALES[key[1]]
            x = MB_X0 + (key[2] - sc['lo']) / sc['step'] * self.pitch - 1.2
            txt = f'{key[2]:+d}' if key[1] == 'dB' and key[2] > 0 else str(key[2])
            draw_text(d, x * k, sc['y'] * k, txt, self.small_font, 255, align='m')
        elif kind == 'stat':
            x = dict(MON_STATS)[key[1]]
            draw_text(d, x * k, STAT_Y * k, key[1], self.lab_font, 255, tracking=0.5 * k)
        else:
            spec = {n: (lab, x, top, units) for n, lab, x, top, units in NUMS}
            lab, x, top, units = spec[key[1]]
            if kind == 'nlab':
                draw_text(d, x * k, (top - 4) * k, lab, self.small_font, 255, tracking=0.5 * k)
            elif kind == 'nseg':
                dx = x + key[2] * NUM_PITCH
                for poly in segment_polys(dx * k, top * k, NUM_W * k, NUM_H * k, NUM_T * k,
                                          DIG_SLANT, key[3]):
                    d.polygon(poly, fill=255)
            elif kind == 'ndp':
                dx = x + 2 * NUM_PITCH + NUM_W + 0.6
                d.rectangle([dx * k, (top + NUM_H - 2.6) * k, (dx + 2.6) * k, (top + NUM_H) * k], fill=255)
            elif kind == 'nunit':
                ux = x + 4 * NUM_PITCH + 4
                if len(units) == 1:
                    y = top + NUM_H
                else:
                    y = top + 7 if key[2] == units[0] else top + NUM_H
                draw_text(d, ux * k, y * k, key[2], self.small_font, 255, tracking=0.3 * k)
