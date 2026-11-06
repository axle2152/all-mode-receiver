#!/usr/bin/env python3
"""
Stereo Tuner and Monitor
========================
A face for the SDR receiver in two stacked units, on the same back end as
the Delco head unit (fm_stereo.FMStereoReceiver / am_receiver.AMReceiver).
Run this file, not fm_stereo.py.

    python quartz_tuner_ui.py
    python quartz_tuner_ui.py --freq 90.5e6 --scale 1.2
    python quartz_tuner_ui.py --no-agc --gain 7.7 --log drive.csv

Tuner (top unit)
    Power           latching; off mutes at once and frees the dongle after 60 s
    Tuning down/up  tap: one channel. Hold: search to the next station.
                    Either key stops a search that is running.
    0 to 9          preset channels 1 to 24 (3-9 recall at once; 1 or 2 wait a
                    moment for a second digit; 0 then a digit = 01 to 09)
    Memory          then a channel number: stores the station you're on
    FM / AM         band; each band remembers its own frequency
    Wide            latching AM filter key: 10.2 kHz, with a 10 kHz notch for
                    adjacent-channel whistles. Off: auto, never wider than the normal
                    width chosen in Settings (5, 6 or 8 kHz). Lights WIDE on AM.
    Mono            latching Stereo key, both bands: FM mono, or AM with the
                    C-QUAM stereo ignored
    ADV / MON / SET the faint buttons bottom left: Advanced window (diagnostics),
    / FREQ          monitor unit, Settings (reception, panel, and display colours),
                    and direct frequency entry: then digits (keyboard or keypad),
                    Enter to tune, Esc to cancel, Backspace to correct. FM: 885 =
                    88.5 MHz, or type a decimal (162.55); AM: kHz (1720), or a
                    decimal for MHz (5.905). Out-of-band needs Band limits: Override.
    Display         TUNED = the retune has settled and there is a carrier the
                    receiver can hold; STEREO = the back end's stereo indicator;
                    SIGNAL = control-loop CNR on a nine-step ladder

Monitor (bottom unit; needs rx_meters.py next to fm_stereo.py)
    L / R           station audio level after de-emphasis, ahead of volume:
                    PPM (IEC type I fall-back) or VU ballistics, with peak hold.
                    0 dBFS = 75 kHz of L+R deviation (AM: 100% modulation).
    MOD             peak modulation from its own +/-110 kHz path, composite
                    cut at 60 kHz (main channel, pilot and RDS; SCAs are not
                    included), so neither the channel filter nor neighbouring
                    stations can inflate it (FM: kHz and percent of 75 kHz;
                    AM: the larger of positive and negative). NOISY
                    lights instead when noise or multipath ahead of the
                    equalizer would make the peaks read high.
    LUFS            BS.1770 momentary loudness; INTEGRATED, SHORT TERM, RANGE
                    (LRA) and the highest TRUE PEAK since the last retune or Reset
    PEAK MOD        FM: peaks of frequent recurrence (the FCC's wording), the highest
                    deviation reached at least once a second over 10 s; the true
                    maximum and the full distribution are in Advanced. Measured
                    from the carrier's centre, so a frequency offset can't add to it.
                    AM: negative peaks of frequent recurrence, which is how the FCC
                    words the AM limit (100%); clicks and static throw the envelope
                    up, almost never down, so this side stays honest on a noisy band.
                    Positive peaks (125% limit, at any time) are in Advanced.
    PILOT           19 kHz pilot injection (AM: C-QUAM pilot)
    Screen          goniometer and phase correlation on the left; on the right
                    the channel before/after the equalizer (IF), the composite
                    spectrum (MPX) or the multipath echo profile (Echo)

Keyboard: Left / Right tune, Shift+Left / Right search, 0 to 9 presets, M memory,
F / A band, O mono, W wide (AM), P power, D or Enter direct frequency entry.
Mouse wheel over the tuner display tunes.

Hand-drawn digits: vfd_0.png ... vfd_9.png in a 'vfd' folder next to this file
replace the tuner's drawn frequency segments (vfd_ghost.png: unlit pattern).

Settings live in quartz_tuner_settings.json. On the first run the DSP options
and gain are copied from the Delco's fm_radio_settings.json; after that this
face keeps its own copy, changed from the Settings window. Colours chosen there
are saved per element ("colors"); delete an entry to go back to its default.
"""

import argparse
import json
import math
import os
import re
import threading
import time
import tkinter as tk
import traceback
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

from fm_stereo import FMStereoReceiver
try:
    from fm_stereo import pi_call_candidates
except ImportError:
    pi_call_candidates = lambda pi: []
try:                                     # AM band (and C-QUAM AM stereo)
    from am_receiver import AMReceiver
    _AM_ERR = None
except Exception as _e:
    AMReceiver, _AM_ERR = None, str(_e)


# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

FM_MIN, FM_MAX, FM_STEP = 87.5e6, 107.9e6, 0.2e6
AM_MIN, AM_MAX, AM_STEP = 530e3, 1710e3, 10e3
BANDS = {'FM': (FM_MIN, FM_MAX, FM_STEP), 'AM': (AM_MIN, AM_MAX, AM_STEP)}
# Settings > Reception > Band limits: Override lifts the band edges to what the
# dongle can reach. With direct sampling available (AM source direct_q, an
# RTL-SDR like the V3), either receiver crosses over at 28.8 MHz: the RTL2832's
# own ADC (Q branch, 28.8 MS/s) below it, the quadrature tuner (R820T, from about
# 24 MHz) from there up. Crossing reopens the dongle, so the receiver restarts.
# A V4 (AM source native) covers HF through its built-in upconverter, no switching;
# with an external upconverter, AM stays on HF.
XOVER_HZ   = 28.8e6
RTL_RANGE  = (100e3, 1766e6)
TUNER_MIN  = 24e6
FM_DEVICE_ARGS = 'numchan=1 rtl=0'
DIRECT_WAIT_S = 10.0                       # direct frequency entry gives up after this
N_CHANNELS = 24

SEEK_MIN_CNR_DB = 6.0      # FM; AM uses its receiver's own SEEK_MIN_DB
SEEK_QUICK_N    = 3
SEEK_CONFIRM_N  = 8
SEEK_TIMEOUT_S  = 1.0

HOLD_TO_SEARCH_S = 0.5
ENTRY_WAIT_S     = 1.5
MEMORY_WAIT_S    = 8.0
POWER_SLEEP_S    = 60
LAMP_TEST_S      = 0.6
UI_TICK_MS       = 50
ADV_TICK_MS      = 100

# TUNED: the retune has settled and the CNR is at the search threshold (on AM
# the carrier PLL must also report lock), with a little hysteresis.
TUNED_SETTLE_S = 0.3
TUNED_HYST_DB  = 2.0

SIG_STEPS      = 9
SIG_RANGE      = {'FM': (3.0, 33.0), 'AM': (12.0, 48.0)}
SIG_FALL_PER_S = 8.0

OUTPUT_DB_DEFAULT = -6.0
OUTPUT_DB_CHOICES = (-12.0, -9.0, -6.0, -3.0, 0.0)
BADGE_DEFAULT     = 'Stereo Tuner'

# Monitor
MON_SETTLE_S   = {'FM': 0.5, 'AM': 0.9}   # discard readings this long after a retune
PPM_FALL_DB_S  = 13.3                     # IEC 60268-10 type I: 20 dB in 1.5 s
PEAK_HOLD_S    = 1.5
VU_TAU_S       = 0.045                    # two poles: 99% in about 300 ms
MOD_HOLD_S     = 2.0
# PEAK MOD on the face (FM): "peaks of frequent recurrence", the FCC's wording in
# 47 CFR 73.1570. Read here as the highest deviation the station reaches at least
# FREQ_PEAK_RATE times a second, counted over the last MOD_DIST_S seconds. Rare
# overshoots and stray spikes don't set it; the true maximum is in Advanced.
FREQ_PEAK_RATE = 1.0
MOD_DIST_S     = 10.0
PEAK_STEP      = 0.25                     # must match rx_meters.PEAK_STEP / PEAK_SPAN
PEAK_SPAN      = 150.0
MOD_MIN_CNR_DB = 20.0                     # FM: the deviation path sits ahead of the equalizer, so
                                          # noise or multipath before the EQ below this makes peaks
                                          # read high (15 dB read 9% high in testing): NOISY instead
