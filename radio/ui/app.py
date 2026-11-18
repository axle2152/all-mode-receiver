"""
radio/ui/app.py - The tuner and monitor window itself (TunerApp) and main().
"""
import argparse
import gc
import json
import math
import os
import re
import threading
import time
import traceback
import tkinter as tk
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageTk

from .advanced import AdvancedWindow
from .art import _rrect, brushed_plate, num_chars
from .backend import AMReceiver, FMStereoReceiver, _AM_ERR
from .constants import (
    AM_MAX, AM_MIN, AM_MOD_MIN_CNR_DB, AM_WIDTHS, BADGE_DEFAULT, BANDS, DELCO_SETTINGS,
    DIRECT_WAIT_S, ENTRY_WAIT_S, FM_DEVICE_ARGS, FM_MAX, FM_MIN, GLOW_DEFAULT,
    HOLD_TO_SEARCH_S, LAG_MAX_S, LAG_SELF_S, LAG_SMOOTH_S, LAMP_TEST_S, MEMORY_WAIT_S,
    MILES_PER_US, MOD_MIN_CNR_DB, MON_SETTLE_S, MPX_STATS_S, N_CHANNELS, OUTPUT_DB_DEFAULT,
    OVER_LAMP_S, PILOT_COMP, POWER_SLEEP_S, RDS_CAL_STEREO_TOOL, RDS_COMP, RDS_CREST,
    RDS_RT_PAUSE_S, RDS_SCROLL_S, RTL_RANGE, SEEK_CONFIRM_N, SEEK_MIN_CNR_DB, SEEK_QUICK_N,
    SEEK_TIMEOUT_S, SETTINGS_FALLBACK, SETTINGS_NAME, SIG_FALL_PER_S, SIG_RANGE, SIG_STEPS,
    TUNED_HYST_DB, TUNED_SETTLE_S, TUNER_MIN, UI_TICK_MS, XOVER_HZ, _ASSET_DIR)
from .crt import Crt
from .fonts import draw_text, font, text_width, use_nimbus_in_tk
from .frequency import as_text, freq_digits
from . import smeter
from .monitor import DelayLine, MonitorLogic
from .settings import SettingsWindow
from .theme import (
    AM_KEY, BASE_H, BASE_W, COLOR_DEFAULTS, COLOR_PANEL, CRT_BOX, CRT_WIN, DOT_FIELDS,
    ECHO_KEY, FM_KEY, FREEZE_KEY, HINTS, HOLD_KEY, IF_KEY, LABEL_Y, LIP, LOWER_LABEL_Y,
    MEM_KEY, MONO_KEY, MPX_KEY, PAD_GAP, PAD_ROWS, PAD_W, PAD_X, POWER_KEY, PPM_KEY, PRINT,
    PRINT_SOFT, RACK_GAP, RESET_KEY, SCALE_Y, SIG_PITCH, SIG_X, SPEC_AVG, SS, TUNE_DOWN, TUNE_UP,
    VFD_BOX, VU_KEY, WELL, WIDE_KEY, WIN)
from .vfd import MonitorVfd, TunerVfd
from .widgets import Hint, Key


# ---------------------------------------------------------------------------
# The tuner and monitor
# ---------------------------------------------------------------------------

