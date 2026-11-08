"""
radio/ui/fonts.py - Finding fonts on Windows and Linux, and drawing text with them.
"""
import os

from PIL import ImageFont


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_FONT_DIRS = [os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'),
              os.path.expanduser('~/AppData/Local/Microsoft/Windows/Fonts'),
              '/usr/share/fonts', '/usr/local/share/fonts', os.path.expanduser('~/.fonts'),
              os.path.expanduser('~/.local/share/fonts'),
              '/Library/Fonts', '/System/Library/Fonts']
_font_index = None

FONT_ROLES = {                           # Windows first, then Linux (DejaVu, Liberation)
    'label':  [('bahnschrift.ttf', 'Regular'), ('segoeui.ttf', None), ('arial.ttf', None),
               ('LiberationSans-Regular.ttf', None), ('DejaVuSans.ttf', None)],
    'badge':  [('bahnschrift.ttf', 'SemiBold'), ('segoeuib.ttf', None), ('arialbd.ttf', None),
               ('LiberationSans-Bold.ttf', None), ('DejaVuSans-Bold.ttf', None)],
    'key':    [('bahnschrift.ttf', 'SemiBold'), ('segoeuisb.ttf', None), ('arialbd.ttf', None),
               ('LiberationSans-Bold.ttf', None), ('DejaVuSans-Bold.ttf', None)],
    'vfd':    [('bahnschrift.ttf', 'SemiBold SemiCondensed'), ('ARIALNB.TTF', None),
               ('arialbd.ttf', None), ('LiberationSansNarrow-Bold.ttf', None),
               ('DejaVuSansCondensed-Bold.ttf', None), ('LiberationSans-Bold.ttf', None),
               ('DejaVuSans-Bold.ttf', None)],
}
_font_cache = {}

# Nimbus Sans (URW's Helvetica) for every printed label, key cap and the
# nameplate. Found by each font file's own family and style names, since the
# files go by NimbusSanL-Regu, n019003l.pfb, NimbusSans-Regular.otf and so on.
# The displays (VFD, dot matrix, CRT) keep their own lettering.
NIMBUS_ROLES = {'label': (False, False), 'badge': (True, False), 'key': (True, False)}
_nimbus = None
UI_FAMILY = 'Helvetica'                  # Tk family for the Advanced window; set at start-up
FACE_FONT = ('Eurostile', 9, 'bold')        # Tk font for the windows' lettering; set at start-up


def _nimbus_faces():
    """Nimbus Sans faces on this machine: {(bold, italic): path}."""
    global _nimbus
    if _nimbus is not None:
        return _nimbus
    _find_font('-')                      # builds the index
    faces = {}
    for name, path in _font_index.items():
        if not (('nimbus' in name or name.startswith('n019')) and
                name.endswith(('.ttf', '.otf', '.pfb', '.t1'))):
            continue
        try:
            fam, style = ImageFont.truetype(path, 12).getname()
        except Exception:
            continue
        fam, style = (fam or '').lower(), (style or '').lower()
        if 'nimbus' not in fam or not ('sans' in fam or 'sanl' in fam):
            continue
        if any(w in fam or w in style for w in ('narrow', 'cond')):
            continue
        key = ('bold' in style, 'italic' in style or 'oblique' in style)
        if key not in faces or faces[key].endswith(('.pfb', '.t1')):   # prefer TrueType / OpenType
            faces[key] = path
    _nimbus = faces
    return faces


def use_nimbus_in_tk(root):
    """Point the Advanced window's lettering at Nimbus Sans if Tk can see it."""
    global UI_FAMILY, FACE_FONT
    import tkinter.font as tkfont
    fams = set(tkfont.families(root))
    pick = next((f for f in ('Nimbus Sans L', 'Nimbus Sans', 'NimbusSans') if f in fams), None)
    if pick is None:
        pick = next((f for f in sorted(fams) if 'nimbus' in f.lower() and 'sans' in f.lower()
                     and not any(w in f.lower() for w in ('narrow', 'cond'))), None)
    if pick:
        UI_FAMILY = pick
        FACE_FONT = (pick, 9, 'bold')
    return pick


def _find_font(name):
    global _font_index
    if _font_index is None:
        _font_index = {}
        for d in _FONT_DIRS:
            if os.path.isdir(d):
                for root, _, files in os.walk(d):
                    for f in files:
                        _font_index.setdefault(f.lower(), os.path.join(root, f))
    return _font_index.get(name.lower())


def font(role, px):
    px = max(6, int(round(px)))
    key = (role, px)
    if key in _font_cache:
        return _font_cache[key]
    face = NIMBUS_ROLES.get(role)
    if face is not None:
        path = _nimbus_faces().get(face) or (_nimbus_faces().get((False, False)) if face[0] else None)
        if path:
            try:
                f = ImageFont.truetype(path, px)
                _font_cache[key] = f
                return f
            except OSError:
                pass
    f = None
    for name, var in FONT_ROLES[role]:
        path = _find_font(name)
        if not path:
            continue
        try:
            f = ImageFont.truetype(path, px)
            if var:
                try:
                    f.set_variation_by_name(var)
                except Exception:
                    pass
            break
        except OSError:
            f = None
    if f is None:                        # none of the named fonts: any sans font installed
        _find_font('-')
        names = sorted(_font_index, key=lambda n: ('sans' not in n, 'bold' not in n, n))
        for name in names:
            if name.endswith(('.ttf', '.otf')) and 'mono' not in name:
                try:
                    f = ImageFont.truetype(_font_index[name], px)
                    break
                except OSError:
                    f = None
    if f is None:                        # last resort: Pillow's own font
        try:
            f = ImageFont.load_default(size=px)
        except TypeError:
            f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _glyph_w(f, c):
    """Advance width of one character. Pillow's built-in bitmap font has no getlength
    before Pillow 9.2 (Ubuntu 22.04 ships 9.0)."""
    if hasattr(f, 'getlength'):
        return f.getlength(c)
    return f.getsize(c)[0]


def _glyph_h(f):
    """Cap height of the bitmap font, for placing it without anchors (getsize went in Pillow 10)."""
    if hasattr(f, 'getbbox'):
        return f.getbbox('A')[3]
    return f.getsize('A')[1]


def text_width(s, f, tracking=0.0):
    return sum(_glyph_w(f, c) for c in s) + tracking * max(0, len(s) - 1)


def draw_text(d, x, y, s, f, fill, tracking=0.0, align='l', vanchor='s'):
    """Text at baseline y; align l / m / r; tracking in pixels between letters."""
    w = text_width(s, f, tracking)
    if align == 'm':
        x -= w / 2
    elif align == 'r':
        x -= w
    kw = {'anchor': 'l' + vanchor}
    if not isinstance(f, ImageFont.FreeTypeFont):    # bitmap font: no anchors, top-left only
        kw = {}
        y -= {'s': _glyph_h(f), 'm': _glyph_h(f) / 2}.get(vanchor, 0)
    if tracking == 0.0:
        d.text((x, y), s, font=f, fill=fill, **kw)
        return
    for c in s:
        d.text((x, y), c, font=f, fill=fill, **kw)
        x += _glyph_w(f, c) + tracking