AM_MOD_MIN_CNR_DB = 40.0                  # AM below this (C/N in 10 kHz): noise pushes peaks >5% high: NOISY
OVER_LAMP_S    = 1.0
CORR_TAU_S     = 0.3
LOUD_RECALC_S  = 0.5
OFFSET_AVG_S   = 3.0
# Sync to audio: the receiver reports how far the sound card trails its meters
# (samples queued in GNU Radio plus fixed processing delays). Meter readings and
# the tuner's received indicators play back that much later, less the displays'
# own lag, so what you see is what you're hearing.
LAG_SELF_S     = 0.035                    # the displays' own lag: half a read interval, plus drawing
LAG_SMOOTH_S   = 1.5
LAG_MAX_S      = 4.0
# Glass glow: light from the tube scattering in the smoked glass. A halo around
# every lit element and a wider, fainter haze, scaled by Settings > Panel > Glass glow.
BLOOM_R1, BLOOM_A1 = 3.5, 0.80            # halo hugging each element: radius (base pixels), strength at 100%
BLOOM_R2, BLOOM_A2 = 10.0, 0.50           # the wider glow in the glass
BLOOM_R3, BLOOM_A3 = 26.0, 0.22           # the lens lit up around the whole display
GLOW_DEFAULT   = 0.8
MILES_PER_US   = 0.18628                  # extra path per microsecond of echo delay (c = 983.6 ft/us)
MPX_STATS_S    = 0.5
# Composite levels. The receiver's composite path rolls off a little toward
# 60 kHz (measured: -0.11 dB at 19 kHz, -0.83 dB at 57 kHz with the IF wide);
# pilot and RDS readings are corrected for it. RDS injection is the peak of the
# RDS waveform, and the IEC 62106 waveform peaks 1.443 x its RMS (worked out
# from the standard's biphase shaping). Settings picks the scale: the standard,
# or about 1.24x that to match Stereo Tool's RDS level setting.
PILOT_COMP     = 10 ** (0.11 / 20)
RDS_COMP       = 10 ** (0.83 / 20)
RDS_CREST      = 1.443
RDS_CAL_STEREO_TOOL = 1.24               # Settings > Panel > RDS level: Stereo Tool's scale
RDS_SCROLL_S   = 0.25                     # RadioText marquee: one character per step (the Delco's)
RDS_RT_PAUSE_S = 2.0                      # ... after showing the start of the text
SCOPE_DECAY    = 0.55                     # CRT phosphor left each frame
GONIO_FILL     = 0.33                     # program RMS sits at this fraction of the radius

_ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_NAME     = 'quartz_tuner_settings.json'
SETTINGS_FALLBACK = os.path.join(os.path.expanduser('~'), '.' + SETTINGS_NAME)
DELCO_SETTINGS    = [os.path.join(_ASSET_DIR, 'fm_radio_settings.json'),
                     os.path.join(os.path.expanduser('~'), '.fm_radio_settings.json')]


# ---------------------------------------------------------------------------
# Look
# ---------------------------------------------------------------------------

BASE_W, BASE_H = 1480, 168            # one unit at scale 1.0
RACK_GAP = 4                          # dark gap between the stacked units
SS = 2                                # supersampling for drawn artwork

PLATE_RGB  = (201, 201, 196)
PRINT      = (43, 44, 47)
PRINT_SOFT = (98, 99, 104)
WELL       = (21, 22, 24)
LIP        = (236, 236, 231)
KEY_TOP, KEY_BOT = (72, 74, 79), (44, 45, 49)
KEY_DN_TOP, KEY_DN_BOT = (52, 53, 57), (36, 37, 40)
KEY_SIDE   = (17, 18, 20)
KEY_HI     = (104, 106, 112)
KEY_TEXT   = (232, 232, 226)
HINT_FILL, HINT_EDGE, HINT_INK, HINT_HOT = '#bfbfba', '#b3b3ae', '#9c9c97', '#2b2c2f'

PHOSPHOR     = np.array((95, 243, 208), np.float32)    # VFD blue-green
PHOSPHOR_HOT = np.array((255, 118, 84), np.float32)    # red-filtered segments
GHOST        = np.array((16, 30, 28), np.float32)
GLASS_MID    = np.array((7, 15, 14), np.float32)
GLASS_EDGE   = np.array((2, 6, 6), np.float32)
GLASS_TINT   = np.array((0.86, 0.93, 1.0), np.float32)  # reflections: cool daylight

# Front glass over the displays (0 turns a part off). Reflections add light
# in cool daylight; the strengths are 0-255 levels at their brightest.
GLASS_SHEEN    = 24.0     # the window-shaped reflection: crisp leading edge, soft tail
GLASS_STREAK   = 13.0     # a narrow second reflection beside it
GLASS_FRESNEL  = 17.0     # glass reflects more toward the top (you look up at it)
GLASS_BEVEL    = 85.0     # light caught along the top edge of the pane
GLASS_UNDER    = 26.0     # and refracted through its bottom edge
GLASS_HAZE     = 2.5      # a faint lift of the blacks: the glass is never perfectly clear
GLASS_DUST     = 0.11     # specks and hairline scratches
GLASS_SMOKE    = 0.93     # transmission of the smoked glass

P31        = np.array((110, 255, 150), np.float32)     # CRT green
CRT_BG     = np.array((5, 11, 8), np.float32)
CRT_GRAT   = np.array((30, 74, 50), np.float32)        # scale illumination: lines
CRT_TEXT   = np.array((125, 210, 160), np.float32)     # ... and lettering, kept readable

# ---- Colours you can change in Settings. Every lit element of both VFDs and
# the CRT belongs to one of these; unlit segments take a faint tint of their own
# colour, the way a coloured filter over a real VFD tints the dark segments too.
_PH, _HOT = '#5ff3d0', '#ff7654'
COLOR_ITEMS = {
    'tuner': (('freq', 'Frequency digits', _PH), ('units', 'kHz / MHz', _PH),
              ('leg_FM', 'FM', _PH), ('leg_AM', 'AM', _PH), ('leg_AUTO', 'AUTO', _PH),
              ('leg_STEREO', 'STEREO', _PH), ('leg_MONO', 'MONO', _PH), ('leg_RDS', 'RDS', _PH),
              ('leg_WIDE', 'WIDE', _PH),
              ('leg_TUNED', 'TUNED', _PH), ('leg_MEMORY', 'MEMORY', _PH),
              ('signal', 'Signal ladder', _PH), ('sig_label', 'SIGNAL label', _PH),
              ('ch_label', 'CH label', _PH), ('ch_digits', 'Channel digits', _PH),
              ('rds_call', 'RDS call letters', _PH), ('rds_ps', 'RDS station name', _PH),
              ('rds_rt', 'RDS RadioText', _PH)),
    'monitor': (('lr', 'L / R bars', _PH), ('lr_hot', 'L / R above 0 dBFS', _HOT),
                ('mod', 'MOD bar', _PH), ('mod_hot', 'MOD above 100%', _HOT),
                ('lufs', 'LUFS bar', _PH), ('hold', 'Peak-hold marker', None),
                ('rowlab', 'Bar labels', _PH), ('scale', 'Scale numbers', _PH),
                ('stat_PPM', 'PPM', _PH), ('stat_VU', 'VU', _PH), ('stat_HOLD', 'HOLD', _PH),
                ('stat_OVER', 'OVER', _HOT), ('stat_NOISY', 'NOISY', _PH),
                ('num_int', 'Integrated', _PH), ('num_short', 'Short term', _PH),
                ('num_lra', 'Range', _PH), ('num_tp', 'True peak', _PH),
                ('num_mod', 'Peak mod', _PH), ('num_pilot', 'Pilot', _PH),
                ('num_labels', 'Readout labels', _PH), ('num_units', 'Readout units', _PH)),
    'screen': (('crt_trace', 'Trace', '#6eff96'), ('crt_scale', 'Scale lines', '#1e4a32'),
               ('crt_text', 'Scale lettering', '#7dd2a0')),
}
COLOR_DEFAULTS = {k: d for items in COLOR_ITEMS.values() for k, _, d in items}
COLOR_PANEL = {k: panel for panel, items in COLOR_ITEMS.items() for k, _, _ in items}


def rgb_of(hexcol):
    return np.array([int(hexcol[i:i + 2], 16) for i in (1, 3, 5)], np.float32)


def hex_of(rgb):
    return '#%02x%02x%02x' % tuple(int(round(min(255, max(0, v)))) for v in rgb)


def ghost_of(rgb):
    """An unlit segment under a filter of this colour, in room light."""
    return rgb * 0.11 + np.array((5, 4, 4), np.float32)


