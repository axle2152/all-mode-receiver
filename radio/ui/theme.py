"""
radio/ui/theme.py - Artwork geometry, colours and the panel layout.
"""
import numpy as np


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
# the signal bar and channel, and a dot-matrix row for RDS underneath.
# The frequency (radio/ui/frequency.py): seven big digits down to 1 kHz, each with a decimal
# point after it, then three small digits for what's below 1 kHz, against the unit.
LEG_Y      = 16
LEGENDS    = (('FM', 20), ('AM', 44), ('AUTO', 82), ('STEREO', 124), ('MONO', 178), ('RDS', 222),
              ('WIDE', 252), ('TUNED', 300), ('MEMORY', 492))
DIG_TOP, DIG_H, DIG_W, DIG_T = 24, 46, 24, 4.4
DIG_SLANT  = 0.18                     # italic, about 10 degrees
DIG_X      = tuple(10 + i * 35 for i in range(7))
FINE_TOP, FINE_H, FINE_W, FINE_T = 42, 28, 14, 3.0
FINE_X     = (263, 282, 301)
DP_GAP, DP_SIZE = 3.0, 4.4            # a decimal point sits this far right of its digit's foot
UNIT_X, KHZ_Y, MHZ_Y = 324, 50, 70
SIG_X, SIG_W, SIG_PITCH, SIG_H0, SIG_H1, SIG_BOT = 362, 7.5, 10.5, 8, 30, 62
SIG_RISE   = 9                        # the first 9 bars rise; the rest stand at full height
SIG_LABEL_X, SIG_LABEL_Y = 362, 74
CH_LABEL_X  = 529
CH_TOP, CH_H, CH_W, CH_T = 44, 28, 13, 3.0
CH_X        = (548, 567)
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
FACE_FG      = '#9a9a9a'
BUTTON_SMALL = (56, 24)
BUTTON_TRIM  = '#000000'
BUTTON_BRUSH = 0.11
THEME = dict(tints={None: ('#f2f2f2', '#a4a4a4'), 'green': ('#cdf3da', '#4f9f6a')},
             ink='#202020', bevel=('#ffffff', '#6a6a6a'), bevel_down=('#7a7a7a', '#e0e0e0'))
