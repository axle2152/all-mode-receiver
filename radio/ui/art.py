"""
radio/ui/art.py - Drawn artwork: segment shapes, brushed plates, keys, the smoked glass.
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .fonts import draw_text, font, text_width
from .theme import (
    GLASS_BEVEL, GLASS_DUST, GLASS_FRESNEL, GLASS_HAZE, GLASS_SHEEN, GLASS_SMOKE, GLASS_STREAK,
    GLASS_TINT, GLASS_UNDER, KEY_BOT, KEY_DN_BOT, KEY_DN_TOP, KEY_HI, KEY_SIDE, KEY_TEXT,
    KEY_TOP, PLATE_RGB, SS)


# ---------------------------------------------------------------------------
# Seven segments
# ---------------------------------------------------------------------------

SEGMENTS = {
    '0': 'abcdef', '1': 'bc', '2': 'abged', '3': 'abgcd', '4': 'fgbc', '5': 'afgcd',
    '6': 'fgedc', '7': 'abc', '8': 'abcdefg', '9': 'abcfg', '-': 'g', ' ': '',     # tail-less 6 and 9
}


def segment_polys(x, y, w, h, t, slant, segs='abcdefg', gap=None):
    """Polygons for the given segments of a 7-segment digit whose upright box
    is (x, y, w, h): square-ended bars, the verticals running into the corners
    (so a 1 is a full-height stroke) with the top, middle and bottom bars set
    between them, then sheared by `slant` into italic."""
    g = t * 0.32 if gap is None else gap
    ym = y + h / 2
    hx0, hx1 = x + t + g, x + w - t - g
    R = lambda x0, y0, x1, y1: [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    shapes = {
        'a': R(hx0, y, hx1, y + t),
        'g': R(hx0, ym - t / 2, hx1, ym + t / 2),
        'd': R(hx0, y + h - t, hx1, y + h),
        'f': R(x, y, x + t, ym - g / 2),
        'b': R(x + w - t, y, x + w, ym - g / 2),
        'e': R(x, ym + g / 2, x + t, y + h),
        'c': R(x + w - t, ym + g / 2, x + w, y + h),
    }
    bottom = y + h
    return [[(px + (bottom - py) * slant, py) for px, py in shapes[s]] for s in segs]


def num_chars(v, width=4, dec=1):
    """A reading as display characters and whether its decimal point is lit.
    None gives dashes, the way a meter shows 'no reading'."""
    if v is None or not math.isfinite(v):
        return ('-',) * width, False
    top = 10 ** (width - dec) - 10 ** -dec
    low = -(10 ** (width - dec - 1) - 10 ** -dec)
    v = min(max(v, low), top)
    s = f'{v:.{dec}f}'.replace('.', '')
    return tuple(s.rjust(width)[-width:]), dec > 0


# ---------------------------------------------------------------------------
# Brushed panel and keys (rendered once with PIL)
# ---------------------------------------------------------------------------

def _rrect(d, box, r, fill):
    d.rounded_rectangle([round(v) for v in box], radius=max(0, round(r)), fill=fill)


def brushed_plate(w, h, seed=11):
    rng = np.random.default_rng(seed)
    cols = max(8, w // 70)
    streak = Image.fromarray(rng.normal(0, 1, (h, cols)).astype(np.float32))
    streak = np.asarray(streak.resize((w, h), Image.BICUBIC), np.float32)
    fine = rng.normal(0, 1, (h, w)).astype(np.float32)
    row = rng.normal(0, 1, (h, 1)).astype(np.float32)
    lum = streak * 2.4 + fine * 1.5 + row * 1.6 + np.linspace(7, -9, h, dtype=np.float32)[:, None]
    rgb = np.asarray(PLATE_RGB, np.float32)[None, None, :] + lum[..., None]
    e = max(1, round(h / 84))
    rgb[:e] += 30
    rgb[e:2 * e] += 12
    rgb[-3 * e:-2 * e] -= 28
    rgb[-2 * e:-e] -= 52
    rgb[-e:] -= 75
    rgb[:, :e] -= 18
    rgb[:, -e:] -= 26
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


def render_key(w, h, s, legend, pressed, icon=None, size=13.0):
    """A charcoal key cap, up or pressed in. Returns an RGBA image (w, h at scale s)."""
    k = s * SS
    W, H = round(w * k), round(h * k)
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = 2.6 * k
    side = 3.0 * k
    _rrect(d, (0, 0, W - 1, H - 1), r, KEY_SIDE + (255,))
    top = round(side * 0.7) if pressed else 0
    bot = H - 1 if pressed else H - 1 - round(side)
    c0, c1 = (KEY_DN_TOP, KEY_DN_BOT) if pressed else (KEY_TOP, KEY_BOT)
    face_h = bot - top + 1
    grad = np.linspace(0, 1, face_h, dtype=np.float32)[:, None, None]
    face = (np.asarray(c0, np.float32) * (1 - grad) + np.asarray(c1, np.float32) * grad)
    face = np.repeat(face, W, axis=1).astype(np.uint8)
    face_im = Image.fromarray(face)
    mask = Image.new('L', (W, face_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, face_h - 1], radius=round(r), fill=255)
    im.paste(face_im, (0, top), mask)
    hi = KEY_HI if not pressed else tuple(int(v * 0.8) for v in KEY_HI)
    d.line([(r, top + k * 0.6), (W - r, top + k * 0.6)], fill=hi + (255,), width=max(1, round(k)))

    f = font('key', size * k)
    cy = top + face_h / 2
    tw = text_width(legend, f) if legend else 0
    tri = 7 * k if icon else 0
    gap = 6 * k if (icon and legend) else 0
    total = tw + tri + gap
    x = W / 2 - total / 2
    if icon == 'left':
        d.polygon([(x, cy), (x + tri, cy - tri * 0.62), (x + tri, cy + tri * 0.62)], fill=KEY_TEXT)
        x += tri + gap
    if legend:
        draw_text(d, x, cy, legend, f, KEY_TEXT, vanchor='m')
        x += tw + gap
    if icon == 'right':
        d.polygon([(x + tri, cy), (x, cy - tri * 0.62), (x, cy + tri * 0.62)], fill=KEY_TEXT)
    return im.resize((round(w * s), round(h * s)), Image.LANCZOS)


def down2d(a, f=SS):
    """Box-filter an array (H, W[, C]) down by f."""
    h, w = a.shape[0] // f, a.shape[1] // f
    a = a[:h * f, :w * f]
    if a.ndim == 2:
        return a.reshape(h, f, w, f).mean(axis=(1, 3))
    return a.reshape(h, f, w, f, -1).mean(axis=(1, 3))


# ---------------------------------------------------------------------------
# Glass: a sheet of smoked glass in front of a display
# ---------------------------------------------------------------------------

def glass_layers(W, H, k, seed=3, curved=False):
    """Transmission T (H, W) and reflected light A (H, W, 3) for the glass in
    front of a display, at drawing resolution (k pixels per base pixel).
    T: the smoke tint, and the bezel lip shading the glass's edges.
    A: a broad sheen and a narrow streak (a window across the room), Fresnel
    brightening toward the top, light caught along the top edge of the pane,
    a little dust and a few faint hairline scratches."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    xn, yn = xx / W, yy / H
    T = np.full((H, W), GLASS_SMOKE, np.float32)
    T *= 1 - 0.55 * np.exp(-yy / (6 * k))
    T *= 1 - 0.30 * np.exp(-xx / (5 * k))
    T *= 1 - 0.30 * np.exp(-(W - 1 - xx) / (5 * k))
    T *= 1 - 0.22 * np.exp(-(H - 1 - yy) / (4 * k))
    bow = 0.0
    if curved:                                        # a CRT face bulges toward you
        r2 = ((xn - 0.5) / 0.5) ** 2 + ((yn - 0.5) / 0.5) ** 2
        T *= np.clip(1 - 0.22 * r2 ** 1.6, 0.3, 1)
        bow = 0.10 * ((xn - 0.5) ** 2) - 0.06 * ((yn - 0.5) ** 2)

    u = (xx + yy * 0.55) / W + bow
    edge = 0.24                                       # the sheen: a sharp edge, then a long fade
    lead = np.exp(-((u - edge) / (0.004 + 0.6 / W)) ** 2)
    tail = np.exp(-np.maximum(u - edge, 0) / 0.10)
    A = GLASS_SHEEN * np.where(u < edge, lead, tail) * (1 - 0.45 * yn)
    A += GLASS_STREAK * np.exp(-((u - 0.43) / 0.012) ** 2) * (1 - 0.35 * yn)
    A += GLASS_FRESNEL * np.exp(-yn / 0.12) * (0.65 + 0.35 * (1 - xn))
    A += 0.4 * GLASS_SHEEN * np.exp(-(((xn - 0.82) / 0.17) ** 2 + ((yn - 0.22) / 0.30) ** 2))
    A += GLASS_BEVEL * np.exp(-((yy - 1.5 * k) / (0.5 * k)) ** 2) * (0.5 + 0.5 * np.sin(np.pi * xn))
    A += 0.35 * GLASS_BEVEL * np.exp(-((yy - 3.2 * k) / (1.1 * k)) ** 2) * (0.5 + 0.5 * np.sin(np.pi * xn))
    A += GLASS_UNDER * np.exp(-((H - 1 - yy - 1.2 * k) / (0.5 * k)) ** 2) * (0.6 + 0.4 * np.sin(np.pi * xn))
    A += GLASS_HAZE

    speck = Image.new('L', (W, H), 0)
    sd = ImageDraw.Draw(speck)
    for _ in range(int(W * H * 1.2e-5) + 3):
        x, y = rng.uniform(0, W), rng.uniform(0, H)
        r = rng.uniform(0.3, 0.9) * k
        sd.ellipse([x - r, y - r, x + r, y + r], fill=int(rng.uniform(90, 230)))
    for _ in range(5):
        x0, y0 = rng.uniform(0, W), rng.uniform(0, H)
        ang, ln = rng.uniform(-0.5, 0.5), rng.uniform(0.08, 0.3) * W
        sd.line([(x0, y0), (x0 + ln * math.cos(ang), y0 + ln * math.sin(ang))],
                fill=int(rng.uniform(40, 70)), width=max(1, int(0.5 * k)))
    speck = speck.filter(ImageFilter.GaussianBlur(0.5 * k))
    A += np.asarray(speck, np.float32) * GLASS_DUST

    if curved:                                        # rounded corners of the tube face
        m = Image.new('L', (W, H), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, W - 1, H - 1], radius=int(11 * k), fill=255)
        m = np.asarray(m.filter(ImageFilter.GaussianBlur(1.2 * k)), np.float32) / 255.0
        T *= m
        A *= m
    return T, A[..., None] * GLASS_TINT