# Tuner layout (base coordinates)
POWER_KEY  = (30, 107, 104, 133)
WIDE_KEY   = (116, 107, 176, 133)        # AM filter: Wide
WIN        = (200, 20, 800, 148)
VFD_BOX    = (203, 23, 797, 145)
PAD_X, PAD_W, PAD_GAP = 836, 56, 10
PAD_ROWS   = ((51, 87), (97, 133))
TUNE_DOWN  = (1192, 51, 1314, 83)
TUNE_UP    = (1328, 51, 1450, 83)
FM_KEY     = (1192, 107, 1246, 133)
AM_KEY     = (1254, 107, 1308, 133)
MEM_KEY    = (1324, 107, 1384, 133)
MONO_KEY   = (1392, 107, 1450, 133)
LABEL_Y, LOWER_LABEL_Y = 43, 99
HINTS      = (('ADV', (30, 145, 56, 156)), ('MON', (61, 145, 87, 156)), ('SET', (92, 145, 118, 156)),
              ('FREQ', (123, 145, 153, 156)))

# Monitor layout
RESET_KEY  = POWER_KEY
CRT_WIN    = (836, 20, 1156, 148)
CRT_BOX    = (839, 23, 1153, 145)
PPM_KEY    = (1192, 51, 1250, 83)
VU_KEY     = (1258, 51, 1316, 83)
HOLD_KEY   = (1392, 51, 1450, 83)
IF_KEY     = (1192, 107, 1250, 133)
MPX_KEY    = (1258, 107, 1316, 133)
ECHO_KEY   = (1324, 107, 1382, 133)
FREEZE_KEY = (1392, 107, 1450, 133)

# Tuner VFD layout, relative to VFD_BOX: legends, italic frequency digits with
# the signal ladder and channel, and a dot-matrix row for RDS underneath
LEG_Y      = 16
LEGENDS    = (('FM', 20), ('AM', 44), ('AUTO', 82), ('STEREO', 124), ('MONO', 178), ('RDS', 222),
              ('WIDE', 252), ('TUNED', 300), ('MEMORY', 492))
DIG_TOP, DIG_H, DIG_W, DIG_T = 24, 48, 29, 5.0
DIG_SLANT  = 0.18                     # italic, about 10 degrees
HALF_X     = 14
DIG_X      = (34, 73, 112)
DP_X       = 104.5
UNIT_X, KHZ_Y, MHZ_Y = 160, 50, 72
SIG_X, SIG_W, SIG_PITCH, SIG_H0, SIG_H1, SIG_BOT = 300, 7, 11, 7, 36, 72
SIG_LABEL_X, SIG_LABEL_Y = 402, 72
CH_LABEL_X  = 470
CH_TOP, CH_H, CH_W, CH_T = 44, 28, 17, 3.4
CH_X        = (492, 516)
DOT_TOP, DOT_PITCH, DOT_SIZE = 86, 2.45, 1.9
DOT_FIELDS  = (('call', 16, 4), ('ps', 96, 8), ('rt', 235, 23))   # name, x, characters (gaps between zones)
DOT_BLUR    = 1.3                     # dots glow less than the big segments
ALWAYS_LIT  = ('CH', 'SIGNAL')

# Monitor VFD layout, relative to VFD_BOX
MB_X0, MB_X1, MB_SEGS = 34, 392, 30
MB_ROWS   = (('L', 9, 18), ('R', 22, 31), ('MOD', 51, 60), ('LUFS', 79, 88))
MB_SCALE  = {'L': 'dB', 'R': 'dB', 'MOD': 'MOD', 'LUFS': 'LUFS'}
SCALES    = {'dB':   dict(lo=-42.0, step=1.5, marks=(-40, -30, -20, -10, -6, -3, 0), y=42, hot=0.0),
             'MOD':  dict(lo=0.0, step=5.0, marks=(0, 25, 50, 75, 100, 125, 150), y=71, hot=100.0),
             'LUFS': dict(lo=-41.0, step=1.2, marks=(-40, -30, -20, -10, -5), y=99, hot=None)}
MON_STATS = (('PPM', 34), ('VU', 64), ('HOLD', 88), ('OVER', 128), ('NOISY', 168))
STAT_Y    = 113
NUM_W, NUM_H, NUM_T, NUM_PITCH = 10, 17, 2.6, 13
NUMS      = (('int', 'INTEGRATED', 410, 15, ('LUFS',)),
             ('short', 'SHORT TERM', 410, 53, ('LUFS',)),
             ('lra', 'RANGE', 410, 91, ('LU',)),
             ('tp', 'TRUE PEAK', 502, 15, ('dBTP',)),
             ('mod', 'PEAK MOD', 502, 53, ('kHz', '%')),
             ('pilot', 'PILOT', 502, 91, ('%',)))

# Advanced window (Delco's dimensions)
SPEC_W, SPEC_H  = 512, 190
SPEC_RANGE_DB   = 70
SPEC_AVG        = 0.35
IF_H            = 150
CONST_SIZE      = 180
CONST_DECAY     = 0.82
DASH_BG      = '#0a0f0e'
DASH_W       = SPEC_W + 40 + 10 + 190
SEG_OFF      = '#14211f'
SEG_ON       = {'good': '#3ae070', 'fair': '#f2c232', 'poor': '#ff4b3a', 'help': '#00e8cc'}
LAMP_OFF     = '#18201f'
LAMP_INK_OFF = '#34433f'
SCOPE_FG     = {'before': (255, 150, 50), 'after': (0, 232, 204)}
HIST_BEFORE, HIST_AFTER, HIST_SHADE = '#b8742a', '#00e8cc', '#0e322c'
EQ_RESP      = '#d8d8d8'
LEGEND_LIT   = '#00e8cc'
FACE_FONT    = ('Eurostile', 9, 'bold')
FACE_FG      = '#9a9a9a'
BUTTON_SMALL = (56, 24)
BUTTON_TRIM  = '#000000'
BUTTON_BRUSH = 0.11
THEME = dict(tints={None: ('#f2f2f2', '#a4a4a4'), 'green': ('#cdf3da', '#4f9f6a')},
             ink='#202020', bevel=('#ffffff', '#6a6a6a'), bevel_down=('#7a7a7a', '#e0e0e0'))


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_FONT_DIRS = [os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'),
              os.path.expanduser('~/AppData/Local/Microsoft/Windows/Fonts'),
              '/usr/share/fonts', '/usr/local/share/fonts', os.path.expanduser('~/.fonts'),
              '/Library/Fonts', '/System/Library/Fonts']
_font_index = None

FONT_ROLES = {
    'label':  [('bahnschrift.ttf', 'Regular'), ('segoeui.ttf', None), ('arial.ttf', None),
               ('DejaVuSans.ttf', None)],
    'badge':  [('bahnschrift.ttf', 'SemiBold'), ('segoeuib.ttf', None), ('arialbd.ttf', None),
               ('DejaVuSans-Bold.ttf', None)],
    'key':    [('bahnschrift.ttf', 'SemiBold'), ('segoeuisb.ttf', None), ('arialbd.ttf', None),
               ('DejaVuSans-Bold.ttf', None)],
    'vfd':    [('bahnschrift.ttf', 'SemiBold SemiCondensed'), ('ARIALNB.TTF', None),
               ('arialbd.ttf', None), ('DejaVuSansCondensed-Bold.ttf', None)],
}
_font_cache = {}

# Nimbus Sans (URW's Helvetica) for every printed label, key cap and the
# nameplate. Found by each font file's own family and style names, since the
# files go by NimbusSanL-Regu, n019003l.pfb, NimbusSans-Regular.otf and so on.
# The displays (VFD, dot matrix, CRT) keep their own lettering.
NIMBUS_ROLES = {'label': (False, False), 'badge': (True, False), 'key': (True, False)}
_nimbus = None
UI_FAMILY = 'Helvetica'                  # Tk family for the Advanced window; set at start-up


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
    if f is None:
        try:
            f = ImageFont.load_default(size=px)
        except TypeError:
            f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def text_width(s, f, tracking=0.0):
    return sum(f.getlength(c) for c in s) + tracking * max(0, len(s) - 1)


