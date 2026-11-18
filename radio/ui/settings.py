"""
radio/ui/settings.py - The Settings window: reception, panel, and the display colours.
"""
import time
import tkinter as tk

from . import fonts
from .backend import band_has
from .constants import AM_WIDTHS, OUTPUT_DB_CHOICES
from .theme import BUTTON_SMALL, COLOR_DEFAULTS, COLOR_ITEMS, COLOR_PANEL, FACE_FG
from .tips import TIPS
from .widgets import SilverButton


class SettingsWindow:
    """The Settings window: reception, panel, and the display colours."""

    # ------------------------------------------------------------------
    # Settings window: reception, panel, and the display colours
    # ------------------------------------------------------------------

    def _build_settings(self):
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title('Settings')
        win.configure(bg='#1a1a1a')
        win.resizable(False, False)
        win.protocol('WM_DELETE_WINDOW', self._toggle_settings)
        self._set, self._set_visible = win, False
        tips, P = self._tips, '#1a1a1a'
        rb = dict(indicatoron=False, font=fonts.FACE_FONT, fg='#cfcfcf', bg='#2a2a2a', selectcolor='#004422',
                  activebackground='#004422', activeforeground='#ffffff', relief=tk.FLAT)
        cb = dict(font=fonts.FACE_FONT, fg='#cfcfcf', bg=P, selectcolor='#2a2a2a',
                  activebackground=P, activeforeground='#ffffff', highlightthickness=0, bd=0)
        lab = dict(font=fonts.FACE_FONT, fg=FACE_FG, bg=P)

        bar = tk.Frame(win, bg=P)
        bar.pack(fill=tk.X, padx=10, pady=(10, 8))
        body = tk.Frame(win, bg=P)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 12))
        self._tab_var = tk.StringVar(value='reception')
        tabs = {}

        def show():
            for name, f in tabs.items():
                if name == self._tab_var.get():
                    f.grid()
                else:
                    f.grid_remove()
        for name, text in (('reception', 'RECEPTION'), ('panel', 'PANEL'), ('colors', 'COLOURS')):
            tk.Radiobutton(bar, text=text, value=name, variable=self._tab_var, width=12, command=show,
                           **rb).pack(side=tk.LEFT, padx=1)
            f = tk.Frame(body, bg=P)
            f.grid(row=0, column=0, sticky='nw')
            tabs[name] = f
        row = lambda parent, title: (lambda fr: (fr.pack(fill=tk.X, pady=(6, 0)),
                                                 tk.Label(fr, text=title, width=18, anchor='w', **lab).pack(side=tk.LEFT),
                                                 fr)[2])(tk.Frame(parent, bg=P))

        # ---- reception
        t = tabs['reception']
        r = row(t, 'HELPERS')
        self._blend_var = tk.BooleanVar(value=self.blend_on)
        self._hicut_var = tk.BooleanVar(value=self.hicut_on)
        self._nr_var = tk.BooleanVar(value=self.noise_red)
        self._nb_var = tk.BooleanVar(value=self.dip_hicut)
        for text, var, cmd, tip in (('Stereo blend', self._blend_var, self._enh_changed, 'sw_blend'),
                                    ('Treble cut', self._hicut_var, self._enh_changed, 'sw_hicut'),
                                    ('Noise reduction', self._nr_var, self._fm_assist_changed, 'sw_nr'),
                                    ('Static cut', self._nb_var, self._fm_assist_changed, 'sw_static')):
            w = tk.Checkbutton(r, text=text, variable=var, command=cmd, **cb)
            w.pack(side=tk.LEFT, padx=(0, 12))
            tips.bind(w, TIPS[tip])
        r = row(t, 'ECHO CANCELLER')
        self._eq_var = tk.StringVar(value=self.eq_mode)
        for mo in ('off', 'on', 'auto'):
            w = tk.Radiobutton(r, text=mo.upper(), value=mo, variable=self._eq_var, width=7,
                               command=self._enh_changed, **rb)
            w.pack(side=tk.LEFT, padx=1)
            tips.bind(w, TIPS['sw_eq'])
        r = row(t, 'FM BANDWIDTH')
        self._if_var = tk.StringVar(value=self.if_mode)
        for mo in ('auto', 'wide', 'narrow'):
            w = tk.Radiobutton(r, text=mo.upper(), value=mo, variable=self._if_var, width=7,
                               command=self._if_changed, **rb)
            w.pack(side=tk.LEFT, padx=1)
            tips.bind(w, TIPS['sw_bw'])
        r = row(t, 'AM DE-EMPHASIS')
        self._nrsc_var = tk.BooleanVar(value=self.am_nrsc)
        w = tk.Checkbutton(r, text='NRSC-1 (modified 75 \u00b5s)', variable=self._nrsc_var,
                           command=self._nrsc_changed, **cb)
        w.pack(side=tk.LEFT)
        tips.bind(w, TIPS['nrsc'])
        r = row(t, 'BAND LIMITS')
        self._ovr_var = tk.BooleanVar(value=self.override)
        w = tk.Checkbutton(r, text='Override: tune anywhere the dongle reaches', variable=self._ovr_var,
                           command=self._override_changed, **cb)
        w.pack(side=tk.LEFT)
        tips.bind(w, TIPS['override'])
        r = row(t, 'AM STEREO')
        self._ams_var = tk.StringVar(value=self.am_stereo)
        for val, caption in (('auto', 'AUTO'), ('cquam', 'C-QUAM'), ('kahn', 'KAHN ISB')):
            w = tk.Radiobutton(r, text=caption, value=val, variable=self._ams_var, width=9,
                               command=self._am_stereo_changed, **rb)
            w.pack(side=tk.LEFT, padx=1)
            tips.bind(w, TIPS['amstereo'])
        if band_has('AM', 'blankers'):                   # only once a receiver has them
            r = row(t, 'AM BLANKERS')
            self._ib_var = tk.BooleanVar(value=self.am_ib)
            w = tk.Checkbutton(r, text='IMPULSE', variable=self._ib_var, command=self._blankers_changed,
                               **cb)
            w.pack(side=tk.LEFT)
            tips.bind(w, TIPS['ib'])
            w = tk.Label(r, text='NOISE', **lab)
            w.pack(side=tk.LEFT, padx=(14, 4))
            tips.bind(w, TIPS['nb'])
            self._amnb_var = tk.StringVar(value=self.am_nb)     # not _nb_var: that's Static cut's
            for val, caption in (('off', 'OFF'), ('low', 'LOW'), ('med', 'MED'), ('high', 'HIGH')):
                w = tk.Radiobutton(r, text=caption, value=val, variable=self._amnb_var, width=6,
                                   command=self._blankers_changed, **rb)
                w.pack(side=tk.LEFT, padx=1)
                tips.bind(w, TIPS['nb'])
        r = row(t, 'AM WIDTH')
        self._amw_var = tk.IntVar(value=int(self.am_normal_hz))
        for hz in AM_WIDTHS:
            w = tk.Radiobutton(r, text=f'{hz / 1000:g} kHz', value=hz, variable=self._amw_var, width=8,
                               command=self._am_width_changed, **rb)
            w.pack(side=tk.LEFT, padx=1)
            tips.bind(w, TIPS['amwidth'])
        self._amauto_var = tk.BooleanVar(value=self.am_auto_width)
        w = tk.Checkbutton(r, text='AUTO', variable=self._amauto_var, command=self._am_width_changed, **cb)
        w.pack(side=tk.LEFT, padx=(14, 0))
        tips.bind(w, TIPS['amauto'])
        r = row(t, 'TUNER GAIN')
        self.agc_btn = SilverButton(r, size=BUTTON_SMALL, text='AGC', font=fonts.FACE_FONT, command=self._toggle_agc)
        self.agc_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._style_agc_btn()
        self.gain_var = tk.DoubleVar(value=self.rf_gain)
        tk.Scale(r, variable=self.gain_var, from_=0, to=49.6, resolution=0.1, showvalue=False,
                 orient=tk.HORIZONTAL, length=220, bg=P, fg='#555555', troughcolor='#2a2a2a',
                 highlightthickness=0, command=self._on_gain_change).pack(side=tk.LEFT)
        self.gain_label = tk.Label(r, text='', font=('Courier', 9), fg='#7fbfb3', bg=P, width=11, anchor='w')
        self.gain_label.pack(side=tk.LEFT, padx=(6, 0))
        self._update_gain_label(self.rf_gain)
        r = row(t, 'S-METER')
        w = tk.Label(r, text='Signal of known strength', **lab)
        w.pack(side=tk.LEFT)
        tips.bind(w, TIPS['smeter'])
        self._cal_var = tk.StringVar(value='-73')
        e = tk.Entry(r, textvariable=self._cal_var, width=7, bg='#2a2a2a', fg='#cfcfcf', justify=tk.RIGHT,
                     insertbackground='#cfcfcf', relief=tk.FLAT)
        e.pack(side=tk.LEFT, padx=(8, 3))
        e.bind('<Return>', lambda ev: self._cal_press())
        tips.bind(e, TIPS['smeter'])
        tk.Label(r, text='dBm', **lab).pack(side=tk.LEFT, padx=(0, 10))
        for text, cmd in (('CALIBRATE', self._cal_press), ('CLEAR', self._cal_clear)):
            w = tk.Button(r, text=text, font=fonts.FACE_FONT, fg='#cfcfcf', bg='#2a2a2a', relief=tk.FLAT,
                          activebackground='#3a3a3a', activeforeground='#ffffff', command=cmd)
            w.pack(side=tk.LEFT, padx=1)
            tips.bind(w, TIPS['smeter'])
        r = row(t, '')
        self._cal_label = tk.Label(r, text='', font=('Courier', 8), fg='#6f8f8a', bg=P, anchor='w')
        self._cal_label.pack(side=tk.LEFT)

        # ---- panel
        t = tabs['panel']
        r = row(t, 'NAMEPLATE')
        self._badge_var = tk.StringVar(value=self.badge)
        be = tk.Entry(r, textvariable=self._badge_var, width=24, bg='#2a2a2a', fg='#cfcfcf',
                      insertbackground='#cfcfcf', relief=tk.FLAT)
        be.pack(side=tk.LEFT)
        be.bind('<Return>', lambda e: self._badge_changed())
        be.bind('<FocusOut>', lambda e: self._badge_changed())
        r = row(t, 'MONITOR UNIT')
        self._mon_var = tk.BooleanVar(value=self.monitor_on)
        tk.Checkbutton(r, text='Show under the tuner', variable=self._mon_var, command=self._toggle_monitor,
                       **cb).pack(side=tk.LEFT)
        r = row(t, 'OUTPUT LEVEL')
        self._out_var = tk.DoubleVar(value=self.output_db)
        for db in OUTPUT_DB_CHOICES:
            tk.Radiobutton(r, text=f'{db:+.0f} dB' if db else '0 dB', value=db, variable=self._out_var,
                           width=7, command=self._output_changed, **rb).pack(side=tk.LEFT, padx=1)
        r = row(t, 'PEAK MOD')
        self._pk_var = tk.StringVar(value=self.peak_mode)
        for mo, txt in (('frequent', 'FREQUENT'), ('highest', 'HIGHEST')):
            w = tk.Radiobutton(r, text=txt, value=mo, variable=self._pk_var, width=13,
                               command=self._peak_mode_changed, **rb)
            w.pack(side=tk.LEFT, padx=1)
            tips.bind(w, TIPS['pkmode'])
        r = row(t, 'SYNC TO AUDIO')
        self._sync_var = tk.BooleanVar(value=self.sync_audio)
        w = tk.Checkbutton(r, text='Meters and indicators', variable=self._sync_var,
                           command=self._sync_changed, **cb)
        w.pack(side=tk.LEFT)
        tips.bind(w, TIPS['sync'])
        self._lag_label = tk.Label(r, text='', font=('Courier', 8), fg='#6f8f8a', bg=P)
        self._lag_label.pack(side=tk.LEFT, padx=(14, 0))
        r = row(t, 'GLASS GLOW')
        self._glow_var = tk.DoubleVar(value=round(self.glow * 100))
        sc = tk.Scale(r, variable=self._glow_var, from_=0, to=150, resolution=5, orient=tk.HORIZONTAL,
                      length=220, showvalue=True, bg=P, fg='#9a9a9a', troughcolor='#2a2a2a',
                      highlightthickness=0, font=('Courier', 8), command=self._glow_changed)
        sc.pack(side=tk.LEFT)
        tips.bind(sc, TIPS['glow'])
        tk.Label(r, text='%', **lab).pack(side=tk.LEFT)
        r = row(t, 'RDS LEVEL')
        self._rdss_var = tk.StringVar(value=self.rds_scale)
        for mo, txt in (('standard', 'IEC 62106'), ('stereo_tool', 'STEREO TOOL')):
            w = tk.Radiobutton(r, text=txt, value=mo, variable=self._rdss_var, width=13,
                               command=self._rds_scale_changed, **rb)
            w.pack(side=tk.LEFT, padx=1)
            tips.bind(w, TIPS['rdsscale'])

        # ---- colours
        t = tabs['colors']
        cols = tk.Frame(t, bg=P)
        cols.pack(anchor='w', pady=(4, 0))
        self._swatches = {}
        for ci, (panel, title) in enumerate((('tuner', 'TUNER DISPLAY'), ('monitor', 'MONITOR DISPLAY'),
                                             ('screen', 'MONITOR SCREEN'))):
            col = tk.Frame(cols, bg=P)
            col.grid(row=0, column=ci, sticky='nw', padx=(0 if ci == 0 else 24, 0))
            tk.Label(col, text=title, anchor='w', **lab).pack(anchor='w', pady=(0, 4))
            for key, label, _ in COLOR_ITEMS[panel]:
                fr = tk.Frame(col, bg=P)
                fr.pack(anchor='w', pady=1)
                sw = tk.Canvas(fr, width=34, height=16, bg=P, highlightthickness=1,
                               highlightbackground='#3a3a3a', cursor='hand2')
                sw.pack(side=tk.LEFT)
                tx = tk.Label(fr, text=label, font=(fonts.UI_FAMILY, 9), fg='#cfcfcf', bg=P, anchor='w')
                tx.pack(side=tk.LEFT, padx=(8, 0))
                sw.bind('<ButtonRelease-1>', lambda e, k=key, l=label: self._pick_color(k, l))
                for seq in ('<Button-3>', '<Button-2>'):
                    sw.bind(seq, lambda e, k=key: self._reset_color(k))
                tips.bind(sw, TIPS['swatch'])
                self._swatches[key] = (sw, tx, label)
            tk.Button(col, text='DEFAULTS', font=fonts.FACE_FONT, fg='#cfcfcf', bg='#2a2a2a', relief=tk.FLAT,
                      activebackground='#3a3a3a', activeforeground='#ffffff',
                      command=lambda pn=panel: self._reset_panel(pn)).pack(anchor='w', pady=(8, 0))
        tk.Label(t, text='Click a swatch to choose its colour; right-click it for the default.',
                 font=(fonts.UI_FAMILY, 8), fg='#6f8f8a', bg=P).pack(anchor='w', pady=(10, 0))
        self._refresh_swatches()

        # every tab in the same cell, the cell sized for the largest
        win.update_idletasks()
        body.grid_columnconfigure(0, minsize=max(f.winfo_reqwidth() for f in tabs.values()))
        body.grid_rowconfigure(0, minsize=max(f.winfo_reqheight() for f in tabs.values()))
        show()

    def _cal_press(self):
        """Settings > S-meter > CALIBRATE: the station tuned now is the level typed."""
        try:
            known = float(self._cal_var.get().replace('\u2212', '-'))
        except ValueError:
            msg = 'Type the signal\'s level in dBm, for example -73.'
        else:
            msg = (self._smeter_calibrate(known) if -150 <= known <= 30
                   else 'That level is out of range: -150 to +30 dBm.')
        self._cal_msg = (msg, time.monotonic() + 6.0)

    def _cal_clear(self):
        self._cal_msg = (self._smeter_clear(), time.monotonic() + 6.0)

    def _toggle_settings(self):
        if self._set_visible:
            self._set.withdraw()
            self._set_visible = False
            return
        if not getattr(self, '_set_placed', False):
            self._set_placed = True
            self._set.update_idletasks()
            r = self.root
            x = r.winfo_rootx() + 60
            y = max(0, min(r.winfo_rooty() + r.winfo_height() + 34,
                           r.winfo_screenheight() - self._set.winfo_reqheight() - 60))
            self._set.geometry(f'+{x}+{y}')
        self._gain_sync()
        self._set.deiconify()
        self._set.lift()
        self._set_visible = True

    def _gain_sync(self):
        self._slider_auto_val = self.rf_gain
        self.gain_var.set(self.rf_gain)
        self._style_agc_btn()
        self._update_gain_label(self.rf_gain)

    def _swatch_colour(self, key):
        if key == 'hold' and 'hold' not in self.colors:
            return None
        return self.colors.get(key) or COLOR_DEFAULTS[key]

    def _refresh_swatches(self):
        for key, (sw, tx, label) in self._swatches.items():
            sw.delete('all')
            c = self._swatch_colour(key)
            if c is None:                              # peak hold: takes the bar's colour
                a = self.colors.get('lr') or COLOR_DEFAULTS['lr']
                b = self.colors.get('lr_hot') or COLOR_DEFAULTS['lr_hot']
                sw.create_rectangle(0, 0, 17, 17, fill=a, outline='')
                sw.create_rectangle(17, 0, 36, 17, fill=b, outline='')
                tx.config(text=label + '  (same as bar)')
            else:
                sw.create_rectangle(0, 0, 36, 18, fill=c, outline='')
                tx.config(text=label)

    def _pick_color(self, key, label):
        from tkinter import colorchooser
        cur = self._swatch_colour(key) or self.colors.get('lr') or COLOR_DEFAULTS['lr']
        _, h = colorchooser.askcolor(color=cur, parent=self._set, title=f'Colour: {label}')
        if h:
            self.colors[key] = h.lower()
            self._colors_changed(COLOR_PANEL[key])

    def _reset_color(self, key):
        if key in self.colors:
            del self.colors[key]
            self._colors_changed(COLOR_PANEL[key])

    def _reset_panel(self, panel):
        if any(COLOR_PANEL.get(k) == panel for k in self.colors):
            self.colors = {k: v for k, v in self.colors.items() if COLOR_PANEL.get(k) != panel}
            self._colors_changed(panel)

    def _colors_changed(self, panel):
        """Recolour the display a colour belongs to (cached, so it's quick)."""
        self._refresh_swatches()
        for w in (self.root, self._set):
            w.config(cursor='watch')
        self.root.update_idletasks()
        try:
            if panel == 'tuner':
                self.vfd.set_colors(self._colors_for('tuner'))
                self._refresh_vfd(force=True)
            elif panel == 'monitor':
                self.mvfd.set_colors(self._colors_for('monitor'))
                self._mshown = None
            else:
                self.crt.set_colors(self._colors_for('screen'))
        finally:
            for w in (self.root, self._set):
                w.config(cursor='')
        self._save_settings()

    def _override_changed(self):
        self.override = bool(self._ovr_var.get())
        if not self.override and not self._in_band():
            self._tune_to(self.freq)                   # back inside the band
        self._save_settings()

    def _blankers_changed(self):
        self.am_ib, self.am_nb = bool(self._ib_var.get()), self._amnb_var.get()
        if self.tb is not None and self.tb.has('blankers'):
            self.tb.set_blankers(impulse=self.am_ib, noise=self.am_nb)
        self._save_settings()

    def _am_stereo_changed(self):
        self.am_stereo = self._ams_var.get()
        if self.tb is not None and self.tb.has('am_stereo'):
            self.tb.set_stereo_system(self.am_stereo)
        self._save_settings()

    def _am_width_changed(self):
        self.am_normal_hz = int(self._amw_var.get())
        self.am_auto_width = bool(self._amauto_var.get())
        if self.tb is not None and self.tb.has('am_width'):
            self.tb.set_auto_ceiling(self.am_normal_hz)
            self.tb.set_if_mode('auto' if self.am_auto_width else 'fixed')
        self._save_settings()

    def _nrsc_changed(self):
        self.am_nrsc = bool(self._nrsc_var.get())
        if self.tb is not None and self.tb.has('nrsc'):
            self.tb.set_nrsc_deemphasis(self.am_nrsc)
        self._save_settings()

    def _sync_changed(self):
        self.sync_audio = bool(self._sync_var.get())
        self._save_settings()

    def _glow_changed(self, _v=None):
        g = float(self._glow_var.get()) / 100.0
        if abs(g - self.glow) < 1e-6:
            return
        self.glow = self.vfd.glow = self.mvfd.glow = g
        self._refresh_vfd(force=True)
        self._mshown = None
        self._save_settings()

    def _rds_scale_changed(self):
        self.rds_scale = self._rdss_var.get()
        self._mpx_t = 0.0
        self._save_settings()

    def _enh_changed(self):
        self.blend_on, self.hicut_on = self._blend_var.get(), self._hicut_var.get()
        self.eq_mode = self._eq_var.get()
        if self.tb is not None and self.tb.has('fm_helpers'):
            self.tb.set_enhancements(blend=self.blend_on, hicut=self.hicut_on)
        if self.tb is not None and self.tb.has('echo'):
            self.tb.set_eq_mode(self.eq_mode)
        self._save_settings()

    def _fm_assist_changed(self):
        self.dip_hicut, self.noise_red = self._nb_var.get(), self._nr_var.get()
        if self.tb is not None and self.tb.has('fm_helpers'):
            self.tb.set_dip_hicut(self.dip_hicut)
            self.tb.set_noise_reduction(self.noise_red)
        self._save_settings()

    def _if_changed(self):
        self.if_mode = self._if_var.get()                 # FM only; AM has its own width settings
        if self.tb is not None and self.tb.has('fm_bandwidth'):
            self.tb.set_if_mode(self.if_mode)
        self._save_settings()

    def _badge_changed(self):
        t = self._badge_var.get().strip()[:24]
        if t != self.badge:
            self.badge = t
            self._render_plates()
            self._update_title()
            self._save_settings()

    def _peak_mode_changed(self):
        self.peak_mode = self._pk_var.get()
        self._mshown = None
        self._save_settings()

    def _output_changed(self):
        self.output_db = float(self._out_var.get())
        self._apply_output()
        self._save_settings()

    def _on_gain_change(self, val):
        gain = float(val)
        if self._slider_auto_val is not None and abs(gain - self._slider_auto_val) < 0.05:
            return
        self._slider_auto_val = None
        self.agc_on = False
        if self.tb:
            self.tb.set_manual_gain(gain)
            gain = self.tb.gain_db
        self.rf_gain = gain
        self._style_agc_btn()
        self._update_gain_label(gain)

    def _toggle_agc(self):
        self.agc_on = not self.agc_on
        if self.tb:
            self.tb.set_agc(self.agc_on)
        self._style_agc_btn()
        self._update_gain_label(self.rf_gain)
        self._save_settings()

    def _style_agc_btn(self):
        self.agc_btn.set_tint('green' if self.agc_on else None)

    def _update_gain_label(self, gain):
        self.gain_label.config(text=f"{'AGC' if self.agc_on else 'MAN'} {gain:4.1f} dB")
