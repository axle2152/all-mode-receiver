"""
radio/ui/widgets.py - Keys, buttons, tooltips, meter bars, signal bars and lamps.
"""
import math
import tkinter as tk

from PIL import ImageTk

from . import fonts
from .art import render_key
from .theme import (
    BUTTON_BRUSH, BUTTON_SMALL, BUTTON_TRIM, DASH_BG, HINT_EDGE, HINT_FILL, HINT_HOT, HINT_INK,
    LAMP_INK_OFF, LAMP_OFF, SEG_OFF, SEG_ON, THEME)


# ---------------------------------------------------------------------------
# Keys and the faint hint buttons
# ---------------------------------------------------------------------------

class Key:
    """A key drawn into a panel canvas. Momentary unless `latched` is given
    (a callable returning the latched state)."""

    def __init__(self, canvas, box, s, legend='', icon=None, size=13.0,
                 on_press=None, on_release=None, latched=None):
        x0, y0, x1, y1 = box
        self.canvas, self.latched = canvas, latched
        self.on_press, self.on_release = on_press, on_release
        self.img_up = ImageTk.PhotoImage(render_key(x1 - x0, y1 - y0, s, legend, False, icon, size))
        self.img_dn = ImageTk.PhotoImage(render_key(x1 - x0, y1 - y0, s, legend, True, icon, size))
        self.item = canvas.create_image(round(x0 * s), round(y0 * s), anchor='nw', image=self.img_up)
        self._held = False
        canvas.tag_bind(self.item, '<ButtonPress-1>', self._press)
        canvas.tag_bind(self.item, '<ButtonRelease-1>', self._release)
        canvas.tag_bind(self.item, '<Enter>', lambda e: canvas.config(cursor='hand2'))
        canvas.tag_bind(self.item, '<Leave>', lambda e: canvas.config(cursor=''))
        self.refresh()

    def refresh(self):
        down = self._held or (self.latched is not None and self.latched())
        self.canvas.itemconfig(self.item, image=self.img_dn if down else self.img_up)

    def _press(self, _e):
        self._held = True
        self.refresh()
        if self.on_press:
            self.on_press()
        self.refresh()

    def _release(self, _e):
        if not self._held:
            return
        self._held = False
        if self.on_release:
            self.on_release()
        self.refresh()


class Hint:
    """Small, flat, low-contrast button that nearly disappears into the panel."""

    def __init__(self, canvas, box, s, text, command):
        x0, y0, x1, y1 = (v * s for v in box)
        self.c = canvas
        self.r = canvas.create_rectangle(x0, y0, x1, y1, fill=HINT_FILL, outline=HINT_EDGE)
        self.t = canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2 + 0.5, text=text,
                                    font=(fonts.UI_FAMILY, max(6, round(6.5 * s)), 'bold'), fill=HINT_INK)
        for it in (self.r, self.t):
            canvas.tag_bind(it, '<Enter>', lambda e: self._hot(True))
            canvas.tag_bind(it, '<Leave>', lambda e: self._hot(False))
            canvas.tag_bind(it, '<ButtonRelease-1>', lambda e: command())

    def _hot(self, on):
        self.c.itemconfig(self.t, fill=HINT_HOT if on else HINT_INK)
        self.c.config(cursor='hand2' if on else '')


# ---------------------------------------------------------------------------
# Advanced window widgets (from the Delco face)
# ---------------------------------------------------------------------------