def draw_text(d, x, y, s, f, fill, tracking=0.0, align='l', vanchor='s'):
    """Text at baseline y; align l / m / r; tracking in pixels between letters."""
    w = text_width(s, f, tracking)
    if align == 'm':
        x -= w / 2
    elif align == 'r':
        x -= w
    if tracking == 0.0:
        d.text((x, y), s, font=f, fill=fill, anchor='l' + vanchor)
        return
    for c in s:
        d.text((x, y), c, font=f, fill=fill, anchor='l' + vanchor)
        x += f.getlength(c) + tracking


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
        d.text((x, cy), legend, font=f, fill=KEY_TEXT, anchor='lm')
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
}
_GLYPHS = {ch: [int(v, 16) for v in rows.split()] for ch, rows in FONT_5X7.items()}


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
            rows = _GLYPHS.get(ch, _GLYPHS[' '])
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
                                    font=(UI_FAMILY, max(6, round(6.5 * s)), 'bold'), fill=HINT_INK)
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
        self._font = font or (UI_FAMILY, 12, 'bold')
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
        tk.Label(tw, text=text, justify=tk.LEFT, wraplength=320, font=(UI_FAMILY, 9),
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
            self.create_text(9, ym, anchor='w', text=name, font=(UI_FAMILY, 8, 'bold'), fill='#8fbfb6')
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
        self._cap = self.create_text(32, 47, text='', font=(UI_FAMILY, 7, 'bold'), fill='#6f9f98')
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
                                 font=(UI_FAMILY, 7, 'bold'), fill=LAMP_INK_OFF)
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


_PTY_NAMES = ['None', 'News', 'Information', 'Sports', 'Talk', 'Rock', 'Classic Rock',
              'Adult Hits', 'Soft Rock', 'Top 40', 'Country', 'Oldies', 'Soft',
              'Nostalgia', 'Jazz', 'Classical', 'R&B', 'Soft R&B', 'Language',
              'Religious Music', 'Religious Talk', 'Personality', 'Public', 'College']


def _pty_name(pty):
    if pty is None:
        return '--'
    if pty < len(_PTY_NAMES):
        return _PTY_NAMES[pty]
    return {29: 'Weather', 30: 'Emergency Test', 31: 'Emergency'}.get(pty, str(pty))


TIPS = dict(
    quality="How clean the station sounds after all the processing, measured from the FM signal "
            "itself. The white tick shows where it was before the echo canceller. Above 20 dB is clean; "
            "below 10 dB you will hear noise.",
    strength="How far the station stands above the background noise of the band. Strong stations "
             "read 30 dB or more.",
    echo="Distortion from the signal arriving by more than one path, bouncing off buildings and hills. "
         "It makes FM sound harsh and splattery, and a plain SDR can only play it back as-is.",
    eqfix="The echo canceller learns the reflections and subtracts them, the way a car radio's DSP "
          "tuner does. This is how much cleaner it makes the signal. It switches itself in only "
          "when there are echoes to fix.",
    nr="Removes hiss between words and notes on weak stations, using the empty parts of the FM "
       "signal to measure the noise. It stays out of the way on strong stations.",
    static="When the signal briefly drops out, trims the treble and stereo for a few milliseconds so "
           "the crackle doesn't come through. Quick bursts of multipath (picket fencing) pull just the "
           "stereo, where that noise is loudest.",
    stereo="How much stereo separation is let through. Weak signals blend toward mono to stay quiet, "
           "like a car radio. 100% is full stereo.",
    treble="On noisy signals the highs roll off gently, where most of the hiss lives. 100% is full treble.",
    bw="Width of the receiver's filter. On a weak station it narrows to just the station to cut noise; "
       "it never narrows on loud music. The white tick is how wide the station really is.",
    mod="How hard the station is modulating. 75 kHz (the tick) is 100%, the legal limit in the US.",
    am_quality="Carrier-to-noise ratio of the AM station in a 10 kHz channel. Above 25 dB is clean.",
    am_adj="How strong the neighbouring stations are next to this one. High readings mean splatter "
           "and whistles, so the receiver narrows its filter.",
    am_bw="Width of the AM filter: wide on clean stations for full audio, narrow on weak ones or with "
          "loud neighbours.",
    am_pilot="Strength of the stereo pilot tone that tells the receiver a station is in AM stereo: "
             "25 Hz for C-QUAM, 15 Hz for Kahn ISB.",
    am_ib="Impulse blanker: spikes per minute it patched (envelope spikes beyond +300%, which no legal "
          "program reaches) plus C-QUAM phase glitches it muted in the stereo difference.",
    am_nb="Noise blanker: share of the time it's patching pulses. It raises its own threshold if this "
          "passes 1%, so it never eats the program.",
    lamp_blank="A blanker is catching noise pulses or glitches.",
    am_stereo="How much stereo is let through. Weak signals blend toward mono.",
    am_level="Carrier level at the receiver, in dB below full scale.",
    scope_fm="Each dot is a sample of the radio wave. A clean FM signal draws a thin ring; echoes and noise "
             "smear it into a fuzzy donut. Left: as received. Right: after the echo canceller.",
    scope_am="Left: the carrier as received, spinning around the circle. Right: after the receiver "
             "locks onto it, mono AM collapses to a line and C-QUAM stereo opens into an arc.",
    hist_fm="Signal quality over the last 30 seconds: amber as received, cyan after cleanup. Shading "
            "marks the time the echo canceller was working.",
    hist_am="AM signal quality over the last 30 seconds. Shading marks AM stereo.",
    spec="MPX shows the FM baseband: mono audio to 15 kHz, the 19 kHz stereo pilot, stereo audio at "
         "23 to 53 kHz, RDS data at 57 kHz and subcarriers (SCA) at 67 and 92 kHz. RF shows the "
         "radio channel itself.",
    ifeq="The channel spectrum as received (amber) and after the echo canceller (cyan). Multipath "
         "carves notches and ripple into a station; the equalizer flattens them. Grey is the "
         "equalizer's own response, on the scale at the right: the correction it is applying.",
    echoprof="The reflections the equalizer has learned, worked out from its taps: each spike is an "
             "echo, plotted by how late it arrives. Every microsecond of delay is about 1,000 feet "
             "(0.19 mile) of extra path, so this shows how far away the reflecting hills or "
             "buildings are.",
    monline="From the monitor taps: peak deviation from its own measuring path (main channel, pilot and "
            "RDS; SCAs not included), how often it went past "
            "100%, the pilot and RDS injection, subcarriers, the carrier's frequency error (on a "
            "station that's on frequency, this is the dongle's own error), and loudness per BS.1770.",
    moddist="How many times a second the deviation reaches each level, measured from the carrier's "
            "centre over the last 10 seconds. A processed station shows a cliff at its ceiling. A thin "
            "tail beyond the cliff is rare overshoots or stray spikes; a cliff past 75 kHz is a station "
            "running hot. Amber: peaks of frequent recurrence (reached at least once a second). Red: the "
            "highest peak seen.",
    override="Tune anywhere the dongle can reach instead of stopping at the band edges, 100 kHz to "
             "1766 MHz. With direct sampling (AM source direct_q), both bands cross over at 28.8 MHz: "
             "direct sampling below, the quadrature tuner above, and crossing restarts the receiver for "
             "a moment. AM above 28.8 MHz covers the aircraft band; FM is still wideband FM anywhere. "
             "Up/Down steps past the band edge, FREQ entry accepts any of it, and the dot matrix spells "
             "out the exact frequency whenever you're off the band.",
    amstereo="Which AM stereo decoder. AUTO picks by the pilot, like the multi-system radios did: 25 Hz is "
             "Motorola C-QUAM, 15 Hz is Kahn/Hazeltine ISB (left on the lower sideband, right on the upper). "
             "Forcing one decodes with it whatever the station sends.",
    ib="Catches the huge peaks: static crashes and glitches far beyond anything a legal AM station "
       "sends (over +300%), patched before the carrier lock sees them. With C-QUAM stereo it also "
       "briefly mutes the stereo difference around a phase glitch, the kind a carrier hiccup causes. "
       "It never touches normal program, so it's on by default.",
    nb="For frequent smaller pulses: ignition, electric fences, power-line and switching-supply buzz. "
       "It finds them at the dongle's full bandwidth, where they're tallest, and patches them before "
       "the channel filter can stretch them into ticks. LOW, MED and HIGH set how small a pulse it "
       "goes after. If it ever starts patching too much, it backs itself off. Off by default; turn "
       "it up when there's noise to fight.",
    amwidth="The widest the AM filter opens when the Wide key is off. It still narrows on its own "
            "for noise and loud neighbours. 8 kHz is AMAX-class wideband, and it keeps the 10 kHz "
            "whistle from adjacent stations out of the audio. 6 kHz suits a crowded night band; "
            "5 kHz is what most AM radios do.",
    nrsc="Undo the treble boost that AM stations following NRSC-1 transmit (a modified 75 \u00b5s "
         "curve, +10 dB at 10 kHz), so the full-width AM filter sounds natural instead of bright. "
         "Not every station uses it: if a station sounds dull with this on, it probably doesn't. "
         "AM only; the meters follow it, so they measure what you hear.",
    sync="Meters and the tuner's received indicators (STEREO, TUNED, RDS, the signal ladder and the "
         "RDS text) are held back by however much audio GNU Radio has queued on its way to the sound "
         "card, measured as it plays, so what you see matches what you hear. The sound card's own "
         "buffer, or Bluetooth, isn't counted.",
    glow="How much the tube's light scatters in the glass: a halo around everything lit, and a wider "
         "haze. 0 turns it off.",
    pkmode="What PEAK MOD on the monitor shows. FREQUENT: peaks of frequent recurrence, the FCC's "
           "wording for the 100% limit (the highest level reached at least once a second over 10 s). "
           "HIGHEST: the single highest peak in the last 2 s.",
    swatch="Click to choose this element's colour; right-click for its default. Unlit segments take a "
           "faint tint of the same colour, like a coloured filter over a real VFD.",
    rdsscale="How RDS injection is scaled. IEC 62106: the peak of the standard RDS waveform. "
             "STEREO TOOL: about 1.24 times that, which matches Stereo Tool's RDS level setting.",
    sw_blend="Blend toward mono when the signal gets noisy, like a car radio.",
    sw_hicut="Roll off the treble when the signal gets noisy.",
    sw_nr="Spectral noise reduction on weak stations.",
    sw_static="Trim treble and stereo during brief dropouts, and stereo during multipath bursts.",
    sw_eq="Echo canceller (adaptive equalizer). AUTO uses it only when it measurably helps.",
    sw_bw="Receiver filter width. AUTO narrows only on weak stations and never on loud music.",
    bars="Overall reception, strength and quality together, like the bars on a phone. "
         "Five bars is a clean, strong station; one bar is barely there.",
    rds_ps="The station's name as it sends it over RDS (some stations scroll it), with the call "
           "letters, frequency and program type.",
    rds_rt="RadioText: what's playing, or whatever message the station is sending.",
    rds_detail="PI: the station's RDS code. TP / TA: traffic program, and a traffic announcement in "
               "progress. Music or speech. Clock: the station's own clock. AF: other frequencies "
               "carrying the same station. Groups per second (about 11 when all get through) and the "
               "share of blocks lost to noise or echoes.",
    lamp_stereo="The station's stereo pilot is received and stereo is playing.",
    lamp_rds="RDS data (station name, song text) is being decoded.",
    lamp_echo="The echo canceller is in use.",
    lamp_nr="Noise reduction is removing hiss.",
    lamp_static="Static cut is trimming a dropout or a multipath burst.",
    lamp_mp="Echoes (multipath) detected on this station.",
    lamp_agc="Automatic gain control is setting the tuner gain.",
    lamp_clip="The tuner is overloading. AGC will back the gain off.",
    lamp_lock="The receiver has locked onto the AM carrier.",
    lamp_amst="C-QUAM AM stereo is playing.",
    lamp_pilot="The C-QUAM stereo pilot is present.",
)


