"""
radio/ui/advanced.py - The Advanced window: diagnostics, scopes, spectra, the modulation chart.
"""
import math
import re
import time
import tkinter as tk
from collections import deque

import numpy as np
from PIL import Image, ImageTk

from . import fonts
from .backend import pi_call_candidates
from .constants import ADV_TICK_MS, FREQ_PEAK_RATE, PEAK_STEP, PILOT_COMP
from .theme import (
    CONST_DECAY, CONST_SIZE, DASH_BG, DASH_W, EQ_RESP, FACE_FG, HIST_AFTER, HIST_BEFORE,
    HIST_SHADE, IF_H, LEGEND_LIT, SCOPE_FG, SEG_ON, SPEC_AVG, SPEC_H, SPEC_RANGE_DB, SPEC_W)
from .tips import TIPS, _pty_name
from .widgets import LampStrip, MeterPanel, SignalBars, Tooltip


class AdvancedWindow:
    """The Advanced window: diagnostics, scopes, spectra, the modulation chart."""

    # Advanced window (the Delco's, plus the equalizer and monitor views)
    # ------------------------------------------------------------------

    def _build_advanced(self):
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title('Advanced')
        win.configure(bg='#1a1a1a')
        win.resizable(False, False)
        win.protocol('WM_DELETE_WINDOW', self._toggle_advanced)
        self._adv = win
        tips = self._tips = Tooltip(self.root)
        P = '#1a1a1a'
        am = lambda: self.band == 'AM'

        # Status line pinned at the bottom; everything else in a body that scrolls
        # when the screen is too short. On a wide screen the body is two columns
        # (Delco diagnostics left, equalizer views and controls right).
        self.status_var = tk.StringVar(value=self._status)
        tk.Label(win, textvariable=self.status_var, font=('Courier', 8), fg='#555555', bg='#111111',
                 anchor='w', padx=8).pack(fill=tk.X, side=tk.BOTTOM, ipady=3)
        outer = tk.Frame(win, bg=P)
        outer.pack(fill=tk.BOTH, expand=True)
        self._adv_cv = cv_ = tk.Canvas(outer, bg=P, highlightthickness=0, bd=0)
        self._adv_sb = tk.Scrollbar(outer, orient=tk.VERTICAL, command=cv_.yview)
        cv_.configure(yscrollcommand=self._adv_sb.set)
        body = tk.Frame(cv_, bg=P)
        cv_.create_window(0, 0, window=body, anchor='nw')
        two_col = self.root.winfo_screenwidth() >= 2 * (DASH_W + 20) + 40
        if two_col:
            colL, colR = tk.Frame(body, bg=P), tk.Frame(body, bg=P)
            colL.pack(side=tk.LEFT, anchor='n')
            colR.pack(side=tk.LEFT, anchor='n')
        else:
            colL = colR = body

        hd = tk.Frame(colL, bg=DASH_BG, highlightthickness=1, highlightbackground='#22302d')
        hd.pack(fill=tk.X, padx=10, pady=(10, 8))
        r1 = tk.Frame(hd, bg=DASH_BG)
        r1.pack(fill=tk.X)
        self._bars = SignalBars(r1)
        self._bars.pack(side=tk.LEFT, padx=(8, 10), pady=(8, 0))
        tips.bind(self._bars, TIPS['bars'])
        self._ps_var, self._id_var = tk.StringVar(value=''), tk.StringVar(value='')
        ps = tk.Label(r1, textvariable=self._ps_var, font=('Courier', 20, 'bold'), fg=LEGEND_LIT,
                      bg=DASH_BG, anchor='w', width=8)
        ps.pack(side=tk.LEFT, pady=(6, 0))
        idl = tk.Label(r1, textvariable=self._id_var, font=(fonts.UI_FAMILY, 9, 'bold'), fg='#8fbfb6',
                       bg=DASH_BG, anchor='w', justify=tk.LEFT, width=22)
        idl.pack(side=tk.LEFT, padx=(10, 0), pady=(6, 0))
        for w in (ps, idl):
            tips.bind(w, TIPS['rds_ps'])
        self._lamps = LampStrip(r1)
        self._lamps.pack(side=tk.RIGHT, padx=8, pady=6)
        tips.bind_regions(self._lamps, self._lamps.tip_at)
        self._rt_var, self._rdsd_var = tk.StringVar(value=''), tk.StringVar(value='')
        rt = tk.Label(hd, textvariable=self._rt_var, font=('Courier', 10, 'bold'), fg='#cfeee8',
                      bg=DASH_BG, anchor='w', width=86)
        rt.pack(fill=tk.X, padx=(12, 8), pady=(2, 0))
        tips.bind(rt, TIPS['rds_rt'])
        det = tk.Label(hd, textvariable=self._rdsd_var, font=('Courier', 8), fg='#5f9f95',
                       bg=DASH_BG, anchor='w', justify=tk.LEFT, width=104)
        det.pack(fill=tk.X, padx=(12, 8), pady=(1, 6))
        tips.bind(det, TIPS['rds_detail'])
        det.bind('<Button-1>', lambda e: self._addon_window())

        r2 = tk.Frame(colL, bg=P)
        r2.pack(padx=10, anchor='w')
        self._scope = {}
        for k in ('before', 'after'):
            cv = tk.Canvas(r2, width=CONST_SIZE, height=CONST_SIZE, bg=DASH_BG, highlightthickness=0)
            cv.pack(side=tk.LEFT, padx=(0, 6))
            self._scope[k] = cv
            tips.bind(cv, lambda: TIPS['scope_am' if am() else 'scope_fm'])
        self._meters = MeterPanel(r2, DASH_W - 2 * CONST_SIZE - 12, CONST_SIZE)
        self._meters.pack(side=tk.LEFT)
        tips.bind_regions(self._meters, self._meters.tip_at)

        head = tk.Frame(colL, bg=P)
        head.pack(fill=tk.X, padx=10, pady=(10, 2))
        self._spec_title = tk.Label(head, text='', font=fonts.FACE_FONT, fg=FACE_FG, bg=P)
        self._spec_title.pack(side=tk.LEFT)
        self._spec_mode = tk.StringVar(value='MPX')
        rb = dict(indicatoron=False, font=fonts.FACE_FONT, fg='#cfcfcf', bg='#2a2a2a', selectcolor='#004422',
                  activebackground='#004422', activeforeground='#ffffff', relief=tk.FLAT)
        for mo in ('MPX', 'RF'):
            tk.Radiobutton(head, text=mo, value=mo, variable=self._spec_mode, width=4,
                           command=self._spec_reset, **rb).pack(side=tk.RIGHT, padx=2)
        r3 = tk.Frame(colL, bg=P)
        r3.pack(padx=10, anchor='w')
        self._spec = tk.Canvas(r3, width=SPEC_W + 40, height=SPEC_H + 22, bg='#0a0f0e', highlightthickness=0)
        self._spec.pack(side=tk.LEFT)
        tips.bind(self._spec, TIPS['spec'])
        self._hist = tk.Canvas(r3, width=DASH_W - SPEC_W - 50, height=SPEC_H + 22, bg=DASH_BG,
                               highlightthickness=0)
        self._hist.pack(side=tk.LEFT, padx=(10, 0))
        tips.bind(self._hist, lambda: TIPS['hist_am' if am() else 'hist_fm'])
        self._hist_pts, self._hist_key = deque(maxlen=300), None

        # ---- the equalizer: channel before / after, and the echoes it is undoing
        h2 = tk.Frame(colR, bg=P)
        h2.pack(fill=tk.X, padx=10, pady=(10, 2))
        tk.Label(h2, text='CHANNEL BEFORE / AFTER THE EQUALIZER', font=fonts.FACE_FONT, fg=FACE_FG,
                 bg=P).pack(side=tk.LEFT)
        tk.Label(h2, text='ECHO PROFILE', font=fonts.FACE_FONT, fg=FACE_FG, bg=P, width=24,
                 anchor='w').pack(side=tk.RIGHT)
        r4 = tk.Frame(colR, bg=P)
        r4.pack(padx=10, anchor='w')
        self._ifc = tk.Canvas(r4, width=SPEC_W + 40, height=IF_H + 22, bg='#0a0f0e', highlightthickness=0)
        self._ifc.pack(side=tk.LEFT)
        tips.bind(self._ifc, TIPS['ifeq'])
        self._echoc = tk.Canvas(r4, width=DASH_W - SPEC_W - 50, height=IF_H + 22, bg=DASH_BG,
                                highlightthickness=0)
        self._echoc.pack(side=tk.LEFT, padx=(10, 0))
        tips.bind(self._echoc, TIPS['echoprof'])
        self._mon1_var, self._mon2_var = tk.StringVar(value=''), tk.StringVar(value='')
        for var in (self._mon1_var, self._mon2_var):
            lab = tk.Label(colR, textvariable=var, font=('Courier', 8), fg='#8fbfb6', bg=P, anchor='w',
                           width=106)                  # fixed, so a long reading can't widen the window
            lab.pack(fill=tk.X, padx=12, pady=(4 if var is self._mon1_var else 0, 0))
            tips.bind(lab, TIPS['monline'])
        h3 = tk.Frame(colR, bg=P)
        h3.pack(fill=tk.X, padx=10, pady=(10, 2))
        tk.Label(h3, text='MODULATION: HOW OFTEN EACH LEVEL IS REACHED', font=fonts.FACE_FONT, fg=FACE_FG,
                 bg=P).pack(side=tk.LEFT)
        self._distc = tk.Canvas(colR, width=DASH_W, height=128, bg=DASH_BG, highlightthickness=0)
        self._distc.pack(padx=10, anchor='w')
        tips.bind(self._distc, TIPS['moddist'])
        self._spec_reset()

        # Fit the screen: show it all if it fits, else scroll (bar and mouse wheel)
        win.update_idletasks()
        bw, bh = body.winfo_reqwidth(), body.winfo_reqheight()
        maxh = self.root.winfo_screenheight() - 150
        cv_.configure(width=bw, height=min(bh, maxh), scrollregion=(0, 0, bw, bh))
        cv_.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        if bh > maxh:
            self._adv_sb.pack(side=tk.RIGHT, fill=tk.Y)
            step = lambda n: cv_.yview_scroll(n, 'units')
            win.bind('<MouseWheel>', lambda e: step(-1 if e.delta > 0 else 1))
            win.bind('<Button-4>', lambda e: step(-1))
            win.bind('<Button-5>', lambda e: step(1))


    def _toggle_advanced(self):
        if self._adv_visible:
            self._adv.withdraw()
            self._adv_visible = False
        else:
            if not getattr(self, '_adv_placed', False):   # first time: under the rack, not on it
                self._adv_placed = True
                self._adv.update_idletasks()
                r = self.root
                x, y = r.winfo_rootx(), r.winfo_rooty() + r.winfo_height() + 34
                h = self._adv.winfo_reqheight()
                y = max(0, min(y, r.winfo_screenheight() - h - 60))
                self._adv.geometry(f'+{x}+{y}')
            self._adv.deiconify()
            self._adv.lift()
            self._adv_visible = True
            self._spec_reset()
            self._spec_loop()

    def _spec_loop(self):
        if not self._adv_visible:
            return
        for job in (self._spec_draw, self._scopes_draw, self._dash_update, self._if_draw,
                    self._echo_draw, self._mon_readouts, self._dist_draw):
            try:
                job()
            except Exception as e:
                self._report(job.__name__, e)
        self.root.after(ADV_TICK_MS, self._spec_loop)

    # ---- before / after constellation scopes (from the Delco)
    def _scopes_reset(self):
        n = CONST_SIZE
        self._scope_buf = {k: np.zeros((n, n), np.float32) for k in self._scope}
        self._scope_img, self._scope_photo, self._scope_cap = {}, {}, {}
        titles = {'before': 'AS RECEIVED', 'after': 'CARRIER LOCKED' if self.band == 'AM' else 'AFTER CLEANUP'}
        for k, cv in self._scope.items():
            cv.delete('all')
            self._scope_img[k] = cv.create_image(0, 0, anchor='nw')
            c, r = n / 2, n * 0.34
            cv.create_line(c, 20, c, n - 20, fill='#14302c')
            cv.create_line(20, c, n - 20, c, fill='#14302c')
            cv.create_oval(c - r, c - r, c + r, c + r, outline='#2a5a54', dash=(2, 3))
            col = '#%02x%02x%02x' % SCOPE_FG[k]
            cv.create_text(7, 6, anchor='nw', text=titles[k], font=(fonts.UI_FAMILY, 8, 'bold'), fill=col)
            self._scope_cap[k] = cv.create_text(n - 7, n - 6, anchor='se', text='',
                                                font=('Courier', 9, 'bold'), fill=col)

    def _scopes_draw(self):
        tb = self.tb
        if not tb or not self.power_on:
            return
        if self.band == 'AM':
            src = {'before': tb.snapshot('iq'), 'after': tb.snapshot('post')}
            caps = {'before': f"C/N {tb.cnr_db:4.1f} dB",
                    'after': 'locked' if getattr(tb, 'locked', False) else 'searching'}
        else:
            pre = tb.snapshot('pre')
            src = {'before': pre, 'after': tb.snapshot('post') if tb.eq_active else pre}
            caps = {'before': f"{tb.pre_cnr_db:4.1f} dB", 'after': f"{tb.cnr_db:4.1f} dB"}
        n = CONST_SIZE
        r = n * 0.34
        bg = np.array([10, 15, 14], np.float32)
        for k, z in src.items():
            z = np.asarray(z)
            if len(z) and np.any(z):
                z = z / max(np.sqrt(np.mean(np.abs(z) ** 2)), 1e-9)
                h, _, _ = np.histogram2d(n / 2 - r * z.imag, n / 2 + r * z.real, bins=n, range=[[0, n], [0, n]])
                buf = self._scope_buf[k]
                buf *= CONST_DECAY
                buf += h.astype(np.float32)
                lvl = 1 - np.exp(-buf / 2.5)
                fg = np.array(SCOPE_FG[k], np.float32)
                rgb = (bg + lvl[..., None] * (fg - bg)).astype(np.uint8)
                ph = ImageTk.PhotoImage(Image.fromarray(rgb))
                self._scope_photo[k] = ph
                self._scope[k].itemconfig(self._scope_img[k], image=ph)
                self._scope[k].tag_lower(self._scope_img[k])
            self._scope[k].itemconfig(self._scope_cap[k], text=caps[k])

    def _hist_draw(self, am):
        cv = self._hist
        cv.delete('all')
        w, h = int(cv['width']), int(cv['height'])
        x0, x1, y0, y1 = 28, w - 6, 22, h - 18
        top = 40.0 if am else 35.0
        cv.create_text(7, 6, anchor='nw', text='LAST 30 SECONDS', font=(fonts.UI_FAMILY, 8, 'bold'), fill='#8fbfb6')
        yv = lambda v: y1 - (y1 - y0) * max(0.0, min(top, v)) / top
        for v in (10, 20, 30) + ((40,) if am else ()):
            cv.create_line(x0, yv(v), x1, yv(v), fill='#14302c')
            cv.create_text(x0 - 4, yv(v), anchor='e', text=str(v), font=('Courier', 7), fill='#3f6f68')
        cv.create_text(x0, h - 8, anchor='w', text='dB', font=('Courier', 7), fill='#3f6f68')
        pts = list(self._hist_pts)
        if len(pts) >= 2:
            step = (x1 - x0) / (self._hist_pts.maxlen - 1)
            xs = [x1 - (len(pts) - 1 - i) * step for i in range(len(pts))]
            i = 0
            while i < len(pts):
                if pts[i][2]:
                    j = i
                    while j + 1 < len(pts) and pts[j + 1][2]:
                        j += 1
                    cv.create_rectangle(xs[i], y0, xs[j], y1, fill=HIST_SHADE, outline='')
                    i = j + 1
                else:
                    i += 1
            if not am:
                cv.create_line(*[c for x, p in zip(xs, pts) for c in (x, yv(p[0]))], fill=HIST_BEFORE)
            cv.create_line(*[c for x, p in zip(xs, pts) for c in (x, yv(p[1]))], fill=HIST_AFTER, width=2)
        if not am:
            cv.create_text(x1, h - 8, anchor='e', text='before', font=('Courier', 7, 'bold'), fill=HIST_BEFORE)
            cv.create_text(x1 - 48, h - 8, anchor='e', text='after', font=('Courier', 7, 'bold'), fill=HIST_AFTER)

    def _signal_bars(self, q, snr):
        clamp = lambda v: max(0.0, min(1.0, v))
        if q < 3 and (snr is None or snr < 6):
            self._bars.show(0, None, 'NO SIGNAL')
            return
        s = clamp((q - 3) / 30) if snr is None else 0.6 * clamp((q - 3) / 25) + 0.4 * clamp((snr - 5) / 35)
        n = max(1, min(5, int(math.ceil(s * 5 - 1e-9))))
        self._bars.show(n, 'good' if n >= 4 else 'fair' if n == 3 else 'poor',
                        ('POOR', 'WEAK', 'FAIR', 'GOOD', 'EXCELLENT')[n - 1])

    def _call_for(self, tb):
        pi = tb.rds_pi
        if pi is None:
            return ''
        key = f'{pi:04X}'
        if key in self.pi_calls:
            return self.pi_calls[key]
        text = f' {tb.rds_ps} {tb.rds_rt} '.upper()
        for cand in pi_call_candidates(pi):
            if re.search(rf'\b{cand}\b', text):
                self.pi_calls[key] = cand
                self._save_settings()
                return cand
        return tb.rds_call or ''

    def _addon_station_show(self, tb, data):
        """Station data an add-on decodes on AM, laid out the way RDS is on FM."""
        khz = f"{self.freq / 1e3:.0f} kHz"
        call = self._call_for(tb)
        pty = _pty_name(tb.rds_pty) if tb.rds_pty else ''
        ptyn = tb.rds_ptyn
        if ptyn and ptyn.lower() != pty.lower():
            pty = f"{pty} ({ptyn})" if pty else ptyn
        alert = tb.station.get('alert', 0)
        self._ps_var.set(tb.rds_ps if tb.rds_ps.strip() else (call or ''))
        self._id_var.set(f"{call or 'PI ' + format(tb.rds_pi, '04X')}   {khz}\n"
                         + ({1: 'EMERGENCY TEST', 2: 'EMERGENCY ALERT'}.get(alert) or pty))
        self._rt_var.set(tb.rds_rt or '')
        flag = lambda v: '--' if v is None else ('yes' if v else 'no')
        ct = tb.rds_ct
        ct = ct.strftime('%I:%M %p').lstrip('0') if hasattr(ct, 'strftime') else (ct or '')
        af = tb.rds_af or []
        extra = '   '.join(x for x in (
            ('AF ' + ', '.join(str(f) for f in af[:5]) + (f" +{len(af) - 5}" if len(af) > 5 else '')) if af else '',
            f"clock {ct}" if ct else '') if x)
        first = (f"PI {tb.rds_pi:04X}   TP {flag(tb.rds_tp)}  TA {flag(tb.rds_ta)}   "
                 f"{tb.rds_rate:4.2f} groups/s   {tb.rds_bler:3.0f}% lost" + (f"   {extra}" if extra else ''))
        lines = [a.readout() for a in data if a.readout()]
        self._rdsd_var.set('\n'.join([first] + lines))

    def _addon_window(self):
        """An add-on's own window (key I, or a click on the station detail line): the first
        add-on running on this receiver that has one. Pressed again, it hides."""
        tb = self.tb
        for a in (tb.addons if tb is not None else []):
            maker = a.window_maker()
            if maker is None or not a.alive:
                continue
            wins = self.__dict__.setdefault('_addon_wins', {})
            if a.name not in wins:
                try:
                    wins[a.name] = maker(self.root, lambda name=a.name: self._addon_obj(name))
                except Exception as e:
                    print(f"Add-on '{a.name}' window not opened: {type(e).__name__}: {e}")
                    return
            wins[a.name].toggle()
            return

    def _addon_obj(self, name):
        """The add-on of this name on the receiver running now, or None (for its window)."""
        tb = self.tb
        for a in (tb.addons if tb is not None else []):
            if a.name == name and a.alive:
                return a.obj
        return None

    def _rds_show(self, tb):
        mhz = f"{self.freq / 1e6:.1f} MHz"
        pi = tb.rds_pi
        lines = [a.readout() for a in tb.addons if a.alive and a.readout()]   # add-ons' own lines
        if pi is None:
            st = tb.addon_stereo
            if st:
                second = f'{st} STEREO' if tb.stereo_indicator else f'{st} stereo, blended to mono'
            else:
                second = 'RDS found, reading...' if tb.rds_synced else 'no RDS yet'
            self._ps_var.set('')
            self._id_var.set(f'{mhz}\n{second}')
            self._rt_var.set('')
            self._rdsd_var.set('\n'.join(lines))
            return
        call = self._call_for(tb)
        pty = _pty_name(tb.rds_pty) if tb.rds_pty else ''
        ptyn = tb.rds_ptyn
        if ptyn and ptyn.lower() != pty.lower():
            pty = f"{pty} ({ptyn})" if pty else ptyn
        self._ps_var.set(tb.rds_ps if tb.rds_ps.strip() else (call or ''))
        self._id_var.set(f"{call or 'PI ' + format(pi, '04X')}   {mhz}\n{pty}")
        self._rt_var.set(tb.rds_rt or '')
        flag = lambda v: '--' if v is None else ('yes' if v else 'no')
        ct = tb.rds_ct
        ct = ct.strftime('%I:%M %p').lstrip('0') if ct else '--'
        af = tb.rds_af
        afs = ' '.join(f"{f:.1f}" for f in af[:5]) + (f" +{len(af) - 5}" if len(af) > 5 else '') if af else '--'
        ms = '--' if tb.rds_ms is None else ('music' if tb.rds_ms else 'speech')
        self._rdsd_var.set('\n'.join([f"PI {pi:04X}   TP {flag(tb.rds_tp)}  TA {flag(tb.rds_ta)}   {ms}   "
                                     f"clock {ct}   AF {afs}   {tb.rds_rate:4.1f} groups/s   "
                                     f"{tb.rds_bler:3.0f}% lost"] + lines))

    def _dash_update(self):
        tb = self.tb
        am = self.band == 'AM'
        if not tb or not self.power_on:
            self._bars.show(0, None, 'OFF' if not self.power_on else '')
            for v in (self._ps_var, self._id_var, self._rt_var, self._rdsd_var):
                v.set('')
            return
        key = (self.band, round(self.freq))
        if key != self._hist_key:
            self._hist_key = key
            self._hist_pts.clear()
        if am:
            blank = tb.has('blankers')
            data = [a for a in tb.addons if a.alive]          # add-ons decoding on this receiver
            meter = data[0].meter() if data else None
            self._lamps.set_lamps([
                ('lock', 'LOCKED', SEG_ON['good'], TIPS['lamp_lock']),
                ('amst', 'AM STEREO', SEG_ON['help'], TIPS['lamp_amst']),
                ('pilot', 'PILOT', SEG_ON['help'], TIPS['lamp_pilot'])]
                + ([('data', data[0].label, SEG_ON['help'], TIPS['lamp_data']),
                    ('alert', 'ALERT', SEG_ON['poor'], TIPS['lamp_alert'])] if data else [])
                + [('agc', 'AGC', SEG_ON['good'], TIPS['lamp_agc'])]
                + ([('blank', 'BLANKER', SEG_ON['fair'], TIPS['lamp_blank'])] if blank else [])
                + [('clip', 'CLIP', SEG_ON['poor'], TIPS['lamp_clip'])])
            if data:
                self._lamps.show('data', tb.rds_synced)
                self._lamps.show('alert', bool(tb.station.get('alert', 0)))
            if blank:
                ibr, nbp, sgr = tb.blanker_stats()
                self._lamps.show('blank', ibr + sgr > 1.0 or nbp > 0.05)
            self._lamps.show('lock', getattr(tb, 'locked', False))
            self._lamps.show('amst', tb.stereo_indicator)
            self._lamps.show('pilot', tb.pilot_on)
            self._lamps.show('agc', tb.agc_enabled)
            self._lamps.show('clip', tb.clip_pct > 0.01)
            self._meters.set_rows([
                ('q', 'Signal quality', 'high', TIPS['am_quality'], None),
                ('lvl', 'Carrier level', 'high', TIPS['am_level'], None),
                ('adj', 'Neighbours', 'low', TIPS['am_adj'], None),
                ('bw', 'Bandwidth', 'help', TIPS['am_bw'], None),
                ('pil', 'Stereo pilot', 'help', TIPS['am_pilot'], None),
                ('st', 'Stereo', 'help', TIPS['am_stereo'], None)]
                + ([('data', meter[0], 'help', TIPS['am_data'], None)] if meter else [])
                + ([('blk', 'Blocks lost', 'low', TIPS['am_blocks'], None)] if data else [])
                + ([('ib', 'Impulse blanker', 'low', TIPS['am_ib'], None),
                    ('nb', 'Noise blanker', 'low', TIPS['am_nb'], None)] if blank else []))
            q = tb.cnr_db
            self._meters.show('q', q / 40, f"{q:4.1f} dB")
            self._meters.show('lvl', (tb.channel_dbfs + 60) / 60, f"{tb.channel_dbfs:5.1f}")
            self._meters.show('adj', (tb.adj_db + 40) / 50, f"{tb.adj_db:+4.0f} dB")
            bw = tb.bw / 1e3
            self._meters.show('bw', bw / 12, f"{bw:4.1f} kHz")
            self._meters.show('pil', tb.pilot_ratio_db / 30 if tb.pilot_on else 0,
                              f"{100 * tb.pilot_level:4.1f}%" if tb.pilot_on else 'none')
            self._meters.show('st', tb.current_blend if tb.pilot_on else 0,
                              f"{100 * tb.current_blend:3.0f}%" if tb.pilot_on else 'mono')
            if blank:
                on_ib, on_nb = getattr(tb, 'ib_on', False), getattr(tb, 'nb_level', 'off') != 'off'
                self._meters.show('ib', min(1.0, (ibr + sgr) / 60) if on_ib else 0,
                                  f"{ibr + sgr:4.0f}/min" if on_ib else 'off')
                self._meters.show('nb', min(1.0, nbp / 1.0) if on_nb else 0,
                                  f"{nbp:4.2f}%" if on_nb else 'off')
            if meter:
                self._meters.show('data', meter[1], meter[2])
            if data:
                has_bler = 'bler' in tb.station
                self._meters.show('blk', tb.rds_bler / 100 if has_bler else 0,
                                  f"{tb.rds_bler:3.0f}%" if has_bler else '--')
            self._signal_bars(q, None)
            if data and tb.rds_pi is not None:
                self._addon_station_show(tb, data)
            else:
                self._ps_var.set(f"{self.freq / 1e3:.0f} AM")
                stereo = (('KAHN ISB AM STEREO' if getattr(tb, 'stereo_system', '') == 'kahn' else 'C-QUAM AM STEREO')
                          if tb.stereo_indicator else
                          ('Kahn ISB' if getattr(tb, 'stereo_system', '') == 'kahn' else 'C-QUAM') + ', blended to mono'
                          if tb.pilot_on else 'AM, mono')
                if data:
                    label = data[0].label
                    self._id_var.set(stereo + (f"\n{label} found, reading..." if tb.rds_synced
                                               else f"\n{label}: searching"))
                else:
                    self._id_var.set(stereo + ('\ncarrier locked' if getattr(tb, 'locked', False)
                                               else '\nsearching for carrier'))
                src = {'direct_q': 'Q-branch direct sampling', 'native': 'quadrature tuner',
                       'upconverter': f"upconverter +{tb.UPCONVERTER_HZ / 1e6:.0f} MHz"}.get(tb.am_source, '')
                self._rt_var.set(f"Carrier offset {tb.carrier_hz:+.0f} Hz   level {tb.channel_dbfs:.1f} dBFS")
                if data:
                    lines = [a.readout() for a in data if a.readout()]
                    self._rdsd_var.set('\n'.join([f"Source: {src}"] + lines))
                else:
                    self._rdsd_var.set(f"Source: {src}   No RDS on AM")
            self._hist_pts.append((q, q, tb.stereo_indicator))
            self._hist_draw(True)
            return

        data = [a for a in tb.addons if a.alive]          # add-ons running on this receiver
        meter = data[0].meter() if data else None
        self._lamps.set_lamps([
            ('stereo', 'STEREO', SEG_ON['help'], TIPS['lamp_stereo']),
            ('rds', 'RDS', SEG_ON['help'], TIPS['lamp_rds'])]
            + ([('data', data[0].label, SEG_ON['help'], TIPS['lamp_fm_addon'])] if data else [])
            + [('echo', 'ECHO FIX', SEG_ON['good'], TIPS['lamp_echo']),
            ('nr', 'NOISE RED', SEG_ON['good'], TIPS['lamp_nr']),
            ('mp', 'MULTIPATH', SEG_ON['fair'], TIPS['lamp_mp']),
            ('static', 'STATIC CUT', SEG_ON['fair'], TIPS['lamp_static']),
            ('agc', 'AGC', SEG_ON['good'], TIPS['lamp_agc']),
            ('clip', 'CLIP', SEG_ON['poor'], TIPS['lamp_clip'])])
        nr = tb.nr_reduction_db if getattr(tb, 'noise_reduction', False) else 0.0
        dip = tb.dip_activity if getattr(tb, 'dip_hicut', False) else 0.0
        mp = max(0.0, tb.mp_excess_db, tb.eq_gain_db if tb.eq_active else 0.0)
        self._lamps.show('stereo', tb.stereo_indicator)
        self._lamps.show('rds', tb.rds_synced)
        if data:
            self._lamps.show('data', bool(tb.station.get('synced')))
        self._lamps.show('echo', tb.eq_active)
        self._lamps.show('nr', nr > 0.5)
        self._lamps.show('mp', tb.eq_gate or mp >= tb.EQ_MP_ON_DB)
        self._lamps.show('static', dip > 0.02)
        self._lamps.show('agc', tb.agc_enabled)
        self._lamps.show('clip', tb.clip_pct > 0.01)
        self._meters.set_rows([
            ('q', 'Signal quality', 'high', TIPS['quality'], None),
            ('snr', 'Signal strength', 'high', TIPS['strength'], None),
            ('mp', 'Echoes', 'low', TIPS['echo'], None),
            ('eq', 'Echo canceller', 'help', TIPS['eqfix'], None),
            ('nr', 'Noise reduction', 'help', TIPS['nr'], None),
            ('dip', 'Static cut', 'help', TIPS['static'], None),
            ('st', 'Stereo', 'help', TIPS['stereo'], None)]
            + ([('data', meter[0], 'help', TIPS['fm_addon'], None)] if meter else [])
            + [('tr', 'Treble', 'help', TIPS['treble'], None),
               ('bw', 'Bandwidth', 'help', TIPS['bw'], None),
               ('mod', 'Modulation', 'limit', TIPS['mod'], 0.75)])
        q, pre = tb.cnr_db, tb.pre_cnr_db
        snr = tb.spec_snr_db if tb.spec_snr_db is not None else 0.0
        self._meters.show('q', q / 35, f"{q:4.1f} dB", ghost=pre / 35 if tb.eq_active and abs(q - pre) > 0.5 else None)
        self._meters.show('snr', snr / 50, f"{snr:4.1f} dB")
        self._meters.show('mp', mp / 25, f"{mp:4.1f} dB")
        if tb.eq_active:
            self._meters.show('eq', tb.eq_gain_db / 15, f"+{max(0.0, tb.eq_gain_db):.1f} dB")
        else:
            self._meters.show('eq', 0, 'off' if tb.eq_mode == 'off' else 'learning' if tb.eq_gate else 'standby')
        self._meters.show('nr', nr / 12, 'off' if not getattr(tb, 'noise_reduction', False)
                          else 'idle' if nr < 0.1 else f"-{nr:.1f} dB")
        self._meters.show('dip', dip, f"{100 * dip:3.0f}%" if getattr(tb, 'dip_hicut', False) else 'off')
        self._meters.show('st', tb.current_blend if tb.stereo_indicator else 0,
                          f"{100 * tb.current_blend:3.0f}%" if tb.stereo_indicator else 'mono')
        if meter:
            self._meters.show('data', meter[1], meter[2])
        self._meters.show('tr', tb.current_treble, f"{100 * tb.current_treble:3.0f}%")
        ifk = tb.if_khz or tb.IF_WIDE_KHZ
        occ = tb.occupied_khz
        self._meters.show('bw', min(ifk, 100) / 100, 'wide' if ifk >= tb.IF_WIDE_KHZ else f"\u00b1{ifk:3.0f} kHz",
                          ghost=min(occ, 100) / 100 if occ else None)
        pk = self.mon.mod_peak()                    # the wide-path peak when the monitor has one
        dev = pk[1] if pk else (tb.dev_khz or 0.0)
        self._meters.show('mod', dev / 100, f"{dev:3.0f} kHz")
        self._signal_bars(q, snr)
        self._rds_show(tb)
        self._hist_pts.append((pre, q, tb.eq_active))
        self._hist_draw(False)

    # ---- spectrum (MPX / RF), from the Delco with SCA markers added
    def _spec_reset(self):
        cv, mode = self._spec, self._spec_mode.get()
        cv.delete('all')
        x0, y0 = 34, 6
        self._spec_avg, self._spec_top = None, None
        for k in range(0, SPEC_RANGE_DB + 1, 10):
            y = y0 + SPEC_H * k / SPEC_RANGE_DB
            cv.create_line(x0, y, x0 + SPEC_W, y, fill='#14302c')
        self._spec_db_ids = [cv.create_text(x0 - 4, y0 + SPEC_H * k / SPEC_RANGE_DB, text='', anchor='e',
                                            font=('Courier', 7), fill='#3f6f68')
                             for k in range(0, SPEC_RANGE_DB + 1, 10)]
        if self.band == 'AM' and mode == 'RF':
            self._spec_title.config(text='CHANNEL SPECTRUM  (+/-24 kHz, dB re FS)')
            marks = [(-20, '-20'), (-10, '-10'), (0, '0'), (10, '+10'), (20, '+20')]
            fx = lambda khz: x0 + SPEC_W * (khz + 24) / 48
        elif self.band == 'AM':
            self._spec_title.config(text='AUDIO SPECTRUM  (L+R, dB re 100% modulation)')
            marks = [(5, '5'), (10, '10'), (15, '15'), (20, '20')]
            fx = lambda khz: x0 + SPEC_W * khz / 24
        elif mode == 'RF':
            self._spec_title.config(text='CHANNEL SPECTRUM  (+/-120 kHz, dB re FS)')
            marks = [(-100, '-100'), (-50, '-50'), (0, '0'), (50, '+50'), (100, '+100')]
            fx = lambda khz: x0 + SPEC_W * (khz + 120) / 240
        else:
            self._spec_title.config(text='MPX SPECTRUM  (dB re 75 kHz deviation)')
            marks = [(15, '15'), (19, '19'), (38, '38'), (53, '53'), (57, '57'), (67, '67'), (92, '92')]
            fx = lambda khz: x0 + SPEC_W * khz / 120
        for khz, txt in marks:
            x = fx(khz)
            cv.create_line(x, y0, x, y0 + SPEC_H, fill='#14302c')
            cv.create_text(x, y0 + SPEC_H + 9, text=txt, font=('Courier', 7), fill='#3f6f68')
        self._spec_line = cv.create_line(x0, y0 + SPEC_H, x0 + SPEC_W, y0 + SPEC_H, fill=LEGEND_LIT, width=1)
        self._spec_x0, self._spec_y0 = x0, y0
        if getattr(self, '_scope', None):
            self._scopes_reset()

    def _spec_draw(self):
        tb = self.tb
        if not tb:
            return
        mode = self._spec_mode.get()
        if mode == 'RF':
            x = tb.snapshot('iq')
            n = len(x)
            w = np.hanning(n).astype(np.float32)
            p = np.abs(np.fft.fftshift(np.fft.fft(x * w))) ** 2 / (np.sum(w) ** 2)
        else:
            x = tb.snapshot('mpx')
            n = len(x)
            w = np.hanning(n).astype(np.float32)
            full = getattr(tb, 'MPX_FULL', 1.8)
            p = (2 * np.abs(np.fft.rfft(x * w))[:n // 2] / np.sum(w) / full) ** 2
        if not np.any(p):
            return
        if self._spec_avg is None or len(self._spec_avg) != len(p):
            self._spec_avg = p
        else:
            self._spec_avg += SPEC_AVG * (p - self._spec_avg)
        cols = SPEC_W
        db = 10 * np.log10(self._spec_avg[: len(self._spec_avg) // cols * cols].reshape(cols, -1).max(axis=1) + 1e-20)
        if mode == 'RF':
            want = np.ceil(db.max() / 10) * 10 + 10
            self._spec_top = want if self._spec_top is None else max(want, self._spec_top - 0.5)
            top = self._spec_top
        else:
            top = 0.0
        for k, tid in enumerate(self._spec_db_ids):
            self._spec.itemconfig(tid, text=f'{top - 10 * k:.0f}')
        y = self._spec_y0 + SPEC_H * np.clip((top - db) / SPEC_RANGE_DB, 0, 1)
        xs = self._spec_x0 + np.arange(cols)
        self._spec.coords(self._spec_line, *np.column_stack((xs, y)).ravel().tolist())

    # ---- new: the channel before / after the equalizer, with the EQ's response
    def _if_draw(self):
        cv = self._ifc
        cv.delete('all')
        x0, y0 = 34, 6
        x1, y1 = x0 + SPEC_W, y0 + IF_H
        g = '#14302c'
        for i in range(7):
            y = y0 + IF_H * i / 6
            cv.create_line(x0, y, x1, y, fill=g)
        fx = lambda khz: x0 + SPEC_W * (khz + 120) / 240
        for khz in (-100, -50, 0, 50, 100):
            cv.create_line(fx(khz), y0, fx(khz), y1, fill=g)
            cv.create_text(fx(khz), y1 + 9, text=f'{khz:+d}' if khz else '0 kHz', font=('Courier', 7), fill='#3f6f68')
        tb = self.tb
        if not tb or self.band != 'FM' or not self.power_on:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text='FM only' if self.band == 'AM' else '',
                           font=(fonts.UI_FAMILY, 9, 'bold'), fill='#3f6f68')
            return
        ifs = self._if_spectra(tb)
        if not ifs:
            return
        f, top = ifs['f'], ifs['top']
        for i in range(7):
            cv.create_text(x0 - 4, y0 + IF_H * i / 6, text=f'{top - 10 * i:.0f}', anchor='e',
                           font=('Courier', 7), fill='#3f6f68')
        sel = (f >= -120) & (f <= 120)
        xs = fx(f[sel])
        yv = lambda db: y0 + IF_H * np.clip((top - db) / 60, 0, 1)
        for key, col, wdt in (('pre', HIST_BEFORE, 1), ('post', HIST_AFTER, 2)):
            bx, by = self._bins(xs, -yv(ifs[key][sel]), SPEC_W // 2)
            cv.create_line(*np.column_stack((bx, -by)).ravel().tolist(), fill=col, width=wdt)
        h, wgt = tb.eq_taps() if tb.has('echo') else (None, 0.0)
        if h is not None and len(h) > 1:
            He = np.abs(np.fft.fftshift(np.fft.fft(np.asarray(h, np.complex128), ifs['n'])))
            band = np.abs(f) <= 90
            ref = float(np.median(He[band])) if np.any(band) else 1.0
            resp = 20 * np.log10(np.maximum(He / max(ref, 1e-12), 1e-6))
            ym = (y0 + y1) / 2
            ry = ym - np.clip(resp[sel], -20, 20) / 20 * (IF_H / 2)
            bx, by = self._bins(xs, ry, SPEC_W // 2)
            cv.create_line(*np.column_stack((bx, by)).ravel().tolist(), fill=EQ_RESP, dash=(3, 2))
            for v, t in ((20, '+20'), (0, '0'), (-20, '-20')):
                cv.create_text(x1 + 3, ym - v / 20 * (IF_H / 2), text=t, anchor='w', font=('Courier', 7), fill='#7a7a7a')
        state = 'equalizer in use' if tb.eq_active else ('equalizer learning' if tb.eq_gate else 'equalizer standby')
        cv.create_text(x0 + 6, y0 + 4, anchor='nw', text='as received', font=('Courier', 8, 'bold'), fill=HIST_BEFORE)
        cv.create_text(x0 + 100, y0 + 4, anchor='nw', text='after', font=('Courier', 8, 'bold'), fill=HIST_AFTER)
        cv.create_text(x0 + 150, y0 + 4, anchor='nw', text='EQ response (right scale)', font=('Courier', 8, 'bold'),
                       fill=EQ_RESP)
        cv.create_text(x1 - 6, y0 + 4, anchor='ne', text=state, font=('Courier', 8, 'bold'),
                       fill=HIST_AFTER if tb.eq_active else '#5f9f95')

    def _echo_draw(self):
        cv = self._echoc
        cv.delete('all')
        w, h = int(cv['width']), int(cv['height'])
        x0, x1, y0, y1 = 30, w - 8, 10, h - 62
        g = '#14302c'
        yv = lambda db: y0 + (y1 - y0) * min(1.0, max(0.0, -db / 40))
        for db in (0, -10, -20, -30, -40):
            cv.create_line(x0, yv(db), x1, yv(db), fill=g)
            cv.create_text(x0 - 4, yv(db), text=str(db), anchor='e', font=('Courier', 7), fill='#3f6f68')
        xv = lambda us: x0 + (x1 - x0) * (us + 20) / 270
        for us in (0, 100, 200):
            cv.create_line(xv(us), y0, xv(us), y1, fill=g)
            cv.create_text(xv(us), y1 + 8, text=f'{us} us' if us == 200 else str(us), font=('Courier', 7), fill='#3f6f68')
        tb = self.tb
        if not tb or self.band != 'FM' or not self.power_on:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text='FM only' if self.band == 'AM' else '',
                           font=(fonts.UI_FAMILY, 9, 'bold'), fill='#3f6f68')
            return
        prof = self._echo_profile(tb)
        if not prof:
            return
        for us, db in zip(prof['delays'], prof['db']):
            if db > -40:
                col = HIST_AFTER if abs(us) < 1 else (SEG_ON['fair'] if db > -20 else '#5f9f95')
                cv.create_line(xv(us), y1, xv(us), yv(db), fill=col, width=2)
        y = y1 + 22
        if prof['echoes']:
            for us, mi, db in prof['echoes']:
                cv.create_text(x0 - 22, y, anchor='w', font=('Courier', 8, 'bold'), fill='#cfeee8',
                               text=f'{us:5.1f} us {mi:4.2f} mi {db:+4.0f} dB')
                y += 12
        else:
            cv.create_text(x0 - 22, y, anchor='w', font=('Courier', 8), fill='#5f9f95',
                           text='no echoes above -30 dB')
        if not prof['active']:
            cv.create_text(x1, y0 + 2, anchor='ne', font=('Courier', 7), fill='#5f9f95', text='EQ not in use')

    def _dist_draw(self):
        """Peaks per second reaching each deviation level (log scale): a processed
        station shows a cliff at its ceiling; stray spikes show as a thin tail."""
        cv = self._distc
        cv.delete('all')
        w, h = int(cv['width']), int(cv['height'])
        x0, x1, y0, y1 = 44, w - 10, 8, h - 20
        lo_k, hi_k = 40.0, 110.0
        dec_lo, dec_hi = -1, 4                              # 0.1 .. 10000 peaks per second
        xv = lambda khz: x0 + (x1 - x0) * (khz - lo_k) / (hi_k - lo_k)
        yv = lambda r: y1 - (y1 - y0) * (math.log10(max(r, 10 ** dec_lo)) - dec_lo) / (dec_hi - dec_lo)
        g = '#14302c'
        for e in range(dec_lo, dec_hi + 1):
            cv.create_line(x0, yv(10 ** e), x1, yv(10 ** e), fill=g)
            cv.create_text(x0 - 4, yv(10 ** e), text=f'{10 ** e:g}', anchor='e', font=('Courier', 7), fill='#3f6f68')
        for khz in range(40, 111, 10):
            cv.create_line(xv(khz), y0, xv(khz), y1, fill=g)
            cv.create_text(xv(khz), y1 + 9, text=f'{khz}' + (' kHz' if khz == 110 else ''), font=('Courier', 7),
                           fill='#3f6f68')
        for khz, col, t in ((75.0, '#cfcfcf', '100%'), (82.5, '#5f6f6c', '110%')):
            cv.create_line(xv(khz), y0, xv(khz), y1, fill=col, dash=(2, 2))
            cv.create_text(xv(khz) + 3, y0 + 2, text=t, anchor='nw', font=('Courier', 7), fill=col)
        cv.create_line(x0, yv(FREQ_PEAK_RATE), x1, yv(FREQ_PEAK_RATE), fill=SEG_ON['fair'], dash=(4, 3))
        cv.create_text(x1 - 2, yv(FREQ_PEAK_RATE) - 2, text='frequent: 1 per second', anchor='se',
                       font=('Courier', 7), fill=SEG_ON['fair'])
        tb, mon = self.tb, self.mon
        if not tb or self.band != 'FM' or not self.power_on:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text='FM only' if self.band == 'AM' else '',
                           font=(fonts.UI_FAMILY, 9, 'bold'), fill='#3f6f68')
            return
        if not mon.mod_valid:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, font=(fonts.UI_FAMILY, 9, 'bold'), fill='#5f9f95',
                           text='not measurable: noise or multipath ahead of the equalizer')
            return
        now = time.monotonic()
        d = mon.distribution(now)
        if d is None:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text='counting...', font=(fonts.UI_FAMILY, 9, 'bold'),
                           fill='#5f9f95')
            return
        levels, rate, secs = d
        sel = (levels >= lo_k) & (levels <= hi_k) & (rate > 0)
        if np.any(sel):
            pts = np.column_stack((xv(levels[sel]), [yv(r) for r in rate[sel]])).ravel().tolist()
            if len(pts) >= 4:
                cv.create_line(*pts, fill=HIST_AFTER, width=2)
        fp = mon.frequent_peak(now)
        if fp is not None:
            cv.create_line(xv(fp), y0, xv(fp), y1, fill=SEG_ON['fair'])
            cv.create_text(xv(fp) - 3, y1 - 3, text=f'{fp:.1f}', anchor='se', font=('Courier', 8, 'bold'),
                           fill=SEG_ON['fair'])
        top = levels[rate > 0]
        if len(top):
            mx = float(top[-1] + PEAK_STEP)
            cv.create_line(xv(min(mx, hi_k)), y0, xv(min(mx, hi_k)), y1, fill=SEG_ON['poor'])
            cv.create_text(xv(min(mx, hi_k)) + 3, y1 - 3, text=f'{mx:.1f}', anchor='sw',
                           font=('Courier', 8, 'bold'), fill=SEG_ON['poor'])
        cv.create_text(x0 + 4, y0 + 2, anchor='nw', font=('Courier', 7), fill='#5f9f95',
                       text=f'peaks per second, last {secs:.0f} s')

    def _mon_readouts(self):
        tb, mon = self.tb, self.mon
        if not tb or not self.power_on:
            self._mon1_var.set('')
            self._mon2_var.set('')
            return
        if self._meters_missing:
            self._mon1_var.set('Monitor readouts need rx_meters.py next to fm_stereo.py, with the patched '
                               'fm_stereo.py and am_receiver.py.')
            self._mon2_var.set('')
            return
        now = time.monotonic()
        fmt = lambda v, f, unit='': '--' if v is None else f'{v:{f}}{unit}'
        over = mon.over_pct()
        pk = mon.mod_peak()
        if self.band == 'FM':
            off = mon.offset_khz()
            off_hz = None if off is None else off * 1000
            ppm = None if off_hz is None else off_hz / (self.freq / 1e6)
            st = self._mpx_stats
            sca = ' '.join(n for n, k in (('67k', 'sca67'), ('92k', 'sca92')) if st.get(k)) or 'none'
            freq = mon.frequent_peak(now) if mon.mod_valid else None
            dev = (f"highest +{pk[2]:.1f}/-{pk[3]:.1f}  frequent {fmt(freq, '.1f')} kHz" if pk else
                   'not measurable (noise/multipath)' if not mon.mod_valid else '--')
            self._mon1_var.set(
                f"Peak dev {dev}   over 100% {fmt(over, '.2f', '%')}   "
                f"pilot {fmt(tb.pilot_level / 1.8 * 100 * PILOT_COMP, '.1f', '%')}   RDS {fmt(st.get('rds'), '.1f', '%')}   SCA {sca}")
            carrier = f"{off_hz:+.0f} Hz ({ppm:+.1f} ppm)" if off_hz is not None else '--'
        else:
            neg = mon.frequent_peak(now, 'neg') if mon.mod_valid else None
            dev = (f"highest +{pk[2]:.0f}% / -{pk[3]:.0f}%  frequent negative {fmt(neg, '.0f', '%')}" if pk else
                   'not measurable (C/N under 40 dB)' if not mon.mod_valid else '--')
            self._mon1_var.set(f"Modulation {dev}   over +125/-100% {fmt(over, '.2f', '%')}   "
                               f"C-QUAM pilot {fmt(tb.pilot_level * 100 if tb.pilot_on else None, '.1f', '%')}")
            carrier = f"{tb.carrier_hz:+.0f} Hz"
        corr = mon.corr
        self._mon2_var.set(
            f"Carrier {carrier}   LUFS M {fmt(mon.loud.momentary(), '.1f')} "
            f"S {fmt(mon.loud.short_term(), '.1f')} I {fmt(mon.loud.integrated(now), '.1f')}   "
            f"LRA {fmt(mon.loud.lra(now), '.1f', ' LU')}   TP max {fmt(mon.tp_max, '.1f', ' dBTP')}   "
            f"corr {fmt(corr, '+.2f')}")