class TunerApp(SettingsWindow, AdvancedWindow):
    def __init__(self, root, scale=1.0, freq=None, rf_gain=None, agc=None, lmr_gain=1.0,
                 log_path=None, record_iq=None, am_source=None, upconverter_hz=None,
                 audio_device=''):
        self.root, self.s = root, scale
        self.override = False
        saved = self._load_settings()                   # sets self.override first
        delco = self._load_delco_dsp()

        self.band    = saved.get('band', 'FM')
        self.fm_freq = saved.get('fm_freq', 90.5e6)
        self.am_freq = saved.get('am_freq', 750e3)
        if freq is not None:
            self.band = 'AM' if freq < 30e6 else 'FM'
            if self.band == 'AM':
                self.am_freq = freq
            else:
                self.fm_freq = freq
        if AMReceiver is None:
            self.band = 'FM'
        self.am_source, self.upconverter_hz = am_source or 'direct_q', upconverter_hz
        self.audio_device = audio_device or ''         # GNU Radio sound output; '' = system default
        self.fm_freq = self._fit('FM', self.fm_freq)
        self.am_freq = self._fit('AM', self.am_freq)
        self.freq = self.am_freq if self.band == 'AM' else self.fm_freq

        pick = lambda key, default: saved.get(key, delco.get(key, default))
        self.channels   = saved.get('channels') or self._seed_channels()
        self.fm_mono    = bool(saved.get('fm_mono', False))
        self.power_on   = bool(saved.get('power', True))
        self.output_db  = saved.get('output_db', OUTPUT_DB_DEFAULT)
        self.rf_gain    = rf_gain if rf_gain is not None else pick('gain', 8.7)
        self.agc_on     = agc if agc is not None else pick('agc', True)
        self.blend_on   = pick('blend', True)
        self.hicut_on   = pick('hicut', True)
        self.eq_mode    = pick('eq_mode', 'auto')
        self.if_mode    = pick('if_mode', 'auto')
        self.dip_hicut  = pick('dip_hicut', True)
        self.noise_red  = pick('noise_red', True)
        self.pi_calls   = dict(pick('pi_calls', {}))
        self.badge      = saved.get('badge', BADGE_DEFAULT)
        self.monitor_on = bool(saved.get('monitor', True))
        self.meter_mode = saved.get('meter_mode', 'ppm')
        self.peak_hold  = bool(saved.get('peak_hold', True))
        self.scope_mode = saved.get('scope_mode', 'IF')
        self.peak_mode  = saved.get('peak_mode', 'frequent')
        self.rds_scale  = saved.get('rds_scale', 'standard')
        self.glow       = saved.get('glow', GLOW_DEFAULT)
        self.sync_audio = bool(saved.get('sync_audio', True))
        self.am_nrsc    = bool(saved.get('am_nrsc', False))
        self.am_wide    = bool(saved.get('am_wide', False))
        self.am_stereo  = saved.get('am_stereo', 'auto')
        self.am_ib      = saved.get('am_ib', True)
        self.am_nb      = saved.get('am_nb', 'off')
        self.am_normal_hz = saved.get('am_normal_hz', 8000)
        self.am_auto_width = bool(saved.get('am_auto_width', True))
        self.colors     = dict(saved.get('colors', {}))
        self.meter_view = {'AM': 'Q', 'FM': 'Q'}       # per band: 'S' meter or signal 'Q'uality
        self.meter_view.update(saved.get('meter_view', {}))
        self.smeter_cal = dict(saved.get('smeter_cal', {}))   # input path -> {'db', 'mhz'}
        self.freeze     = False
        self.lmr_gain   = lmr_gain
        self._backend_args = dict(lmr_gain=lmr_gain, log_path=log_path, record_iq=record_iq)
        self._run_mode = None                           # (band, AM source, FM device args) running now

        self.tb = None
        self.running, self._radio_run, self._restart_req = True, True, False
        self._power_job = None
        self._status = 'Connecting...'
        self._error_count, self._MAX_ERRORS = 0, 5

        now = time.monotonic()
        self._tuned_t = self._tb_t = now
        self._lamp_until = now + LAMP_TEST_S if self.power_on else 0.0
        self._seek_state, self._seek_job = None, None
        self._hold_job = None
        self._entry, self._entry_job = None, None
        self._memory_until = 0.0
        self._last_ch = None
        self._flash = None
        self._q_on = False
        self._sig, self._sig_t = 0.0, now
        self._sm, self._sm_t = 0.0, now                  # the S-meter's bar, smoothed
        self._cal_label, self._cal_msg = None, None     # Settings > S-meter: status, last result
        self._shown = self._mshown = None
        self._rt_text, self._rt_pos, self._rt_t = '', 0, 0.0
        self._direct, self._direct_t, self._dm_msg = None, 0.0, None   # direct entry; a short message
        self._lag, self._lag_tb, self._lag_t = None, None, now     # (meters, front end) seconds behind
        self._rx_line = DelayLine()                                 # tuner indicators, as measured
        self._mon_q, self._mon_last = deque(), None                 # monitor readings, waiting for the audio

        self.mon = MonitorLogic(self.meter_mode, self.peak_hold)
        self._mon_t = now
        self._mon_settle_until = now + 1.0
        self._mon_reset_req = False
        self._meters_missing = False
        self._mpx_stats, self._mpx_t = {}, 0.0
        self._ifs, self._echo, self._echo_t = None, None, 0.0
        self._mpx_avg = self._am_if_avg = None
        self._errors = set()
        self._adv_visible = False
        self._slider_auto_val = None
        self._lag_label = None

        use_nimbus_in_tk(self.root)
        self._build_ui()
        self._build_advanced()
        self._build_settings()
        threading.Thread(target=self._run_backend_loop, daemon=True).start()
        self.root.after(UI_TICK_MS, self._tick)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    @staticmethod
    def _read_json(paths):
        for p in paths:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (OSError, ValueError):
                continue
        return {}

    def _load_delco_dsp(self):
        """First-run defaults from the Delco's settings file (read only)."""
        raw, out = self._read_json(DELCO_SETTINGS), {}
        for key, name in (('stereo_blend', 'blend'), ('hi_cut', 'hicut'), ('dip_hicut', 'dip_hicut'),
                          ('noise_reduction', 'noise_red'), ('agc', 'agc')):
            if isinstance(raw.get(key), bool):
                out[name] = raw[key]
        if raw.get('eq_mode') in ('off', 'on', 'auto'):
            out['eq_mode'] = raw['eq_mode']
        if raw.get('if_mode') in ('auto', 'wide', 'narrow'):
            out['if_mode'] = raw['if_mode']
        g = raw.get('gain_db')
        if isinstance(g, (int, float)) and not isinstance(g, bool) and 0 <= g <= 50:
            out['gain'] = float(g)
        if isinstance(raw.get('pi_calls'), dict):
            out['pi_calls'] = self._clean_calls(raw['pi_calls'])
        return out

    @staticmethod
    def _clean_calls(calls):
        return {k.upper(): v.upper() for k, v in calls.items()
                if isinstance(k, str) and isinstance(v, str)
                and re.fullmatch(r'[0-9A-Fa-f]{4}', k) and re.fullmatch(r'[A-Za-z]{3,4}', v)}

    def _seed_channels(self):
        """First run: the Delco's FM presets in 1-4 and AM presets in 5-8."""
        ch = [None] * N_CHANNELS
        delco = self._read_json(DELCO_SETTINGS)
        fm = delco.get('presets_mhz') or [90.1, 90.5, 94.5, 96.1]
        am = delco.get('am_presets_khz') or [680, 750, 1380, 1690]
        for i, v in enumerate(fm[:4]):
            if isinstance(v, (int, float)) and FM_MIN / 1e6 <= v <= FM_MAX / 1e6:
                ch[i] = {'band': 'FM', 'freq': self._snap_in('FM', v * 1e6)}
        for i, v in enumerate(am[:4]):
            if isinstance(v, (int, float)) and AM_MIN / 1e3 <= v <= AM_MAX / 1e3:
                ch[4 + i] = {'band': 'AM', 'freq': self._snap_in('AM', v * 1e3)}
        return ch

    def _load_settings(self):
        raw = self._read_json([os.path.join(_ASSET_DIR, SETTINGS_NAME), SETTINGS_FALLBACK])
        out = {}
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
        self.override = raw.get('override') is True
        if raw.get('band') in ('FM', 'AM'):
            out['band'] = raw['band']
        v = raw.get('last_fm_mhz')
        if num(v) and RTL_RANGE[0] / 1e6 <= v <= RTL_RANGE[1] / 1e6:
            out['fm_freq'] = v * 1e6
        v = raw.get('last_am_khz')
        if num(v) and RTL_RANGE[0] / 1e3 <= v <= RTL_RANGE[1] / 1e3:
            out['am_freq'] = v * 1e3
        chans = raw.get('channels')
        if isinstance(chans, list) and len(chans) == N_CHANNELS:
            out['channels'] = [self._parse_channel(c) for c in chans]
        for key in ('fm_mono', 'power', 'agc', 'monitor', 'peak_hold'):
            if isinstance(raw.get(key), bool):
                out[key] = raw[key]
        for key, name in (('stereo_blend', 'blend'), ('hi_cut', 'hicut'), ('dip_hicut', 'dip_hicut'),
                          ('noise_reduction', 'noise_red')):
            if isinstance(raw.get(key), bool):
                out[name] = raw[key]
        if raw.get('eq_mode') in ('off', 'on', 'auto'):
            out['eq_mode'] = raw['eq_mode']
        if raw.get('if_mode') in ('auto', 'wide', 'narrow'):
            out['if_mode'] = raw['if_mode']
        if raw.get('meter_mode') in ('ppm', 'vu'):
            out['meter_mode'] = raw['meter_mode']
        if raw.get('scope_mode') in ('IF', 'MPX', 'ECHO'):
            out['scope_mode'] = raw['scope_mode']
        if raw.get('peak_mode') in ('frequent', 'highest'):
            out['peak_mode'] = raw['peak_mode']
        v = raw.get('glow')
        if num(v) and 0 <= v <= 1.5:
            out['glow'] = float(v)
        if isinstance(raw.get('sync_audio'), bool):
            out['sync_audio'] = raw['sync_audio']
        if isinstance(raw.get('am_nrsc'), bool):
            out['am_nrsc'] = raw['am_nrsc']
        if isinstance(raw.get('am_wide'), bool):
            out['am_wide'] = raw['am_wide']
        if raw.get('am_stereo') in ('auto', 'cquam', 'kahn'):
            out['am_stereo'] = raw['am_stereo']
        if isinstance(raw.get('am_ib'), bool):
            out['am_ib'] = raw['am_ib']
        if raw.get('am_nb') in ('off', 'low', 'med', 'high'):
            out['am_nb'] = raw['am_nb']
        if raw.get('am_normal_hz') in AM_WIDTHS:
            out['am_normal_hz'] = raw['am_normal_hz']
        elif raw.get('am_normal_hz') == 6000:              # no longer offered: the next one down
            out['am_normal_hz'] = 5000
        if isinstance(raw.get('am_auto_width'), bool):
            out['am_auto_width'] = raw['am_auto_width']
        if raw.get('rds_scale') in ('standard', 'stereo_tool'):
            out['rds_scale'] = raw['rds_scale']
        if isinstance(raw.get('meter_view'), dict):
            out['meter_view'] = {b: v for b, v in raw['meter_view'].items() if b in BANDS and v in ('S', 'Q')}
        if isinstance(raw.get('smeter_cal'), dict):
            out['smeter_cal'] = {p: {'db': float(c['db']), 'mhz': float(c['mhz'])}
                                 for p, c in raw['smeter_cal'].items()
                                 if p in smeter.PATHS and isinstance(c, dict) and num(c.get('db'))
                                 and -150 <= c['db'] <= 150 and num(c.get('mhz'))}
        if isinstance(raw.get('colors'), dict):
            out['colors'] = {k: v.lower() for k, v in raw['colors'].items()
                             if k in COLOR_DEFAULTS and isinstance(v, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', v)}
        v = raw.get('output_db')
        if num(v) and -40 <= v <= 6:
            out['output_db'] = float(v)
        v = raw.get('gain_db')
        if num(v) and 0 <= v <= 50:
            out['gain'] = float(v)
        if isinstance(raw.get('badge'), str):
            out['badge'] = raw['badge'][:24]
        if isinstance(raw.get('pi_calls'), dict):
            out['pi_calls'] = self._clean_calls(raw['pi_calls'])
        return out

    def _parse_channel(self, c):
        if not isinstance(c, str):
            return None
        parts = c.split()
        if len(parts) != 2 or parts[0] not in BANDS:
            return None
        try:
            v = float(parts[1])
        except ValueError:
            return None
        hz = v * (1e6 if parts[0] == 'FM' else 1e3)
        lo, hi = RTL_RANGE
        if not lo <= hz <= hi:
            return None
        return {'band': parts[0], 'freq': round(hz, -3)}   # exactly as stored (to 1 kHz)

    @staticmethod
    def _channel_text(c):
        if not c:
            return None
        if c['band'] == 'FM':
            return 'FM ' + f"{c['freq'] / 1e6:.4f}".rstrip('0').rstrip('.')
        return f"AM {round(c['freq'] / 1e3)}"

    def _save_settings(self):
        fm = self.freq if self.band == 'FM' else self.fm_freq
        am = self.freq if self.band == 'AM' else self.am_freq
        data = {
            'band': self.band,
            'last_fm_mhz': round(fm / 1e6, 4),
            'override': bool(self.override),
            'last_am_khz': round(am / 1e3),
            'channels': [self._channel_text(c) for c in self.channels],
            'fm_mono': bool(self.fm_mono),
            'power': bool(self.power_on),
            'output_db': float(self.output_db),
            'agc': bool(self.agc_on),
            'gain_db': round(float(self.rf_gain), 1),
            'stereo_blend': bool(self.blend_on),
            'hi_cut': bool(self.hicut_on),
            'eq_mode': self.eq_mode,
            'if_mode': self.if_mode,
            'dip_hicut': bool(self.dip_hicut),
            'noise_reduction': bool(self.noise_red),
            'badge': self.badge,
            'monitor': bool(self.monitor_on),
            'meter_mode': self.meter_mode,
            'peak_hold': bool(self.peak_hold),
            'scope_mode': self.scope_mode,
            'peak_mode': self.peak_mode,
            'rds_scale': self.rds_scale,
            'glow': round(float(self.glow), 2),
            'sync_audio': bool(self.sync_audio),
            'am_nrsc': bool(self.am_nrsc),
            'am_wide': bool(self.am_wide),
            'am_stereo': self.am_stereo,
            'am_ib': bool(self.am_ib),
            'am_nb': self.am_nb,
            'am_normal_hz': int(self.am_normal_hz),
            'am_auto_width': bool(self.am_auto_width),
            'colors': dict(sorted(self.colors.items())),
            'meter_view': dict(sorted(self.meter_view.items())),
            'smeter_cal': {p: dict(c) for p, c in sorted(self.smeter_cal.items())},
            'pi_calls': dict(sorted(self.pi_calls.items())),
        }
        for path in (os.path.join(_ASSET_DIR, SETTINGS_NAME), SETTINGS_FALLBACK):
            tmp = path + '.tmp'
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, path)
                return True
            except OSError:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        self._set_status('Could not save settings')
        return False

    # ------------------------------------------------------------------
    # Back end lifecycle (same pattern as the Delco)
    # ------------------------------------------------------------------

    def _run_backend_loop(self):
        while self.running:
            if not self._radio_run:
                time.sleep(0.1)
                continue
            self._error_count = 0
            self._set_status('Connecting...')
            try:
                self._restart_req = False
                am_src, fm_args = self._source_for(self.band, self.freq)
                self._run_mode = (self.band, am_src, fm_args)
                if self.band == 'AM':
                    self.tb = AMReceiver(freq=self.freq, rf_gain=self.rf_gain, agc=self.agc_on,
                                         am_source=am_src, upconverter_hz=self.upconverter_hz,
                                         audio_device=self.audio_device)
                else:
                    self.tb = FMStereoReceiver(freq=self.freq, rf_gain=self.rf_gain, agc=self.agc_on,
                                               device_args=fm_args, audio_device=self.audio_device,
                                               **self._backend_args)
                tb = self.tb
                g = 10 ** (self.output_db / 20)
                tb.set_output_gain(g, g)
                tb.set_tone(0, 0)                          # flat, like a component tuner
                self._apply_dsp(tb)
                self._apply_mono()
                self._radio_mute()
                tb.start()
                self._tb_t = self._tuned_t = time.monotonic()
                self._mon_settle_until = self._tb_t + 1.0     # its filters' start-up burst isn't program
                self._mon_reset_req = True
                self._set_status('Ready')
                self._blend_loop()
            except Exception as e:
                self._set_status(f'Error: {e}')
            finally:
                tb, self.tb = self.tb, None
                if tb is not None:
                    self.rf_gain = tb.gain_db
                    self.agc_on = tb.agc_enabled
                    try:
                        tb.stop()
                        tb.wait()
                        tb.close()
                    except Exception:
                        pass
                tb = None                        # drop the last reference so the dongle is
                gc.collect()                     # released before the next attempt opens it
            if not self.running:
                break
            if not self._radio_run:
                self._set_status('Off')
                continue
            if self._restart_req:
                continue
            self._set_status('Dongle lost, retrying in 3 s...')
            for _ in range(30):
                if not self.running:
                    break
                time.sleep(0.1)

    def _blend_loop(self):
        while self.running and self._radio_run and not self._restart_req:
            try:
                self.tb.update_blend()
                self._error_count = 0
            except Exception as e:
                self._error_count += 1
                if self._error_count >= self._MAX_ERRORS:
                    print(f'Receiver stopped: {type(e).__name__}: {e}')   # why, in the terminal
                    if not isinstance(e, RuntimeError):
                        traceback.print_exc()
                    return
            time.sleep(0.02)

    def _set_status(self, msg):
        self._status = msg
        if msg not in ('Ready', 'Off', 'Connecting...'):
            print(msg)
        try:
            self.root.after(0, self._update_title)
        except Exception:
            pass

    def _update_title(self):
        t = self.badge or 'Stereo Tuner'
        if self._status not in ('Ready', 'Off', ''):
            t += f'  ({self._status})'
        self.root.title(t)
        if getattr(self, 'status_var', None) is not None:
            self.status_var.set(self._status)

    def _report(self, where, e):
        key = (where, type(e).__name__)
        if key not in self._errors:
            self._errors.add(key)
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Audio and DSP state
    # ------------------------------------------------------------------

    def _moving(self):
        return bool(self._seek_state)

    def _radio_mute(self):
        tb = self.tb
        if tb is not None:
            tb.set_mute((not self.power_on) or self._moving())

    def _apply_mono(self):
        """Mono: FM zeroes the L-R gain; AM ignores the C-QUAM stereo. STEREO stays dark."""
        tb = self.tb
        if tb is not None and tb.has('mono'):
            tb.set_force_mono(self.fm_mono)

    def _apply_output(self):
        if self.tb is not None:
            g = 10 ** (self.output_db / 20)
            self.tb.set_output_gain(g, g)

    def _apply_dsp(self, tb):
        """Every setting the receiver has a feature for (Receiver.FEATURES)."""
        if tb.has('fm_helpers'):
            tb.set_enhancements(blend=self.blend_on, hicut=self.hicut_on)
        if tb.has('echo'):
            tb.set_eq_mode(self.eq_mode)
        if tb.has('am_width'):
            tb.set_auto_ceiling(self.am_normal_hz)
            tb.set_if_mode('auto' if self.am_auto_width else 'fixed')
            tb.set_wide(self.am_wide)
        if tb.has('fm_bandwidth'):
            tb.set_if_mode(self.if_mode)
        if tb.has('fm_helpers'):
            tb.set_dip_hicut(self.dip_hicut)
            tb.set_noise_reduction(self.noise_red)
        if tb.has('nrsc'):
            tb.set_nrsc_deemphasis(self.am_nrsc)
        if tb.has('am_stereo'):
            tb.set_stereo_system(self.am_stereo)
        if tb.has('blankers'):
            tb.set_blankers(impulse=self.am_ib, noise=self.am_nb)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        s = self.s
        W, H = round(BASE_W * s), round(BASE_H * s)
        self._update_title()
        self.root.resizable(False, False)
        self.root.configure(bg='#161616')
        self.canvas = c = tk.Canvas(self.root, width=W, height=H, highlightthickness=0, bd=0)
        c.pack()
        self.mcanvas = mc = tk.Canvas(self.root, width=W, height=H, highlightthickness=0, bd=0)
        if self.monitor_on:
            mc.pack(pady=(round(RACK_GAP * s), 0))

        self._plate_item = c.create_image(0, 0, anchor='nw')
        self._mplate_item = mc.create_image(0, 0, anchor='nw')
        self._render_plates()

        self.vfd = TunerVfd(s, self._colors_for('tuner'))
        self.vfd_item = c.create_image(round(VFD_BOX[0] * s), round(VFD_BOX[1] * s), anchor='nw')
        for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            c.bind(seq, self._wheel)
        c.tag_bind(self.vfd_item, '<ButtonRelease-1>', self._vfd_click)

        self.mvfd = MonitorVfd(s, self._colors_for('monitor'))
        self.vfd.glow = self.mvfd.glow = self.glow
        self.mvfd_item = mc.create_image(round(VFD_BOX[0] * s), round(VFD_BOX[1] * s), anchor='nw')
        self.crt = Crt(s)
        self.crt.set_colors(self._colors_for('screen'))
        self.crt_item = mc.create_image(round(CRT_BOX[0] * s), round(CRT_BOX[1] * s), anchor='nw')

        self.keys = []
        K = lambda cv, *a, **kw: self.keys.append(Key(cv, *a, s=s, **kw)) or self.keys[-1]
        K(c, POWER_KEY, on_press=self._toggle_power, latched=lambda: self.power_on)
        K(c, WIDE_KEY, legend='Wide', size=11, on_press=self._toggle_wide, latched=lambda: self.am_wide)
        for i, n in enumerate((1, 2, 3, 4, 5, 6, 7, 8, 9, 0)):
            row, col = divmod(i, 5)
            x0 = PAD_X + col * (PAD_W + PAD_GAP)
            y0, y1 = PAD_ROWS[row]
            K(c, (x0, y0, x0 + PAD_W, y1), legend=str(n), size=15, on_press=lambda n=n: self._digit(n))
        K(c, TUNE_DOWN, legend='Down', icon='left', size=12,
          on_press=lambda: self._tune_press(-1), on_release=self._tune_release)
        K(c, TUNE_UP, legend='Up', icon='right', size=12,
          on_press=lambda: self._tune_press(+1), on_release=self._tune_release)
        K(c, FM_KEY, legend='FM', size=11.5, on_press=lambda: self._band('FM'))
        K(c, AM_KEY, legend='AM', size=11.5, on_press=lambda: self._band('AM'))
        K(c, MEM_KEY, legend='Memory', size=11, on_press=self._memory)
        K(c, MONO_KEY, legend='Mono', size=11, on_press=self._toggle_mono, latched=lambda: self.fm_mono)
        self.hints = [Hint(c, HINTS[0][1], s, 'ADV', self._toggle_advanced),
                      Hint(c, HINTS[1][1], s, 'MON', self._toggle_monitor),
                      Hint(c, HINTS[2][1], s, 'SET', self._toggle_settings),
                      Hint(c, HINTS[3][1], s, 'FREQ', self._direct_toggle),
                      Hint(c, HINTS[4][1], s, 'METER', self._meter_toggle)]

        K(mc, RESET_KEY, legend='Reset', size=11, on_press=self._mon_reset)
        K(mc, PPM_KEY, legend='PPM', size=11, on_press=lambda: self._set_meter_mode('ppm'),
          latched=lambda: self.meter_mode == 'ppm')
        K(mc, VU_KEY, legend='VU', size=11, on_press=lambda: self._set_meter_mode('vu'),
          latched=lambda: self.meter_mode == 'vu')
        K(mc, HOLD_KEY, legend='Hold', size=11, on_press=self._toggle_hold, latched=lambda: self.peak_hold)
        for box, mode, legend in ((IF_KEY, 'IF', 'IF'), (MPX_KEY, 'MPX', 'MPX'), (ECHO_KEY, 'ECHO', 'Echo')):
            K(mc, box, legend=legend, size=11, on_press=lambda m=mode: self._set_scope(m),
              latched=lambda m=mode: self.scope_mode == m)
        K(mc, FREEZE_KEY, legend='Freeze', size=11, on_press=self._toggle_freeze,
          latched=lambda: self.freeze)

        r = self.root
        r.bind('<Return>', lambda e: self._direct_toggle())
        r.bind('<KP_Enter>', lambda e: self._direct_toggle())
        r.bind('<Escape>', lambda e: self._direct_cancel())
        r.bind('<BackSpace>', lambda e: self._direct_back())
        for seq in ('<period>', '<KP_Decimal>'):
            r.bind(seq, lambda e: self._direct_key('.'))
        r.bind('<Left>', lambda e: self._step(-1, manual=True))
        r.bind('<Right>', lambda e: self._step(+1, manual=True))
        r.bind('<Shift-Left>', lambda e: self._search_key(-1))
        r.bind('<Shift-Right>', lambda e: self._search_key(+1))
        for n in range(10):
            r.bind(str(n), lambda e, n=n: self._digit(n))
        for ch, fn in (('m', self._memory), ('f', lambda: self._band('FM')),
                       ('a', lambda: self._band('AM')), ('o', self._toggle_mono),
                       ('w', self._toggle_wide), ('p', self._toggle_power),
                       ('d', self._direct_toggle), ('i', self._addon_window),
                       ('s', self._meter_toggle)):
            r.bind(ch, lambda e, fn=fn: fn())
            r.bind(ch.upper(), lambda e, fn=fn: fn())
        self._refresh_vfd(force=True)

    def _colors_for(self, panel):
        return {k: v for k, v in self.colors.items() if COLOR_PANEL.get(k) == panel}

    def _render_plates(self):
        s = self.s
        W, H = round(BASE_W * s), round(BASE_H * s)
        for kind, item, canvas in (('tuner', self._plate_item, self.canvas),
                                   ('monitor', self._mplate_item, self.mcanvas)):
            plate = brushed_plate(W, H, seed=11 if kind == 'tuner' else 23).convert('RGBA')
            art = Image.new('RGBA', (round(BASE_W * s * SS), round(BASE_H * s * SS)), (0, 0, 0, 0))
            d = ImageDraw.Draw(art)
            if kind == 'tuner':
                self._draw_tuner_panel(d)
            else:
                self._draw_monitor_panel(d)
            plate.alpha_composite(art.resize((W, H), Image.LANCZOS))
            ph = ImageTk.PhotoImage(plate)
            setattr(self, f'_{kind}_plate_img', ph)
            canvas.itemconfig(item, image=ph)
            canvas.tag_lower(item)

    def _draw_bezel(self, d, box):
        k = self.s * SS
        x0, y0, x1, y1 = box
        P = lambda *v: [x * k for x in v]
        _rrect(d, P(x0 - 1, y0 + 1, x1 + 1, y1 + 2), 7 * k, LIP + (255,))
        _rrect(d, P(x0 - 1, y0 - 1, x1 + 1, y1 + 1), 7 * k, (58, 59, 62, 255))
        _rrect(d, P(x0, y0, x1, y1), 6 * k, (6, 8, 8, 255))

    def _draw_wells(self, d, boxes):
        k = self.s * SS
        P = lambda *v: [x * k for x in v]
        for bx0, by0, bx1, by1 in boxes:
            _rrect(d, P(bx0 - 2.5, by0 - 1.5, bx1 + 2.5, by1 + 3.5), 3.5 * k, LIP + (255,))
            _rrect(d, P(bx0 - 2.5, by0 - 2.5, bx1 + 2.5, by1 + 2.5), 3.5 * k, WELL + (255,))

    def _nameplate(self, d, text):
        k = self.s * SS
        px = 23.0
        f = font('badge', px * k)
        while px > 12 and text_width(text, f, 1.4 * k) > 160 * k:
            px -= 1
            f = font('badge', px * k)
        draw_text(d, 30 * k, 50 * k, text, f, PRINT, tracking=1.4 * k)

    def _draw_tuner_panel(self, d):
        k = self.s * SS
        lab, small = font('label', 11 * k), font('label', 9.5 * k)
        self._nameplate(d, self.badge)
        self._draw_bezel(d, WIN)
        boxes = [POWER_KEY, WIDE_KEY, TUNE_DOWN, TUNE_UP, FM_KEY, AM_KEY, MEM_KEY, MONO_KEY]
        for i in range(10):
            row, col = divmod(i, 5)
            xa = PAD_X + col * (PAD_W + PAD_GAP)
            boxes.append((xa, PAD_ROWS[row][0], xa + PAD_W, PAD_ROWS[row][1]))
        self._draw_wells(d, boxes)
        draw_text(d, POWER_KEY[0] * k, LOWER_LABEL_Y * k, 'Power', lab, PRINT)
        draw_text(d, WIDE_KEY[0] * k, LOWER_LABEL_Y * k, 'AM filter', lab, PRINT)
        draw_text(d, PAD_X * k, LABEL_Y * k, 'Preset channels 1-24', lab, PRINT)
        draw_text(d, (PAD_X + 5 * PAD_W + 4 * PAD_GAP) * k, LABEL_Y * k,
                  'To store: Memory, then channel', small, PRINT_SOFT, align='r')
        draw_text(d, TUNE_DOWN[0] * k, LABEL_Y * k, 'Tuning', lab, PRINT)
        draw_text(d, TUNE_UP[2] * k, LABEL_Y * k, 'Tap for manual, hold for auto', small,
                  PRINT_SOFT, align='r')
        draw_text(d, FM_KEY[0] * k, LOWER_LABEL_Y * k, 'Band', lab, PRINT)
        draw_text(d, MONO_KEY[0] * k, LOWER_LABEL_Y * k, 'Stereo', lab, PRINT)

    def _draw_monitor_panel(self, d):
        k = self.s * SS
        lab, small = font('label', 11 * k), font('label', 9.5 * k)
        self._nameplate(d, 'Monitor')
        self._draw_bezel(d, WIN)
        self._draw_bezel(d, CRT_WIN)
        self._draw_wells(d, [RESET_KEY, PPM_KEY, VU_KEY, HOLD_KEY, IF_KEY, MPX_KEY, ECHO_KEY, FREEZE_KEY])
        draw_text(d, RESET_KEY[0] * k, LOWER_LABEL_Y * k, 'Loudness', lab, PRINT)
        draw_text(d, PPM_KEY[0] * k, LABEL_Y * k, 'Meter', lab, PRINT)
        draw_text(d, HOLD_KEY[0] * k, LABEL_Y * k, 'Peak', lab, PRINT)
        draw_text(d, IF_KEY[0] * k, LOWER_LABEL_Y * k, 'Screen', lab, PRINT)
        draw_text(d, FREEZE_KEY[2] * k, LOWER_LABEL_Y * k, 'Hold the trace', small, PRINT_SOFT,
                  align='r')

    def _wheel(self, e):
        x0, y0, x1, y1 = (v * self.s for v in WIN)
        if not (x0 <= e.x <= x1 and y0 <= e.y <= y1):
            return
        if getattr(e, 'num', None) == 4 or getattr(e, 'delta', 0) > 0:
            self._step(+1, manual=True)
        else:
            self._step(-1, manual=True)

    def _refresh_keys(self):
        for key in self.keys:
            key.refresh()

    # ------------------------------------------------------------------
    # Power, band, mono
    # ------------------------------------------------------------------

    def _toggle_power(self):
        self._seek_cancel()
        self._entry_cancel()
        self._memory_until = 0.0
        self.power_on = not self.power_on
        if self.power_on:
            if self._power_job:
                self.root.after_cancel(self._power_job)
                self._power_job = None
            self._radio_run = True
            now = time.monotonic()
            self._lamp_until = now + LAMP_TEST_S
            self._tuned_t = now
            self._mon_retuned()
        else:
            if self._power_job:
                self.root.after_cancel(self._power_job)
            self._power_job = self.root.after(int(POWER_SLEEP_S * 1000), self._radio_sleep)
            self.freeze = False
        self._radio_mute()
        self._refresh_keys()
        self._save_settings()

    def _radio_sleep(self):
        self._power_job = None
        if not self.power_on:
            self._radio_run = False

    def _band(self, band, freq=None):
        if not self.power_on:
            return
        self._memory_until = 0.0
        if band == 'AM' and AMReceiver is None:
            self._set_status(f'AM unavailable: {_AM_ERR}')
            return
        if band == self.band:
            if freq is not None:
                self._tune_to(freq)
            return
        self._seek_cancel()
        if self.band == 'FM':
            self.fm_freq = self.freq
        else:
            self.am_freq = self.freq
        self.band = band
        self.freq = self._snap(freq if freq is not None else
                               (self.am_freq if band == 'AM' else self.fm_freq))
        self._tuned_t = time.monotonic()
        self._q_on = False
        self._mon_retuned()
        self._restart_req = True
        if self._adv_visible:
            self._spec_reset()
        self._save_settings()

    def _toggle_wide(self):
        """AM filter Wide (latching). The setting is kept on FM and applies on AM."""
        if not self.power_on:
            return
        self.am_wide = not self.am_wide
        if self.tb is not None and self.tb.has('am_width'):
            self.tb.set_wide(self.am_wide)
        self._refresh_keys()
        self._save_settings()

    def _toggle_mono(self):
        if not self.power_on:
            return
        self.fm_mono = not self.fm_mono
        self._apply_mono()
        self._refresh_keys()
        self._save_settings()

    # ------------------------------------------------------------------
    # Tuning
    # ------------------------------------------------------------------

    @staticmethod
    def _snap_in(band, freq):
        lo, hi, st = BANDS[band]
        n = int(round((hi - lo) / st)) + 1
        idx = min(max(int(round((freq - lo) / st)), 0), n - 1)
        return round(lo + idx * st, -3)

    def _limits(self, band):
        """Where the dongle can tune for this band."""
        if self.am_source in ('direct_q', 'native'):
            return RTL_RANGE                            # direct sampling / the V4's HF path below 28.8 MHz
        return (RTL_RANGE[0], 30e6) if band == 'AM' else (TUNER_MIN, RTL_RANGE[1])

    def _source_for(self, band, freq):
        """(AM source, FM device args) for a frequency: direct sampling below the
        crossover, the quadrature tuner above, when the dongle has both."""
        if self.am_source != 'direct_q':
            return self.am_source, FM_DEVICE_ARGS
        if band == 'AM':
            return ('direct_q' if freq < XOVER_HZ else 'native'), FM_DEVICE_ARGS
        return 'direct_q', FM_DEVICE_ARGS + (',direct_samp=2' if freq < XOVER_HZ else '')

    def _fit(self, band, freq):
        """A frequency the receiver will take: the band's raster within the band,
        or with Band limits: Override, anywhere the tuner reaches (to 1 kHz)."""
        if self.override:
            lo, hi = self._limits(band)
            return round(min(max(freq, lo), hi), -3)
        return self._snap_in(band, freq)

    def _snap(self, freq):
        return self._fit(self.band, freq)

    def _in_band(self, freq=None):
        lo, hi, _ = BANDS[self.band]
        f = self.freq if freq is None else freq
        return lo - 1 <= f <= hi + 1

    def _step_hz(self):
        return BANDS[self.band][2]

    def _nchan(self):
        lo, hi, st = BANDS[self.band]
        return int(round((hi - lo) / st)) + 1

    def _wrap(self, freq):
        if self.override:                              # past the band edge instead of round to the other end
            lo, hi = self._limits(self.band)
            return round(min(max(freq, lo), hi), -3)
        lo, _, st = BANDS[self.band]
        idx = int(round((freq - lo) / st)) % self._nchan()
        return round(lo + idx * st, -3)

    def _tune_to(self, freq):
        self.freq = self._snap(freq)
        self._tuned_t = time.monotonic()
        self._q_on = False
        self._mon_retuned()
        mode = (self.band,) + self._source_for(self.band, self.freq)
        if self._run_mode is not None and mode != self._run_mode:
            below = self.freq < XOVER_HZ                 # across 28.8 MHz: reopen the dongle
            self._message('DIRECT SAMPLING' if below else 'QUADRATURE TUNER')
            self._restart_req = True
            return
        if self.tb:
            self.tb.set_freq(self.freq)

    def _step(self, direction, manual=False):
        if not self.power_on:
            return
        if manual:
            self._seek_cancel()
            self._entry_cancel()
            self._memory_until = 0.0
        self._tune_to(self._wrap(self.freq + direction * self._step_hz()))

    def _tune_press(self, direction):
        if not self.power_on:
            return
        self._entry_cancel()
        self._memory_until = 0.0
        if self._seek_state:
            self._seek_cancel()
            return
        self._step(direction)
        self._hold_job = self.root.after(int(HOLD_TO_SEARCH_S * 1000),
                                         lambda: self._hold_fire(direction))

    def _tune_release(self):
        if self._hold_job:
            self.root.after_cancel(self._hold_job)
            self._hold_job = None

    def _hold_fire(self, direction):
        """Held: search, starting by re-checking the channel the tap moved to."""
        self._hold_job = None
        self.freq = self._wrap(self.freq - direction * self._step_hz())
        self._search(direction)

    def _search_key(self, direction):
        if self._seek_state:
            self._seek_cancel()
        else:
            self._search(direction)

    def _search(self, direction):
        if not self.power_on or not self.tb:
            return
        self._memory_until = 0.0
        self._seek_state = dict(dir=direction, start=self.freq, steps=0, t0=0.0)
        self._radio_mute()
        self._seek_next()

    def _seek_next(self):
        st = self._seek_state
        if not st or not self.tb:
            self._seek_cancel()
            return
        st['steps'] += 1
        if st['steps'] > self._nchan():
            self._seek_finish(st['start'])
            return
        self._tune_to(self._wrap(self.freq + st['dir'] * self._step_hz()))
        st['t0'] = time.monotonic()
        st.pop('verify', None)
        self._seek_job = self.root.after(60, self._seek_check)

    def _seek_check(self):
        self._seek_job = None
        st = self._seek_state
        if not st or not self.tb:
            self._seek_cancel()
            return
        vals = self.tb.cnr_since_tune()
        waited = time.monotonic() - st['t0']
        if len(vals) < SEEK_QUICK_N:
            if waited > SEEK_TIMEOUT_S:
                self._seek_next()
            else:
                self._seek_job = self.root.after(20, self._seek_check)
            return
        q = float(np.median(vals))
        thr = getattr(self.tb, 'SEEK_MIN_DB', SEEK_MIN_CNR_DB)
        if len(vals) < SEEK_CONFIRM_N:
            if q < thr - 3.0:
                self._seek_next()
            else:
                self._seek_job = self.root.after(20, self._seek_check)
            return
        if q < thr:
            self._seek_next()
            return
        if 'verify' not in st:
            st['verify'] = len(vals)
        fresh = vals[st['verify']:]
        if len(fresh) < SEEK_CONFIRM_N:
            if waited > SEEK_TIMEOUT_S + 0.5:
                self._seek_next()
            else:
                self._seek_job = self.root.after(20, self._seek_check)
            return
        if float(np.median(fresh)) >= thr:
            self._seek_finish(None)
        else:
            self._seek_next()

    def _seek_finish(self, freq):
        self._seek_state = None
        if self._seek_job:
            self.root.after_cancel(self._seek_job)
            self._seek_job = None
        if freq is not None:
            self._tune_to(freq)
        else:
            self._mon_retuned()                    # measure the station it stopped on fresh
        self._radio_mute()
        self._save_settings()

    def _seek_cancel(self):
        if self._seek_state or self._seek_job:
            self._seek_finish(None)

    # ------------------------------------------------------------------
    # Preset channels
    # ------------------------------------------------------------------

    def _memory(self):
        if not self.power_on:
            return
        self._seek_cancel()
        self._entry_cancel()
        now = time.monotonic()
        self._memory_until = 0.0 if self._memory_until > now else now + MEMORY_WAIT_S

    def _digit(self, n):
        if not self.power_on:
            return
        if self._direct is not None:                   # typing a frequency, not a channel
            self._direct_key(str(n))
            return
        self._seek_cancel()
        e = self._entry
        if e is None:
            if n in (0, 1, 2):
                self._entry = n
                self._entry_job = self.root.after(int(ENTRY_WAIT_S * 1000), self._entry_timeout)
            else:
                self._channel(n)
            return
        self._entry_cancel()
        ch = e * 10 + n
        if 1 <= ch <= N_CHANNELS:
            self._channel(ch)
        else:
            self._digit(n)

    def _entry_timeout(self):
        self._entry_job = None
        e, self._entry = self._entry, None
        if e in (1, 2):
            self._channel(e)

    def _entry_cancel(self):
        if self._entry_job:
            self.root.after_cancel(self._entry_job)
            self._entry_job = None
        self._entry = None

    def _channel(self, n):
        now = time.monotonic()
        if self._memory_until > now:
            self._memory_until = 0.0
            self.channels[n - 1] = {'band': self.band, 'freq': self.freq}
            self._last_ch = n
            self._flash = (n, now + 1.2)
            self._save_settings()
            return
        c = self.channels[n - 1]
        if not c:
            self._flash = (n, now + 1.2)
            return
        if not self.override and not (BANDS[c['band']][0] - 1 <= c['freq'] <= BANDS[c['band']][1] + 1):
            self._message('OUT OF BAND')                # stored with the override on
            return
        self._last_ch = n
        if c['band'] != self.band:
            self._band(c['band'], c['freq'])
        else:
            self._tune_to(c['freq'])
        self._save_settings()

    def _channel_now(self):
        here = (self.band, round(self.freq))
        match = lambda c: c and (c['band'], round(c['freq'])) == here
        if self._last_ch and match(self.channels[self._last_ch - 1]):
            return self._last_ch
        for i, c in enumerate(self.channels):
            if match(c):
                return i + 1
        return None

    # ------------------------------------------------------------------
    # Tuner display
    # ------------------------------------------------------------------

    def _tuned(self, tb, now):
        if (tb is None or self._moving() or now - self._tuned_t < TUNED_SETTLE_S
                or now - self._tb_t < TUNED_SETTLE_S):
            self._q_on = False
            return False
        if self.band == 'AM' and not getattr(tb, 'locked', True):
            self._q_on = False
            return False
        q = getattr(tb, 'cnr_db', 0.0)
        thr = getattr(tb, 'SEEK_MIN_DB', SEEK_MIN_CNR_DB)
        if q >= thr:
            self._q_on = True
        elif q < thr - TUNED_HYST_DB:
            self._q_on = False
        return self._q_on

    def _signal_steps(self, tb, now):
        dt, self._sig_t = now - self._sig_t, now
        if tb is None or self._moving() or now - self._tuned_t < TUNED_SETTLE_S:
            target = 0.0
        else:
            lo, hi = SIG_RANGE[self.band]
            q = getattr(tb, 'cnr_db', 0.0)
            target = SIG_STEPS * min(1.0, max(0.0, (q - lo) / (hi - lo)))
        self._sig = target if target >= self._sig else max(target, self._sig - SIG_FALL_PER_S * dt)
        return int(round(self._sig))

    # ---- the S-meter
    def _input_path(self):
        """Which calibration readings use: direct sampling, or the tuner below / above 30 MHz."""
        band, am_src, fm_args = self._run_mode or ((self.band,) + self._source_for(self.band, self.freq))
        direct = am_src == 'direct_q' if band == 'AM' else 'direct_samp' in fm_args
        return smeter.path_for(direct, self.freq)

    def _smeter_reading(self, tb):
        """(dBm, uncalibrated, path, gain known) for the station now, or None."""
        if tb is None:
            return None
        dbfs, gain = tb.signal_level()
        path = self._input_path()
        if path == 'direct':
            gain = 0.0                                 # no tuner ahead of the converter
        cal = self.smeter_cal.get(path)
        dbm = smeter.to_dbm(dbfs, gain or 0.0, smeter.GUESS_CAL_DB if cal is None else cal['db'])
        return dbm, cal is None or gain is None, path, gain is not None

    def _smeter_steps(self, tb, now):
        """(bars lit, UNCAL) for the meter in S units: rises at once, falls like the quality bar."""
        dt, self._sm_t = now - self._sm_t, now
        r = self._smeter_reading(tb)
        settling = self._moving() or now - self._tuned_t < TUNED_SETTLE_S
        target = 0.0 if r is None or settling else smeter.segments(r[0], self.freq)
        self._sm = target if target >= self._sm else max(target, self._sm - SIG_FALL_PER_S * dt)
        return int(round(self._sm)), r is None or r[1]

    def _meter_toggle(self):
        """S, the METER button or a click on the meter: S units or signal quality, per band."""
        self.meter_view[self.band] = 'S' if self.meter_view.get(self.band, 'Q') == 'Q' else 'Q'
        self._save_settings()
        self._refresh_vfd(force=True)

    def _vfd_click(self, e):
        x, y = e.x / self.s - VFD_BOX[0], e.y / self.s - VFD_BOX[1]
        if SIG_X - 12 <= x <= SIG_X + SIG_STEPS * SIG_PITCH and 18 <= y <= SCALE_Y + 4:
            self._meter_toggle()

    def _smeter_calibrate(self, known_dbm):
        """Settings > S-meter: the station tuned now is known_dbm strong. Returns what happened."""
        tb, now = self.tb, time.monotonic()
        if tb is None or not self.power_on:
            return 'Turn the radio on and tune to the signal first.'
        if not self._tuned(tb, now):
            return 'Tune to the signal first (TUNED lit).'
        dbm, _, path, gain_known = self._smeter_reading(tb)
        if not gain_known:
            return "Turn the tuner's AGC off first: with it on, the gain is unknown."
        dbfs, gain = tb.signal_level()
        self.smeter_cal[path] = {'db': round(smeter.calibration_for(known_dbm, dbfs,
                                                                     0.0 if path == 'direct' else gain), 2),
                                 'mhz': round(self.freq / 1e6, 4)}
        self._save_settings()
        return f'Calibrated {smeter.PATHS[path]} at {as_text(self.freq, self._step_hz())}.'

    def _smeter_clear(self):
        path = self._input_path()
        self.smeter_cal.pop(path, None)
        self._save_settings()
        return f'Calibration for {smeter.PATHS[path]} cleared.'

    def _smeter_status(self, now):
        """The line under Settings > S-meter: the reading now, and this path's calibration."""
        if self._cal_msg is not None and now < self._cal_msg[1]:
            return self._cal_msg[0]
        r = self._smeter_reading(self.tb)
        if r is None:
            return ''
        dbm, _, path, gain_known = r
        cal = self.smeter_cal.get(path)
        where = smeter.PATHS[path]
        how = (f"calibrated at {as_text(cal['mhz'] * 1e6, 1e3)}" if cal else 'not calibrated')
        note = '' if gain_known else "; tuner AGC on, so the gain is unknown"
        return f'Now {dbm:.1f} dBm ({smeter.s_text(dbm, self.freq)}). {where[0].upper()}{where[1:]}: {how}{note}.'

    def _vfd_state(self):
        if not self.power_on:
            return None
        now = time.monotonic()
        blink = int(now * 3) % 2 == 0
        tb = self.tb
        big, dots, fine, unit = freq_digits(self.freq, self._step_hz())

        # What the receiver measures now, then shown as of when the audio you're
        # hearing was received (Settings > Panel > Sync to audio)
        rx = dict(stereo=tb is not None and not self._moving() and getattr(tb, 'stereo_indicator', False),
                  tuned=self._tuned(tb, now),
                  rds=(tb is not None and (tb.has('rds') or bool(tb.addons)) and not self._moving()
                       and getattr(tb, 'rds_synced', False)),
                  sig=self._signal_steps(tb, now), smeter=self._smeter_steps(tb, now),
                  text=self._rds_fields(tb, now))
        self._rx_line.push(now, rx)
        shown = self._rx_line.at(now - self._delays()[1]) or rx

        legends = {self.band}
        if self._moving():
            legends.add('AUTO')
        mono = self.fm_mono
        if mono:
            legends.add('MONO')
        if self.band == 'AM' and self.am_wide:
            legends.add('WIDE')
        if shown['stereo'] and not mono:
            legends.add('STEREO')
        if shown['tuned']:
            legends.add('TUNED')
        if shown['rds']:
            legends.add('RDS')
        if self._memory_until > now:
            if blink:
                legends.add('MEMORY')
        else:
            self._memory_until = 0.0

        def ch_chars(n):
            return (str(n // 10) if n >= 10 else ' ', str(n % 10))

        if self._entry is not None:
            ch = (' ', str(self._entry)) if blink else (' ', ' ')
        elif self._flash and now < self._flash[1]:
            ch = ch_chars(self._flash[0]) if blink else (' ', ' ')
        else:
            self._flash = None
            n = self._channel_now()
            ch = ch_chars(n) if n else (' ', ' ')

        view = self.meter_view.get(self.band, 'Q')
        sig, uncal = shown['smeter'] if view == 'S' else (shown['sig'], False)
        state = dict(legends=frozenset(legends), fixed=True, unit=unit, freq=(big, dots, fine),
                     sig=sig, meter=view, uncal=uncal, ch=ch,
                     rds=self._live_text(now, blink) or shown['text'])
        if now < self._lamp_until:
            state['all'] = True
        return state

    # ---- direct frequency entry and messages
    def _message(self, text, secs=2.5):
        self._dm_msg = (text, time.monotonic() + secs)

    def _live_text(self, now, blink):
        """What the dot matrix shows right now, ahead of RDS: an entry in progress or a
        message. Live, never held back for audio sync. (The exact frequency, off the band
        or off its raster, is on the frequency digits.)"""
        if self._direct is not None:
            unit = 'MHz' if self.band == 'FM' or '.' in self._direct else 'kHz'
            return ('', '  FREQ', f"{self._direct}{'_' if blink else ' '} {unit}")
        if self._dm_msg is not None:
            if now < self._dm_msg[1]:
                return ('', '', self._dm_msg[0])
            self._dm_msg = None
        return None

    def _direct_start(self):
        if not self.power_on:
            return
        self._seek_cancel()
        self._entry_cancel()
        self._memory_until = 0.0
        self._direct, self._direct_t = '', time.monotonic()

    def _direct_key(self, ch):
        if self._direct is None or len(self._direct) >= 9 or (ch == '.' and '.' in self._direct):
            return
        self._direct += ch
        self._direct_t = time.monotonic()

    def _direct_back(self):
        if self._direct:
            self._direct = self._direct[:-1]
            self._direct_t = time.monotonic()

    def _direct_cancel(self):
        self._direct = None

    def _direct_toggle(self):
        """FREQ button, D or Enter: start an entry, or tune to the one typed."""
        if self._direct is None:
            self._direct_start()
        else:
            self._direct_enter()

    def _direct_enter(self):
        text, self._direct = self._direct, None
        hz, why = self._parse_direct(text or '')
        if hz is None:
            self._message(why)
            return
        self._tune_to(hz)
        self._last_ch = None
        self._save_settings()

    def _parse_direct(self, text):
        """Typed digits to Hz. FM: tenths of a MHz (885 = 88.5), whole MHz, or kHz;
        a decimal means MHz. AM: kHz, or MHz with a decimal."""
        if not text or text == '.':
            return None, 'NO FREQUENCY'
        try:
            v = float(text)
        except ValueError:
            return None, 'NOT A FREQUENCY'
        if '.' in text:
            cands = [v * 1e6]
        elif self.band == 'FM':
            cands = [v * 1e5, v * 1e6, v * 1e3]
        else:
            cands = [v * 1e3, v * 1e6]
        lo, hi, _ = BANDS[self.band]
        rlo, rhi = self._limits(self.band)
        for c in cands:
            if lo - 1 <= c <= hi + 1:
                return c, ''
        for c in cands:
            if rlo <= c <= rhi:
                return (c, '') if self.override else (None, 'OUT OF BAND: OVERRIDE OFF')
        return None, 'OUT OF RANGE'

    def _rds_fields(self, tb, now):
        """Call letters, PS and a window of RadioText for the dot-matrix row.
        RadioText longer than its field scrolls one character per RDS_SCROLL_S
        (the Delco's marquee pace), pausing on the start of the text."""
        n_rt = DOT_FIELDS[2][2]
        if tb is None or self._moving() or now - self._tuned_t < TUNED_SETTLE_S:
            return ('', '', '')
        if self.band == 'AM' and not (tb.addons and getattr(tb, 'rds_pi', None) is not None):
            if not getattr(tb, 'stereo_indicator', False):
                return ('', '', '')
            return ('', '', 'KAHN ISB STEREO' if getattr(tb, 'stereo_system', '') == 'kahn' else 'C-QUAM STEREO')
        if getattr(tb, 'rds_pi', None) is None:          # a stereo add-on's system is named,
            st = tb.addon_stereo                          # as AM stereo is
            return ('', '', f'{st} STEREO' if st and getattr(tb, 'stereo_indicator', False) else '')
        call = (self._call_for(tb) or '')[:4]
        ps = (tb.rds_ps or '').strip()[:8]
        alert = tb.station.get('alert', 0)              # an add-on's alert: blink ALERT or TEST
        if alert:                                       # over the station name
            ps = ('ALERT' if alert >= 2 else 'TEST') if int(now * 2) % 2 == 0 else ''
        rt = ' '.join((tb.rds_rt or '').split())
        if rt != self._rt_text:
            self._rt_text, self._rt_pos, self._rt_t = rt, 0, now
        if len(rt) <= n_rt:
            window = rt
        else:
            loop = rt + ' ' * 4
            if now - self._rt_t >= (RDS_RT_PAUSE_S if self._rt_pos == 0 else RDS_SCROLL_S):
                self._rt_t = now
                self._rt_pos = (self._rt_pos + 1) % len(loop)
            window = (loop + loop)[self._rt_pos:self._rt_pos + n_rt]
        return (call.ljust(4), ps.center(8), window)

    @staticmethod
    def _state_key(st):
        if st is None:
            return None
        def norm(v):
            if isinstance(v, dict):
                return tuple(sorted((k, norm(x)) for k, x in v.items()))
            if isinstance(v, (set, frozenset)):
                return tuple(sorted(v))
            return v
        return norm(st)

    def _refresh_vfd(self, force=False):
        st = self._vfd_state()
        key = self._state_key(st)
        if not force and key == self._shown:
            return
        self._shown = key
        self._vfd_img = ImageTk.PhotoImage(self.vfd.render(st))
        self.canvas.itemconfig(self.vfd_item, image=self._vfd_img)

    # ------------------------------------------------------------------
    # Monitor unit
    # ------------------------------------------------------------------

    def _mon_retuned(self):
        self._mon_settle_until = time.monotonic() + MON_SETTLE_S[self.band]
        self._mon_reset_req = True
        self._ifs, self._echo, self._mpx_avg, self._am_if_avg = None, None, None, None
        self._mpx_stats = {}

    def _toggle_monitor(self):
        self.monitor_on = not self.monitor_on
        if self.monitor_on:
            self.mcanvas.pack(pady=(round(RACK_GAP * self.s), 0))
            self._mshown = None
        else:
            self.mcanvas.pack_forget()
        if getattr(self, '_mon_var', None) is not None:
            self._mon_var.set(self.monitor_on)
        self._save_settings()

    def _set_meter_mode(self, mode):
        self.meter_mode = self.mon.mode = mode
        self._refresh_keys()
        self._save_settings()

    def _toggle_hold(self):
        self.peak_hold = self.mon.hold_on = not self.peak_hold
        self._refresh_keys()
        self._save_settings()

    def _set_scope(self, mode):
        self.scope_mode = mode
        self.freeze = False
        self._refresh_keys()
        self._save_settings()

    def _toggle_freeze(self):
        if self.power_on:
            self.freeze = not self.freeze
        self._refresh_keys()

    def _mon_reset(self):
        self.mon.reset()

    # ---- sync to audio
    def _lag_update(self, now):
        """Track how far the sound card trails the meters (smoothed; reset per flowgraph)."""
        dt, self._lag_t = now - self._lag_t, now
        tb = self.tb
        if tb is not self._lag_tb:
            self._lag_tb, self._lag = tb, None
        if tb is None:
            return
        try:
            raw = tb.audio_lag()
        except Exception as e:
            self._report('audio lag', e)
            return
        if not raw:
            return
        raw = tuple(min(LAG_MAX_S, max(0.0, v)) for v in raw)
        if self._lag is None:
            self._lag = raw
        else:
            a = 1 - math.exp(-max(0.0, dt) / LAG_SMOOTH_S)
            self._lag = tuple(o + a * (r - o) for o, r in zip(self._lag, raw))

    def _delays(self):
        """(meters, indicators): how long to hold readings back, less the displays' own lag."""
        if not self.sync_audio or self._lag is None:
            return 0.0, 0.0
        return max(0.0, self._lag[0] - LAG_SELF_S), max(0.0, self._lag[1] - LAG_SELF_S)

    def _monitor_step(self, now):
        """Take a reading now, queue it with what it needs to be shown, and show
        the readings the audio has caught up with."""
        tb = self.tb
        if self._mon_reset_req:                # a retune: start over when the audio gets there
            self._mon_reset_req = False
            self._mon_q.append(dict(t=now, reset=True))
        m = None
        if tb is not None:
            try:
                m = tb.monitor()
            except Exception as e:
                self._report('monitor', e)
        if tb is not None and m is None and not self._meters_missing:
            self._meters_missing = True
            print('The monitor needs rx_meters.py next to fm_stereo.py (and the patched receivers).')
        settling = (tb is None or not self.power_on or self._moving() or now < self._mon_settle_until)
        item = dict(t=now, m=m, band=self.band, settling=settling, keep=self._q_on)
        if m is not None and not settling:
            fm = self.band == 'FM'
            if fm:                                 # judged before the equalizer, like the path itself
                item['valid'] = getattr(tb, 'pre_cnr_db', getattr(tb, 'cnr_db', 0.0)) >= MOD_MIN_CNR_DB
            else:                                  # AM: locked, and clean enough to mean anything
                item['valid'] = (bool(getattr(tb, 'locked', False))
                                 and getattr(tb, 'cnr_db', 0.0) >= AM_MOD_MIN_CNR_DB)
            pilot = None
            if fm and getattr(tb, 'pilot_level', 0.0) / 1.8 * 100 >= 1.0:
                pilot = tb.pilot_level / 1.8 * 100 * PILOT_COMP
            elif not fm and getattr(tb, 'pilot_on', False):
                pilot = tb.pilot_level * 100
            item['pilot'] = pilot
            if self.monitor_on and not self.freeze:
                try:
                    item['scope'] = (self.scope_mode, self._scope_traces(tb))
                except Exception as e:
                    self._report('scope', e)
            try:
                self._mpx_stats_update(tb, now)
            except Exception as e:
                self._report('mpx stats', e)
        self._mon_q.append(item)

        play = now - self._delays()[0]
        while self._mon_q and (self._mon_q[0]['t'] <= play or len(self._mon_q) > 400):
            it = self._mon_q.popleft()
            if it.get('reset'):
                self.mon.reset()
                self._mon_last = None
                continue
            dt = min(0.25, max(0.0, it['t'] - self._mon_t))
            self._mon_t = it['t']
            if it['m'] is not None and not it['settling']:
                self.mon.feed(it['m'], it['t'], dt, it['band'], it['keep'], it['valid'])
            else:
                self.mon.idle(it['t'], dt)
                if it['settling']:
                    self.mon.gonio = None
            self._mon_last = it
        if not self.monitor_on:
            return
        last = self._mon_last
        live = bool(last) and last['m'] is not None and not last['settling']
        st = self._monitor_state(now, play, live, last.get('pilot') if last else None)
        key = self._state_key(st)
        if key != self._mshown:
            self._mshown = key
            self._mvfd_img = ImageTk.PhotoImage(self.mvfd.render(st))
            self.mcanvas.itemconfig(self.mvfd_item, image=self._mvfd_img)
        self._crt_step(last, live)

    def _monitor_state(self, now, play, live, pilot):
        """now: for the power-on lamp test; play: the time the audio has reached."""
        if not self.power_on:
            return None
        if now < self._lamp_until:
            return {'all': True}
        mon, sc = self.mon, MonitorVfd.seg_index
        bars = {}
        for ch, row in ((0, 'L'), (1, 'R')):
            hold = sc('dB', mon.hold[ch][0]) - 1 if self.peak_hold else None
            bars[row] = (sc('dB', mon.level(ch)), hold)
        hold = sc('MOD', mon.mod_bar_hold[0]) - 1 if self.peak_hold and mon.mod_valid else None
        bars['MOD'] = (sc('MOD', mon.mod_bar), hold)
        bars['LUFS'] = (sc('LUFS', mon.loud.momentary() if live else None), None)
        stats = {'PPM' if self.meter_mode == 'ppm' else 'VU'}
        if self.peak_hold:
            stats.add('HOLD')
        if play - mon.last_over < OVER_LAMP_S:
            stats.add('OVER')
        if live and not mon.mod_valid:
            stats.add('NOISY')
        fm = self.band == 'FM'
        pk = mon.mod_peak() if live else None
        nums = {}

        def put(name, v, unit):
            chars, dp = num_chars(v)
            nums[name] = (chars, dp, unit)
        put('int', mon.loud.integrated(play), 'LUFS')
        put('short', mon.loud.short_term() if live else None, 'LUFS')
        put('lra', mon.loud.lra(play), 'LU')
        put('tp', mon.tp_max, 'dBTP')
        if self.peak_mode == 'frequent':
            mod_v = mon.frequent_peak(play, 'both' if fm else 'neg') if live and mon.mod_valid else None
        else:
            mod_v = (pk[1] if fm else max(pk[2], pk[3])) if pk else None
        put('mod', mod_v, 'kHz' if fm else '%')
        put('pilot', pilot if live else None, '%')
        return dict(bars=bars, stats=frozenset(stats), nums=nums)

    def _crt_step(self, last, live):
        on = self.power_on
        traces, stems = (), None
        if live and not self.freeze and last.get('scope') and last['scope'][0] == self.scope_mode:
            traces, stems = last['scope'][1]
        img = self.crt.frame(on, self.scope_mode, self.band,
                             gonio=self.mon.gonio if live else None,
                             corr=self.mon.corr if live else None,
                             traces=traces, stems=stems, frozen=self.freeze and on)
        self._crt_img = ImageTk.PhotoImage(img)
        self.mcanvas.itemconfig(self.crt_item, image=self._crt_img)

    @staticmethod
    def _bins(x, y, n=180):
        """Reduce a trace to n columns (peak of each), like a spectrum analyser's display."""
        m = len(x) // n * n
        if m < n:
            return np.asarray(x), np.asarray(y)
        return np.asarray(x[:m]).reshape(n, -1).mean(1), np.asarray(y[:m]).reshape(n, -1).max(1)

    def _scope_traces(self, tb):
        mode, fm = self.scope_mode, self.band == 'FM'
        if mode == 'IF':
            if fm:
                ifs = self._if_spectra(tb)
                if not ifs:
                    return (), None
                f, top = ifs['f'], ifs['top']
                sel = (f >= -120) & (f <= 120)
                xs = (f[sel] + 120) / 240
                xa, ya = self._bins(xs, (ifs['pre'][sel] - (top - 60)) / 60)
                xb, yb = self._bins(xs, (ifs['post'][sel] - (top - 60)) / 60)
                return [(xa, ya, 0.4), (xb, yb, 1.0)], None
            x = np.asarray(tb.snapshot('iq'))
            if len(x) < 64 or not np.any(x):
                return (), None
            n = len(x)
            w = np.hanning(n)
            p = np.fft.fftshift(np.abs(np.fft.fft(x * w)) ** 2) / np.sum(w) ** 2
            self._am_if_avg = p if self._am_if_avg is None or len(self._am_if_avg) != n else \
                self._am_if_avg + SPEC_AVG * (p - self._am_if_avg)
            db = 10 * np.log10(self._am_if_avg + 1e-20)
            f = np.fft.fftshift(np.fft.fftfreq(n, 1 / getattr(tb, 'ch_rate', 48000))) / 1e3
            top = math.ceil(db.max() / 10) * 10
            sel = np.abs(f) <= 24
            xb, yb = self._bins((f[sel] + 24) / 48, (db[sel] - (top - 60)) / 60)
            return [(xb, yb, 1.0)], None
        if mode == 'MPX':
            x = np.asarray(tb.snapshot('mpx'), np.float64)
            if len(x) < 64 or not np.any(x):
                return (), None
            n = len(x)
            w = np.hanning(n)
            full = getattr(tb, 'MPX_FULL', 1.8)
            p = (2 * np.abs(np.fft.rfft((x - x.mean()) * w)) / np.sum(w) / full) ** 2
            self._mpx_avg = p if self._mpx_avg is None or len(self._mpx_avg) != len(p) else \
                self._mpx_avg + SPEC_AVG * (p - self._mpx_avg)
            db = 10 * np.log10(self._mpx_avg + 1e-20)
            f = np.fft.rfftfreq(n, 1 / getattr(tb, 'mpx_rate', 240000)) / 1e3
            fmax = 100.0 if fm else 12.0
            sel = f <= fmax
            xb, yb = self._bins(f[sel] / fmax, (db[sel] + 60) / 60)
            return [(xb, yb, 1.0)], None
        if mode == 'ECHO' and fm:
            prof = self._echo_profile(tb)
            if not prof:
                return (), None
            return (), ((prof['delays'] + 20) / 270, (prof['db'] + 40) / 40)
        return (), None

    def _if_spectra(self, tb):
        """Channel spectra before and after the equalizer, smoothed (shared with Advanced)."""
        now = time.monotonic()
        if self._ifs is not None and now - self._ifs['t'] < 0.04:
            return self._ifs
        pre, post = np.asarray(tb.snapshot('pre')), np.asarray(tb.snapshot('post'))
        n = len(pre)
        if n < 64 or len(post) != n or not np.any(pre):
            return self._ifs
        w = np.hanning(n)
        sw = np.sum(w) ** 2
        spec = lambda z: np.fft.fftshift(np.abs(np.fft.fft(z * w)) ** 2) / sw
        a, b = spec(pre), spec(post)
        old = self._ifs
        if old is not None and len(old['pa']) == n:
            a = old['pa'] + SPEC_AVG * (a - old['pa'])
            b = old['pb'] + SPEC_AVG * (b - old['pb'])
        pre_db, post_db = 10 * np.log10(a + 1e-20), 10 * np.log10(b + 1e-20)
        top = math.ceil(max(pre_db.max(), post_db.max()) / 10) * 10
        self._ifs = dict(t=now, f=np.fft.fftshift(np.fft.fftfreq(n, 1 / getattr(tb, 'mpx_rate', 240000))) / 1e3,
                         pa=a, pb=b, pre=pre_db, post=post_db, top=top, n=n)
        return self._ifs

    def _echo_profile(self, tb):
        """The channel the equalizer is undoing: invert its taps (regularized),
        put the strongest path at 0 and read the echoes off by delay."""
        now = time.monotonic()
        if self._echo is not None and now - self._echo_t < 0.25:
            return self._echo
        if not tb.has('echo'):
            return None
        h, wgt = tb.eq_taps()
        h = np.asarray(h, np.complex128)
        if len(h) < 4 or not np.any(h):
            return None
        n = 1024
        H = np.fft.fft(h, n)
        p = np.abs(H) ** 2
        mag = np.abs(np.fft.ifft(np.conj(H) / (p + 0.02 * p.max())))
        k0 = int(np.argmax(mag))
        fs = float(getattr(tb, 'mpx_rate', 240000))
        idx = np.arange(-int(20e-6 * fs), int(250e-6 * fs) + 1)
        db = 20 * np.log10(np.maximum(mag[(k0 + idx) % n] / mag[k0], 1e-6))
        delays = idx / fs * 1e6
        echoes = [(delays[i], delays[i] * MILES_PER_US, db[i]) for i in range(1, len(idx) - 1)
                  if idx[i] >= 2 and db[i] > -30 and db[i] >= db[i - 1] and db[i] >= db[i + 1]]
        echoes.sort(key=lambda e: -e[2])
        self._echo = dict(delays=delays, db=db, echoes=echoes[:3], weight=wgt,
                          active=bool(getattr(tb, 'eq_active', False)))
        self._echo_t = now
        return self._echo

    def _mpx_stats_update(self, tb, now):
        """RDS injection and subcarriers from the composite (FM, twice a second)."""
        if self.band != 'FM' or now - self._mpx_t < MPX_STATS_S:
            return
        self._mpx_t = now
        x = np.asarray(tb.snapshot('mpx'), np.float64)
        n = len(x)
        if n < 1024 or not np.any(x):
            return
        w = np.hanning(n)
        P = np.abs(np.fft.rfft((x - x.mean()) * w)) ** 2
        f = np.fft.rfftfreq(n, 1 / getattr(tb, 'mpx_rate', 240000))
        norm = 2.0 / (n * np.sum(w ** 2))                     # band sum -> mean power
        band = lambda a, b: P[(f >= a) & (f < b)]
        dens = lambda a, b: float(np.mean(band(a, b))) if np.any((f >= a) & (f < b)) else 0.0
        rds_bins = band(54.6e3, 59.4e3)
        floor = dens(60.5e3, 63.5e3)
        rds_pow = max(float(np.sum(rds_bins)) - floor * len(rds_bins), 0.0) * norm
        ref = max(dens(75e3, 84e3), 1e-30)
        self._mpx_stats = dict(
            rds=math.sqrt(2 * rds_pow) / 1.8 * 100 * RDS_CREST * RDS_COMP
                * (RDS_CAL_STEREO_TOOL if self.rds_scale == 'stereo_tool' else 1.0),
            sca67=10 * math.log10(max(dens(62e3, 72e3), 1e-30) / ref) > 8.0,
            sca92=10 * math.log10(max(dens(87e3, 97e3), 1e-30) / ref) > 8.0)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _tick(self):
        if not self.running:
            return
        now = time.monotonic()
        if self._direct is not None and now - self._direct_t > DIRECT_WAIT_S:
            self._direct = None
        self._lag_update(now)
        if self._set_visible and self._lag_label is not None:
            lag = self._lag
            self._lag_label.config(text='' if lag is None else
                                   f'audio trails the meters by {lag[0]:.2f} s')
        if self._set_visible and self._cal_label is not None:
            self._cal_label.config(text=self._smeter_status(now))
        try:
            self._refresh_vfd()
        except Exception as e:
            self._report('tuner display', e)
        try:
            self._monitor_step(now)
        except Exception as e:
            self._report('monitor', e)
        tb = self.tb
        if tb is not None and self.agc_on:
            g = tb.gain_db
            self.rf_gain = g
            if self._set_visible and abs(self.gain_var.get() - g) > 0.05:
                self._slider_auto_val = g
                self.gain_var.set(g)
                self._update_gain_label(g)
        self.root.after(UI_TICK_MS, self._tick)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def _on_close(self):
        if self._seek_state:
            self.freq = self._seek_state['start']
        self._seek_state = None
        self._save_settings()
        self.running = False
        tb, self.tb = self.tb, None
        if tb is not None:
            try:
                tb.stop()
                tb.wait()
                tb.close()
            except Exception:
                pass
        self.root.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description='Stereo tuner and monitor for the SDR receiver')
    ap.add_argument('--freq', type=float, default=None, help='frequency in Hz (default: last station played)')
    ap.add_argument('--scale', type=float, default=None, help='panel size (default: 1.0, smaller to fit the screen)')
    ap.add_argument('--gain', type=float, default=None, help='manual gain, or AGC starting point (dB)')
    ap.add_argument('--no-agc', action='store_true', help='start in manual gain')
    ap.add_argument('--lmr-gain', type=float, default=1.0)
    ap.add_argument('--log', default=None, help='CSV of receiver metrics')
    ap.add_argument('--record-iq', default=None, help='raw IQ recording, int8 at 2.4 Msps')
    ap.add_argument('--am-source', default='direct_q', choices=('direct_q', 'native', 'upconverter'))
    ap.add_argument('--upconverter-hz', type=float, default=125e6)
    ap.add_argument('--audio-device', default='',
                    help='sound output for GNU Radio, e.g. pulse or plughw:0,0 on Linux (default: system default)')
    a = ap.parse_args()

    root = tk.Tk()
    scale = a.scale
    if scale is None:
        scale = min(1.0, (root.winfo_screenwidth() - 40) / BASE_W,
                    (root.winfo_screenheight() - 120) / (2 * BASE_H + RACK_GAP))
    TunerApp(root, scale=scale, freq=a.freq, rf_gain=a.gain,
             agc=False if a.no_agc else None, lmr_gain=a.lmr_gain,
             log_path=a.log, record_iq=a.record_iq,
             am_source=a.am_source, upconverter_hz=a.upconverter_hz,
             audio_device=a.audio_device)
    root.mainloop()