# ---------------------------------------------------------------------------
# Loudness (BS.1770-4 / EBU R128) from the meter's 100 ms block energies
# ---------------------------------------------------------------------------

def _db(x, floor=-120.0):
    return 20 * math.log10(x) if x > 1e-12 else floor


def _lufs(e):
    return -0.691 + 10 * math.log10(e) if e is not None and e > 0 else None


class Loudness:
    ABS_GATE = -70.0
    KEEP_MAX = 144000                     # four hours of 100 ms steps

    def __init__(self):
        self.reset()

    def reset(self):
        self.blocks = deque(maxlen=30)    # the last 3 s of block energies
        self.mom = []                     # 400 ms energies, one per 100 ms, since reset
        self.short = []                   # 3 s energies, one per 100 ms, since reset
        self._cache, self._t = (None, None), -1e9

    def feed(self, energies, keep):
        for e in energies:
            self.blocks.append(e)
            if keep:
                b = list(self.blocks)
                if len(b) >= 4:
                    self.mom.append(sum(b[-4:]) / 4)
                if len(b) == 30:
                    self.short.append(sum(b) / 30)
        for lst in (self.mom, self.short):
            if len(lst) > self.KEEP_MAX:
                del lst[:len(lst) - self.KEEP_MAX]

    def momentary(self):
        b = list(self.blocks)[-4:]
        return _lufs(sum(b) / 4) if len(b) == 4 else None

    def short_term(self):
        return _lufs(sum(self.blocks) / 30) if len(self.blocks) == 30 else None

    def _gated(self, now):
        if now - self._t < LOUD_RECALC_S:
            return self._cache
        self._t = now
        integ = lra = None
        if len(self.mom) >= 4:
            e = np.asarray(self.mom)
            L = -0.691 + 10 * np.log10(np.maximum(e, 1e-20))
            a = e[L > self.ABS_GATE]
            if len(a):
                rel = _lufs(float(a.mean())) - 10.0
                b = e[(L > self.ABS_GATE) & (L > rel)]
                integ = _lufs(float(b.mean())) if len(b) else None
        if len(self.short) >= 20:
            e = np.asarray(self.short)
            L = -0.691 + 10 * np.log10(np.maximum(e, 1e-20))
            g = L > self.ABS_GATE
            if np.any(g):
                rel = _lufs(float(e[g].mean())) - 20.0
                v = L[g & (L > rel)]
                if len(v) >= 2:
                    lra = float(np.percentile(v, 95) - np.percentile(v, 10))
        self._cache = (integ, lra)
        return self._cache

    def integrated(self, now):
        return self._gated(now)[0]

    def lra(self, now):
        return self._gated(now)[1]


class DelayLine:
    """Values stamped with the time they were measured, read back as of an
    earlier time (requests move forward, so old entries are dropped as read)."""

    def __init__(self):
        self.q = deque()

    def push(self, t, v):
        self.q.append((t, v))
        if len(self.q) > 2000:
            self.q.popleft()

    def at(self, t):
        q = self.q
        while len(q) >= 2 and q[1][0] <= t:
            q.popleft()
        return q[0][1] if q else None


# ---------------------------------------------------------------------------
# Monitor arithmetic: ballistics, holds, overs, offsets. No Tk in here.
# ---------------------------------------------------------------------------