def _mix(c0, c1, t):
    a = [int(c0[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(a, b))


class SilverButton(tk.Canvas):
    """Brushed-aluminium push button (the Advanced window's AGC button)."""

    def __init__(self, parent, size=BUTTON_SMALL, text='', font=None,
                 command=None, bg='#1a1a1a', radius=4, trim=2):
        w, h = size
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0)
        self._bw, self._bh, self._r, self._t = w, h, radius, trim
        self._text = text
        self._font = font or (fonts.UI_FAMILY, 12, 'bold')
        self._command = command
        self._tint = None
        self._pressed = False
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        self._draw(False)

    def set_tint(self, tint):
        if tint != self._tint:
            self._tint = tint
            self._draw(False)

    def _rounded(self, x0, y0, x1, y1, r, colour_at):
        h = y1 - y0
        for i in range(h):
            d = min(i, h - 1 - i)
            inset = r - (r * r - (r - d) ** 2) ** 0.5 if d < r else 0
            inset = int(round(inset))
            self.create_line(x0 + inset, y0 + i, x1 - inset, y0 + i, fill=colour_at(i, i / max(h - 1, 1)))

    @staticmethod
    def _grain(i):
        v = math.sin(i * 12.9898 + 4.1414) * 43758.5453
        return 2 * (v - math.floor(v)) - 1

    def _draw(self, down):
        self.delete('all')
        w, h, r, t = self._bw, self._bh, self._r, self._t
        top, bot = THEME['tints'][self._tint]
        if down:
            top, bot = bot, top
        self._rounded(0, 0, w, h, r + 1, lambda i, f: BUTTON_TRIM)

        def face(i, f):
            c = _mix(top, bot, f)
            g = self._grain(i) * BUTTON_BRUSH
            return _mix(c, '#ffffff' if g > 0 else '#000000', abs(g))
        self._rounded(t, t, w - t, h - t, max(r - 1, 1), face)
        hi, lo = THEME['bevel'] if not down else THEME['bevel_down']
        self.create_line(t + r, t, w - t - r, t, fill=hi)
        self.create_line(t + r, h - t - 1, w - t - r, h - t - 1, fill=lo)
        if self._text:
            self.create_text(w / 2, h / 2 + (1 if down else 0), text=self._text,
                             font=self._font, fill=THEME['ink'])

    def _press(self, _e):
        self._pressed = True
        self._draw(True)

    def _release(self, e):
        was = self._pressed
        self._pressed = False
        self._draw(False)
        if was and 0 <= e.x < self._bw and 0 <= e.y < self._bh and self._command:
            self._command()


def _seg_colour(style, f, mark=None):
    if style == 'high':
        return SEG_ON['poor'] if f < 0.3 else SEG_ON['fair'] if f < 0.57 else SEG_ON['good']
    if style == 'low':
        return SEG_ON['good'] if f < 0.2 else SEG_ON['fair'] if f < 0.45 else SEG_ON['poor']
    if style == 'limit':
        return SEG_ON['good'] if f <= (mark or 1.0) else SEG_ON['poor']
    return SEG_ON['help']


class Tooltip:
    """One or two sentences of help after a short hover."""

    def __init__(self, root):
        self.root, self._tip, self._job, self._cur = root, None, None, None

    def bind(self, widget, text):
        widget.bind('<Enter>', lambda e: self._want(e, text), add='+')
        widget.bind('<Leave>', lambda e: self._want(e, None), add='+')

    def bind_regions(self, canvas, lookup):
        canvas.bind('<Motion>', lambda e: self._want(e, lookup(e.x, e.y)), add='+')
        canvas.bind('<Leave>', lambda e: self._want(e, None), add='+')

    def _want(self, e, text):
        if callable(text):
            text = text()
        if text == self._cur and (self._tip is not None or self._job is not None):
            return
        self._hide()
        self._cur = text
        if text:
            x, y = e.x_root + 14, e.y_root + 16
            self._job = self.root.after(400, lambda: self._show(x, y, text))

    def _show(self, x, y, text):
        self._job = None
        tw = tk.Toplevel(self.root)
        tw.wm_overrideredirect(True)
        try:
            tw.attributes('-topmost', True)
        except tk.TclError:
            pass
        tk.Label(tw, text=text, justify=tk.LEFT, wraplength=320, font=(fonts.UI_FAMILY, 9),
                 fg='#e6eeec', bg='#1b2523', padx=9, pady=6, highlightthickness=1,
                 highlightbackground='#3f6f68').pack()
        tw.wm_geometry(f'+{x}+{y}')
        self._tip = tw

    def _hide(self):
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
        self._cur = None


class MeterPanel(tk.Canvas):
    """Rows of segmented LED bar meters (name, bar, value)."""
    NAME_W, VALUE_W, SEGS = 112, 72, 26

    def __init__(self, parent, width, height):
        super().__init__(parent, width=width, height=height, bg=DASH_BG, highlightthickness=0)
        self._pw, self._ph, self._keys, self._ids = width, height, None, {}

    def set_rows(self, rows):
        keys = [r[0] for r in rows]
        if keys == self._keys:
            return
        self.delete('all')
        self._keys, self._ids = keys, {}
        rh = (self._ph - 8) / max(len(rows), 1)
        x0, x1 = self.NAME_W, self._pw - self.VALUE_W
        sw = (x1 - x0) / self.SEGS
        for i, (key, name, style, tip, mark) in enumerate(rows):
            y = 4 + i * rh
            ym = y + rh / 2
            self.create_text(9, ym, anchor='w', text=name, font=(fonts.UI_FAMILY, 8, 'bold'), fill='#8fbfb6')
            segs = [self.create_rectangle(x0 + j * sw + 1, ym - 4, x0 + (j + 1) * sw - 1, ym + 4,
                                          fill=SEG_OFF, outline='') for j in range(self.SEGS)]
            if mark is not None:
                xm = x0 + mark * (x1 - x0)
                self.create_line(xm, ym - 7, xm, ym + 7, fill='#e0e0e0')
            ghost = self.create_line(0, 0, 0, 0, fill='#ffffff', width=2, state='hidden')
            val = self.create_text(self._pw - 8, ym, anchor='e', text='', font=('Courier', 9, 'bold'),
                                   fill='#cfeee8')
            self._ids[key] = dict(segs=segs, lit=-1, ghost=ghost, val=val, ym=ym, x0=x0, x1=x1,
                                  y0=y, y1=y + rh, tip=tip,
                                  cols=[_seg_colour(style, (j + 0.5) / self.SEGS, mark)
                                        for j in range(self.SEGS)])

    def show(self, key, frac, text, ghost=None):
        d = self._ids.get(key)
        if d is None:
            return
        n = int(round(max(0.0, min(1.0, frac)) * self.SEGS))
        if n != d['lit']:
            for j, s in enumerate(d['segs']):
                if d['lit'] < 0 or (j < n) != (j < d['lit']):
                    self.itemconfig(s, fill=d['cols'][j] if j < n else SEG_OFF)
            d['lit'] = n
        if ghost is None:
            self.itemconfig(d['ghost'], state='hidden')
        else:
            xg = d['x0'] + max(0.0, min(1.0, ghost)) * (d['x1'] - d['x0'])
            self.coords(d['ghost'], xg, d['ym'] - 6, xg, d['ym'] + 6)
            self.itemconfig(d['ghost'], state='normal')
        self.itemconfig(d['val'], text=text)

    def tip_at(self, x, y):
        for d in self._ids.values():
            if d['y0'] <= y < d['y1']:
                return d['tip']
        return None


class SignalBars(tk.Canvas):
    N = 5

    def __init__(self, parent):
        super().__init__(parent, width=64, height=52, bg=DASH_BG, highlightthickness=0)
        self._bars = []
        for i in range(self.N):
            x, hgt = 5 + i * 11, 8 + i * 7
            self._bars.append(self.create_rectangle(x, 40 - hgt, x + 8, 40, fill=SEG_OFF, outline=''))
        self._cap = self.create_text(32, 47, text='', font=(fonts.UI_FAMILY, 7, 'bold'), fill='#6f9f98')
        self._state = None

    def show(self, n, grade, caption):
        col = SEG_ON.get(grade, SEG_OFF)
        if (n, col, caption) == self._state:
            return
        self._state = (n, col, caption)
        for i, b in enumerate(self._bars):
            self.itemconfig(b, fill=col if i < n else SEG_OFF)
        self.itemconfig(self._cap, text=caption)


class LampStrip(tk.Canvas):
    LW, LH, COLS = 68, 17, 4

    def __init__(self, parent):
        super().__init__(parent, width=self.COLS * (self.LW + 4) + 2, height=2 * (self.LH + 4) + 2,
                         bg=DASH_BG, highlightthickness=0)
        self._keys, self._ids = None, {}

    def set_lamps(self, lamps):
        keys = [l[0] for l in lamps]
        if keys == self._keys:
            return
        self.delete('all')
        self._keys, self._ids = keys, {}
        for i, (key, label, colour, tip) in enumerate(lamps):
            x = 2 + (i % self.COLS) * (self.LW + 4)
            y = 2 + (i // self.COLS) * (self.LH + 4)
            r = self.create_rectangle(x, y, x + self.LW, y + self.LH, fill=LAMP_OFF, outline='#27332f')
            t = self.create_text(x + self.LW / 2, y + self.LH / 2 + 1, text=label,
                                 font=(fonts.UI_FAMILY, 7, 'bold'), fill=LAMP_INK_OFF)
            self._ids[key] = dict(r=r, t=t, c=colour, on=None, box=(x, y, x + self.LW, y + self.LH), tip=tip)

    def show(self, key, on):
        d = self._ids.get(key)
        if d is None or d['on'] == bool(on):
            return
        d['on'] = bool(on)
        self.itemconfig(d['r'], fill=d['c'] if on else LAMP_OFF, outline=d['c'] if on else '#27332f')
        self.itemconfig(d['t'], fill='#081010' if on else LAMP_INK_OFF)

    def tip_at(self, x, y):
        for d in self._ids.values():
            x0, y0, x1, y1 = d['box']
            if x0 <= x <= x1 and y0 <= y <= y1:
                return d['tip']
        return None