class MonitorLogic:
    def __init__(self, mode='ppm', hold=True):
        self.mode, self.hold_on = mode, hold
        self.loud = Loudness()
        self.reset()

    def reset(self):
        """A retune or the Reset key: start the program measurements over."""
        self.loud.reset()
        self.tp_max = None
        self.over_n = self.total_n = 0
        self.mod_hist = deque()               # (t, pct, raw, pos, neg)
        self.mod_maxpos = self.mod_maxneg = None
        self.off_hist = deque()               # (t, sum, n)
        self.last_over = -1e9
        self.ppm = [-120.0, -120.0]
        self.vu = [[0.0, 0.0], [0.0, 0.0]]
        self.hold = [(-120.0, 0.0), (-120.0, 0.0)]
        self.mod_bar, self.mod_bar_hold = 0.0, (0.0, 0.0)
        self.mod_valid = False
        self.corr, self.gonio = None, None
        self.peak_hist = deque()              # (t, histogram of peak heights, carrier offset)
        self._dist, self._dist_t, self._dist_side = None, -1e9, None

    def level(self, ch):
        return self.ppm[ch] if self.mode == 'ppm' else _db(self.vu[ch][1])

    def _hold_step(self, ch, now, dt):
        cur = self.level(ch)
        hv, ht = self.hold[ch]
        if cur >= hv:
            self.hold[ch] = (cur, now)
        elif now - ht > PEAK_HOLD_S:
            self.hold[ch] = (max(cur, hv - PPM_FALL_DB_S * dt), ht)

    def idle(self, now, dt):
        """No readings (settling, searching, off): let everything fall."""
        for ch in (0, 1):
            self.ppm[ch] = max(-120.0, self.ppm[ch] - PPM_FALL_DB_S * dt)
            a = 1 - math.exp(-dt / VU_TAU_S)
            s1 = self.vu[ch][0] * (1 - a)
            self.vu[ch] = [s1, self.vu[ch][1] + a * (s1 - self.vu[ch][1])]
            self._hold_step(ch, now, dt)
        self.mod_bar = max(0.0, self.mod_bar - 60.0 * dt)

    def feed(self, m, now, dt, band, keep, mod_valid):
        a, md = m['audio'], m['mod']
        if a['n']:
            al = 1 - math.exp(-dt / VU_TAU_S)
            for ch in (0, 1):
                self.ppm[ch] = max(_db(a['peak'][ch]), self.ppm[ch] - PPM_FALL_DB_S * dt)
                rms = math.sqrt(max(a['ms'][ch], 0.0))
                s1 = self.vu[ch][0] + al * (rms - self.vu[ch][0])
                self.vu[ch] = [s1, self.vu[ch][1] + al * (s1 - self.vu[ch][1])]
                self._hold_step(ch, now, dt)
            if a['corr'] is not None:
                c = 1 - math.exp(-dt / CORR_TAU_S)
                self.corr = a['corr'] if self.corr is None else self.corr + c * (a['corr'] - self.corr)
            self.gonio = a['gonio']
            if keep:
                tp = _db(max(a['true_peak']))
                self.tp_max = tp if self.tp_max is None else max(self.tp_max, tp)
                if tp > 0.0:
                    self.last_over = now
        else:
            self.idle(now, dt)
        self.loud.feed(a['blocks'], keep)

        self.mod_valid = bool(mod_valid)
        off = 0.0
        if band == 'FM' and md['n']:           # peaks from the carrier's centre, not from 0 Hz
            off = self.offset_khz()
            if off is None:
                off = md['mean'] or 0.0
        if md['n'] and mod_valid:
            pos, neg = md['max'] - off, off - md['min']
            if band == 'AM':                   # AM: the negative side (clicks only go up)
                pct = raw = neg
            if md.get('peaks') is not None:
                self.peak_hist.append((now, md['peaks'], off))
                while self.peak_hist and now - self.peak_hist[0][0] > MOD_DIST_S:
                    self.peak_hist.popleft()
            if band == 'FM':
                pct, raw = max(pos, neg) / 75.0 * 100.0, max(pos, neg)
            self.mod_hist.append((now, pct, raw, pos, neg))
            while self.mod_hist and now - self.mod_hist[0][0] > MOD_HOLD_S:
                self.mod_hist.popleft()
            self.mod_bar = max(pct, self.mod_bar - 60.0 * dt)
            hv, ht = self.mod_bar_hold
            if pct >= hv:
                self.mod_bar_hold = (pct, now)
            elif now - ht > PEAK_HOLD_S:
                self.mod_bar_hold = (max(pct, hv - 60.0 * dt), ht)
            if keep:
                self.over_n += md['over']
                self.total_n += md['n']
                if md['over']:
                    self.last_over = now
                self.mod_maxpos = pos if self.mod_maxpos is None else max(self.mod_maxpos, pos)
                self.mod_maxneg = neg if self.mod_maxneg is None else max(self.mod_maxneg, neg)
        else:
            self.mod_bar = max(0.0, self.mod_bar - 60.0 * dt)
            self.mod_bar_hold = (self.mod_bar, now)
            self.mod_hist.clear()
            self.peak_hist.clear()
        # The average frequency stays sound under noise and multipath, which only
        # spoil the peaks: carrier offset keeps measuring while tuned.
        if keep and band == 'FM' and md['n'] and md['mean'] is not None:
            self.off_hist.append((now, md['mean'] * md['n'], md['n']))
            while self.off_hist and now - self.off_hist[0][0] > OFFSET_AVG_S:
                self.off_hist.popleft()

    def mod_peak(self):
        """Highest peak in the hold window: (percent, display value, +peak, -peak)."""
        if not self.mod_hist:
            return None
        i = max(range(len(self.mod_hist)), key=lambda j: self.mod_hist[j][1])
        _, pct, raw, _, _ = self.mod_hist[i]
        return pct, raw, max(h[3] for h in self.mod_hist), max(h[4] for h in self.mod_hist)

    def distribution(self, now, side='both'):
        """(levels, peaks per second reaching each level, seconds covered), measured
        from the carrier's centre; None until 2 s have been counted. side='neg'
        counts only the troughs (AM's negative modulation)."""
        if now - self._dist_t < 0.25 and self._dist is not None and self._dist_side == side:
            return self._dist
        self._dist_t, self._dist_side = now, side
        if len(self.peak_hist) < 2 or now - self.peak_hist[0][0] < 2.0:
            self._dist = None
            return None
        nb = int(round(PEAK_SPAN / PEAK_STEP)) + 1
        centres = -PEAK_SPAN + (np.arange(int(round(2 * PEAK_SPAN / PEAK_STEP))) + 0.5) * PEAK_STEP
        mag = np.zeros(nb)
        for _, h, off in self.peak_hist:
            v = centres - off
            if side == 'neg':
                h = np.where(v < 0, h, 0)
            j = np.minimum((np.abs(v) / PEAK_STEP).astype(np.int64), nb - 1)
            mag += np.bincount(j, weights=h, minlength=nb)[:nb]
        secs = max(0.5, now - self.peak_hist[0][0])
        rate = np.cumsum(mag[::-1])[::-1] / secs                 # peaks per second at or above
        self._dist = (np.arange(nb) * PEAK_STEP, rate, secs)
        return self._dist

    def frequent_peak(self, now, side='both'):
        """Peaks of frequent recurrence: the highest level reached FREQ_PEAK_RATE times a second."""
        d = self.distribution(now, side)
        if d is None:
            return None
        levels, rate, _ = d
        ok = np.nonzero(rate >= FREQ_PEAK_RATE)[0]
        return float(levels[ok[-1]] + PEAK_STEP / 2) if len(ok) else None

    def offset_khz(self):
        n = sum(h[2] for h in self.off_hist)
        return sum(h[1] for h in self.off_hist) / n if n else None

    def over_pct(self):
        return 100.0 * self.over_n / self.total_n if self.total_n else None


# ---------------------------------------------------------------------------
# The tuner and monitor
# ---------------------------------------------------------------------------

class TunerApp:
    def __init__(self, root, scale=1.0, freq=None, rf_gain=None, agc=None, lmr_gain=1.0,
                 log_path=None, record_iq=None, am_source=None, upconverter_hz=None):
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
        self.colors     = dict(saved.get('colors', {}))
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
        if raw.get('am_normal_hz') in (5000, 6000, 8000):
            out['am_normal_hz'] = raw['am_normal_hz']
        if raw.get('rds_scale') in ('standard', 'stereo_tool'):
            out['rds_scale'] = raw['rds_scale']
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
            'colors': dict(sorted(self.colors.items())),
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
                                         am_source=am_src, upconverter_hz=self.upconverter_hz)
                else:
                    self.tb = FMStereoReceiver(freq=self.freq, rf_gain=self.rf_gain, agc=self.agc_on,
                                               device_args=fm_args, **self._backend_args)
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
            except Exception:
                self._error_count += 1
                if self._error_count >= self._MAX_ERRORS:
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
        if tb is not None and self.band == 'FM' and hasattr(tb, 'target_lmr'):
            tb.target_lmr = 0.0 if self.fm_mono else self.lmr_gain
        if tb is not None and hasattr(tb, 'set_force_mono'):
            tb.set_force_mono(self.fm_mono)

    def _apply_output(self):
        if self.tb is not None:
            g = 10 ** (self.output_db / 20)
            self.tb.set_output_gain(g, g)

    def _apply_dsp(self, tb):
        tb.set_enhancements(blend=self.blend_on, hicut=self.hicut_on)
        tb.set_eq_mode(self.eq_mode)
        if hasattr(tb, 'set_if_mode'):
            tb.set_if_mode(self.if_mode)
        if hasattr(tb, 'set_dip_hicut'):
            tb.set_dip_hicut(self.dip_hicut)
            tb.set_noise_reduction(self.noise_red)
        if hasattr(tb, 'set_nrsc_deemphasis'):
            tb.set_nrsc_deemphasis(self.am_nrsc)
        if hasattr(tb, 'set_auto_ceiling'):
            tb.set_auto_ceiling(self.am_normal_hz)
            tb.set_wide(self.am_wide)
        if hasattr(tb, 'set_stereo_system'):
            tb.set_stereo_system(self.am_stereo)
        if hasattr(tb, 'set_blankers'):
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
                      Hint(c, HINTS[3][1], s, 'FREQ', self._direct_toggle)]

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
                       ('d', self._direct_toggle)):
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
        if self.tb is not None and self.band == 'AM' and hasattr(self.tb, 'set_wide'):
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

    def _vfd_state(self):
        if not self.power_on:
            return None
        now = time.monotonic()
        blink = int(now * 3) % 2 == 0
        tb = self.tb
        if self.band == 'FM' or self.freq >= 2e6:      # AM from 2 MHz up reads in MHz
            v, dp, unit = int(round(self.freq / 1e5)), True, 'MHz'
        else:
            v, dp, unit = int(round(self.freq / 1e3)), False, 'kHz'
        if v > 1999:                                   # past 199.9 MHz: the dot matrix has it
            digits, dp = (' ', '---'), False
        else:
            txt = f'{v:4d}'
            digits = ('1' if txt[0] == '1' else ' ', txt[1:])

        # What the receiver measures now, then shown as of when the audio you're
        # hearing was received (Settings > Panel > Sync to audio)
        rx = dict(stereo=tb is not None and not self._moving() and getattr(tb, 'stereo_indicator', False),
                  tuned=self._tuned(tb, now),
                  rds=(self.band == 'FM' and tb is not None and not self._moving()
                       and getattr(tb, 'rds_synced', False)),
                  sig=self._signal_steps(tb, now), text=self._rds_fields(tb, now))
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

        state = dict(legends=frozenset(legends), fixed=True, unit=unit, digits=digits, dp=dp,
                     sig=shown['sig'], ch=ch, rds=self._live_text(now, blink) or shown['text'])
        if now < self._lamp_until:
            state['all'] = True
        return state

    # ---- direct frequency entry, messages, and the exact frequency off the band
    def _freq_text(self, f=None):
        f = self.freq if f is None else f
        if self.band == 'FM' or f >= 2e6:
            return f"{f / 1e6:.3f} MHz"
        return f"{f / 1e3:.0f} kHz"

    def _message(self, text, secs=2.5):
        self._dm_msg = (text, time.monotonic() + secs)

    def _live_text(self, now, blink):
        """What the dot matrix shows right now, ahead of RDS: an entry in progress,
        a message, or the exact frequency when it's off the band's raster. Live,
        never held back for audio sync."""
        if self._direct is not None:
            unit = 'MHz' if self.band == 'FM' or '.' in self._direct else 'kHz'
            return ('', '  FREQ', f"{self._direct}{'_' if blink else ' '} {unit}")
        if self._dm_msg is not None:
            if now < self._dm_msg[1]:
                return ('', '', self._dm_msg[0])
            self._dm_msg = None
        lo, hi, st = BANDS[self.band]
        if not self._in_band() or abs(self.freq - self._snap_in(self.band, self.freq)) > 1:
            return ('', '', self._freq_text())
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
        if self.band == 'AM':
            if not getattr(tb, 'stereo_indicator', False):
                return ('', '', '')
            return ('', '', 'KAHN ISB STEREO' if getattr(tb, 'stereo_system', '') == 'kahn' else 'C-QUAM STEREO')
        if getattr(tb, 'rds_pi', None) is None:
            return ('', '', '')
        call = (self._call_for(tb) or '')[:4]
        ps = (tb.rds_ps or '').strip()[:8]
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
        if tb is None or not hasattr(tb, 'audio_lag'):
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
        if tb is not None and hasattr(tb, 'monitor'):
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
        if not hasattr(tb, 'eq_taps'):
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
        idl = tk.Label(r1, textvariable=self._id_var, font=(UI_FAMILY, 9, 'bold'), fg='#8fbfb6',
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
                       bg=DASH_BG, anchor='w', width=104)
        det.pack(fill=tk.X, padx=(12, 8), pady=(1, 6))
        tips.bind(det, TIPS['rds_detail'])

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
        self._spec_title = tk.Label(head, text='', font=FACE_FONT, fg=FACE_FG, bg=P)
        self._spec_title.pack(side=tk.LEFT)
        self._spec_mode = tk.StringVar(value='MPX')
        rb = dict(indicatoron=False, font=FACE_FONT, fg='#cfcfcf', bg='#2a2a2a', selectcolor='#004422',
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
        tk.Label(h2, text='CHANNEL BEFORE / AFTER THE EQUALIZER', font=FACE_FONT, fg=FACE_FG,
                 bg=P).pack(side=tk.LEFT)
        tk.Label(h2, text='ECHO PROFILE', font=FACE_FONT, fg=FACE_FG, bg=P, width=24,
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
        tk.Label(h3, text='MODULATION: HOW OFTEN EACH LEVEL IS REACHED', font=FACE_FONT, fg=FACE_FG,
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
        rb = dict(indicatoron=False, font=FACE_FONT, fg='#cfcfcf', bg='#2a2a2a', selectcolor='#004422',
                  activebackground='#004422', activeforeground='#ffffff', relief=tk.FLAT)
        cb = dict(font=FACE_FONT, fg='#cfcfcf', bg=P, selectcolor='#2a2a2a',
                  activebackground=P, activeforeground='#ffffff', highlightthickness=0, bd=0)
        lab = dict(font=FACE_FONT, fg=FACE_FG, bg=P)

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
        r = row(t, 'BANDWIDTH')
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
        r = row(t, 'AM BLANKERS')
        self._ib_var = tk.BooleanVar(value=self.am_ib)
        w = tk.Checkbutton(r, text='IMPULSE', variable=self._ib_var, command=self._blankers_changed, **cb)
        w.pack(side=tk.LEFT)
        tips.bind(w, TIPS['ib'])
        w = tk.Label(r, text='NOISE', **lab)
        w.pack(side=tk.LEFT, padx=(14, 4))
        tips.bind(w, TIPS['nb'])
        self._nb_var = tk.StringVar(value=self.am_nb)
        for val, caption in (('off', 'OFF'), ('low', 'LOW'), ('med', 'MED'), ('high', 'HIGH')):
            w = tk.Radiobutton(r, text=caption, value=val, variable=self._nb_var, width=6,
                               command=self._blankers_changed, **rb)
            w.pack(side=tk.LEFT, padx=1)
            tips.bind(w, TIPS['nb'])
        r = row(t, 'AM NORMAL WIDTH')
        self._amw_var = tk.IntVar(value=int(self.am_normal_hz))
        for hz in (5000, 6000, 8000):
            w = tk.Radiobutton(r, text=f'{hz // 1000} kHz', value=hz, variable=self._amw_var, width=7,
                               command=self._am_width_changed, **rb)
            w.pack(side=tk.LEFT, padx=1)
            tips.bind(w, TIPS['amwidth'])
        r = row(t, 'TUNER GAIN')
        self.agc_btn = SilverButton(r, size=BUTTON_SMALL, text='AGC', font=FACE_FONT, command=self._toggle_agc)
        self.agc_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._style_agc_btn()
        self.gain_var = tk.DoubleVar(value=self.rf_gain)
        tk.Scale(r, variable=self.gain_var, from_=0, to=49.6, resolution=0.1, showvalue=False,
                 orient=tk.HORIZONTAL, length=220, bg=P, fg='#555555', troughcolor='#2a2a2a',
                 highlightthickness=0, command=self._on_gain_change).pack(side=tk.LEFT)
        self.gain_label = tk.Label(r, text='', font=('Courier', 9), fg='#7fbfb3', bg=P, width=11, anchor='w')
        self.gain_label.pack(side=tk.LEFT, padx=(6, 0))
        self._update_gain_label(self.rf_gain)

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
                tx = tk.Label(fr, text=label, font=(UI_FAMILY, 9), fg='#cfcfcf', bg=P, anchor='w')
                tx.pack(side=tk.LEFT, padx=(8, 0))
                sw.bind('<ButtonRelease-1>', lambda e, k=key, l=label: self._pick_color(k, l))
                for seq in ('<Button-3>', '<Button-2>'):
                    sw.bind(seq, lambda e, k=key: self._reset_color(k))
                tips.bind(sw, TIPS['swatch'])
                self._swatches[key] = (sw, tx, label)
            tk.Button(col, text='DEFAULTS', font=FACE_FONT, fg='#cfcfcf', bg='#2a2a2a', relief=tk.FLAT,
                      activebackground='#3a3a3a', activeforeground='#ffffff',
                      command=lambda pn=panel: self._reset_panel(pn)).pack(anchor='w', pady=(8, 0))
        tk.Label(t, text='Click a swatch to choose its colour; right-click it for the default.',
                 font=(UI_FAMILY, 8), fg='#6f8f8a', bg=P).pack(anchor='w', pady=(10, 0))
        self._refresh_swatches()

        # every tab in the same cell, the cell sized for the largest
        win.update_idletasks()
        body.grid_columnconfigure(0, minsize=max(f.winfo_reqwidth() for f in tabs.values()))
        body.grid_rowconfigure(0, minsize=max(f.winfo_reqheight() for f in tabs.values()))
        show()

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
        self.am_ib, self.am_nb = bool(self._ib_var.get()), self._nb_var.get()
        if self.tb is not None and hasattr(self.tb, 'set_blankers'):
            self.tb.set_blankers(impulse=self.am_ib, noise=self.am_nb)
        self._save_settings()

    def _am_stereo_changed(self):
        self.am_stereo = self._ams_var.get()
        if self.tb is not None and hasattr(self.tb, 'set_stereo_system'):
            self.tb.set_stereo_system(self.am_stereo)
        self._save_settings()

    def _am_width_changed(self):
        self.am_normal_hz = int(self._amw_var.get())
        if self.tb is not None and hasattr(self.tb, 'set_auto_ceiling'):
            self.tb.set_auto_ceiling(self.am_normal_hz)
        self._save_settings()

    def _nrsc_changed(self):
        self.am_nrsc = bool(self._nrsc_var.get())
        if self.tb is not None and hasattr(self.tb, 'set_nrsc_deemphasis'):
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
        if self.tb:
            self.tb.set_enhancements(blend=self.blend_on, hicut=self.hicut_on)
            self.tb.set_eq_mode(self.eq_mode)
        self._save_settings()

    def _fm_assist_changed(self):
        self.dip_hicut, self.noise_red = self._nb_var.get(), self._nr_var.get()
        if self.tb and hasattr(self.tb, 'set_dip_hicut'):
            self.tb.set_dip_hicut(self.dip_hicut)
            self.tb.set_noise_reduction(self.noise_red)
        self._save_settings()

    def _if_changed(self):
        self.if_mode = self._if_var.get()
        if self.tb and hasattr(self.tb, 'set_if_mode'):
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
            cv.create_text(7, 6, anchor='nw', text=titles[k], font=(UI_FAMILY, 8, 'bold'), fill=col)
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
        cv.create_text(7, 6, anchor='nw', text='LAST 30 SECONDS', font=(UI_FAMILY, 8, 'bold'), fill='#8fbfb6')
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

    def _rds_show(self, tb):
        mhz = f"{self.freq / 1e6:.1f} MHz"
        pi = tb.rds_pi
        if pi is None:
            self._ps_var.set('')
            self._id_var.set(mhz + ('\nRDS found, reading...' if tb.rds_synced else '\nno RDS yet'))
            self._rt_var.set('')
            self._rdsd_var.set('')
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
        self._rdsd_var.set(f"PI {pi:04X}   TP {flag(tb.rds_tp)}  TA {flag(tb.rds_ta)}   {ms}   "
                          f"clock {ct}   AF {afs}   {tb.rds_rate:4.1f} groups/s   {tb.rds_bler:3.0f}% lost")

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
            self._lamps.set_lamps([
                ('lock', 'LOCKED', SEG_ON['good'], TIPS['lamp_lock']),
                ('amst', 'AM STEREO', SEG_ON['help'], TIPS['lamp_amst']),
                ('pilot', 'PILOT', SEG_ON['help'], TIPS['lamp_pilot']),
                ('agc', 'AGC', SEG_ON['good'], TIPS['lamp_agc']),
                ('blank', 'BLANKER', SEG_ON['fair'], TIPS['lamp_blank']),
                ('clip', 'CLIP', SEG_ON['poor'], TIPS['lamp_clip'])])
            ibr, nbp, sgr = tb.blanker_stats() if hasattr(tb, 'blanker_stats') else (0.0, 0.0, 0.0)
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
                ('st', 'Stereo', 'help', TIPS['am_stereo'], None),
                ('ib', 'Impulse blanker', 'low', TIPS['am_ib'], None),
                ('nb', 'Noise blanker', 'low', TIPS['am_nb'], None)])
            q = tb.cnr_db
            self._meters.show('q', q / 40, f"{q:4.1f} dB")
            self._meters.show('lvl', (tb.channel_dbfs + 60) / 60, f"{tb.channel_dbfs:5.1f}")
            self._meters.show('adj', (tb.adj_db + 40) / 50, f"{tb.adj_db:+4.0f} dB")
            bw = tb.bw / 1e3
            self._meters.show('bw', bw / 10, 'wide' if tb.bw >= tb.AM_WIDE_HZ else f"{bw:4.1f} kHz")
            self._meters.show('pil', tb.pilot_ratio_db / 30 if tb.pilot_on else 0,
                              f"{100 * tb.pilot_level:4.1f}%" if tb.pilot_on else 'none')
            self._meters.show('st', tb.current_blend if tb.pilot_on else 0,
                              f"{100 * tb.current_blend:3.0f}%" if tb.pilot_on else 'mono')
            on_ib, on_nb = getattr(tb, 'ib_on', False), getattr(tb, 'nb_level', 'off') != 'off'
            self._meters.show('ib', min(1.0, (ibr + sgr) / 60) if on_ib else 0,
                              f"{ibr + sgr:4.0f}/min" if on_ib else 'off')
            self._meters.show('nb', min(1.0, nbp / 1.0) if on_nb else 0, f"{nbp:4.2f}%" if on_nb else 'off')
            self._signal_bars(q, None)
            self._ps_var.set(f"{self.freq / 1e3:.0f} AM")
            self._id_var.set((('KAHN ISB AM STEREO' if getattr(tb, 'stereo_system', '') == 'kahn' else 'C-QUAM AM STEREO')
                              if tb.stereo_indicator else
                              ('Kahn ISB' if getattr(tb, 'stereo_system', '') == 'kahn' else 'C-QUAM') + ', blended to mono'
                              if tb.pilot_on else 'AM, mono')
                             + ('\ncarrier locked' if getattr(tb, 'locked', False) else '\nsearching for carrier'))
            src = {'direct_q': 'Q-branch direct sampling', 'native': 'quadrature tuner',
                   'upconverter': f"upconverter +{tb.UPCONVERTER_HZ / 1e6:.0f} MHz"}.get(tb.am_source, '')
            self._rt_var.set(f"Carrier offset {tb.carrier_hz:+.0f} Hz   level {tb.channel_dbfs:.1f} dBFS")
            self._rdsd_var.set(f"Source: {src}   No RDS on AM")
            self._hist_pts.append((q, q, tb.stereo_indicator))
            self._hist_draw(True)
            return

        self._lamps.set_lamps([
            ('stereo', 'STEREO', SEG_ON['help'], TIPS['lamp_stereo']),
            ('rds', 'RDS', SEG_ON['help'], TIPS['lamp_rds']),
            ('echo', 'ECHO FIX', SEG_ON['good'], TIPS['lamp_echo']),
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
            ('st', 'Stereo', 'help', TIPS['stereo'], None),
            ('tr', 'Treble', 'help', TIPS['treble'], None),
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
                           font=(UI_FAMILY, 9, 'bold'), fill='#3f6f68')
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
        h, wgt = tb.eq_taps() if hasattr(tb, 'eq_taps') else (None, 0.0)
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
                           font=(UI_FAMILY, 9, 'bold'), fill='#3f6f68')
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
                           font=(UI_FAMILY, 9, 'bold'), fill='#3f6f68')
            return
        if not mon.mod_valid:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, font=(UI_FAMILY, 9, 'bold'), fill='#5f9f95',
                           text='not measurable: noise or multipath ahead of the equalizer')
            return
        now = time.monotonic()
        d = mon.distribution(now)
        if d is None:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text='counting...', font=(UI_FAMILY, 9, 'bold'),
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
    a = ap.parse_args()

    root = tk.Tk()
    scale = a.scale
    if scale is None:
        scale = min(1.0, (root.winfo_screenwidth() - 40) / BASE_W,
                    (root.winfo_screenheight() - 120) / (2 * BASE_H + RACK_GAP))
    TunerApp(root, scale=scale, freq=a.freq, rf_gain=a.gain,
             agc=False if a.no_agc else None, lmr_gain=a.lmr_gain,
             log_path=a.log, record_iq=a.record_iq,
             am_source=a.am_source, upconverter_hz=a.upconverter_hz)
    root.mainloop()


if __name__ == '__main__':
    main()
