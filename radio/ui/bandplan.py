"""
radio/ui/bandplan.py - the bands behind the Bands panel, and the modes they're played in
=========================================================================================
Each band key tunes to a band: its edges, its tuning step, the mode it's usually heard in,
and the parts of it with a usual mode of their own (the CW end of an amateur band, say).
Picking a band, or tuning into one of those parts, picks that mode; where there's no usual
mode, the radio stays in the one it's in.

The Americas (ITU Region 2, US channel plans and amateur privileges, simplified to where each
mode is usually heard). Other regions can come later (Settings > Region, docs/ROADMAP.md).

Modes, and the receiver each needs (the receivers declare BAND = 'AM', 'FM', ...):

    AM     AM                        receiver 'AM'
    SAM_U  synchronous AM, upper     receiver 'HF' (in this order, AM's key steps AM, SAM_U, SAM_L)
    SAM_L  synchronous AM, lower     receiver 'HF'
    FM     broadcast FM (+/-75 kHz)  receiver 'FM'
    NFM    narrow FM (+/-5 kHz)      receiver 'NFM'  (FM's key steps FM, NFM, NFM2)
    NFM2   narrow FM (+/-2.5 kHz)    receiver 'NFM'
    USB    upper sideband            receiver 'HF'   (SSB's key steps USB, LSB)
    LSB    lower sideband            receiver 'HF'
    CW     Morse                     receiver 'HF'
"""
from collections import namedtuple

MODES = ('AM', 'SAM_U', 'SAM_L', 'FM', 'NFM', 'NFM2', 'USB', 'LSB', 'CW')
MODE_RX = {'AM': 'AM', 'SAM_U': 'HF', 'SAM_L': 'HF', 'FM': 'FM', 'NFM': 'NFM', 'NFM2': 'NFM',
           'USB': 'HF', 'LSB': 'HF', 'CW': 'HF'}
MODE_KEYS = ('AM', 'FM', 'SSB', 'CW')                   # the mode keys on the tuner
KEY_MODES = {'AM': ('AM', 'SAM_U', 'SAM_L'), 'FM': ('FM', 'NFM', 'NFM2'), 'SSB': ('USB', 'LSB'),
             'CW': ('CW',)}
MODE_KEY = {m: k for k, ms in KEY_MODES.items() for m in ms}
MODE_LEGEND = {'AM': 'AM', 'SAM_U': 'AM', 'SAM_L': 'AM', 'FM': 'FM', 'NFM': 'FM', 'NFM2': 'FM',
               'USB': 'USB', 'LSB': 'LSB', 'CW': 'CW'}
MODE_TEXT = {'AM': 'AM', 'SAM_U': 'SYNC AM USB', 'SAM_L': 'SYNC AM LSB', 'FM': 'FM',
             'NFM': 'FM NARROW', 'NFM2': 'FM NARROW 2.5', 'USB': 'USB', 'LSB': 'LSB', 'CW': 'CW'}
DATA_MODES = ('AM', 'SAM_U', 'SAM_L', 'FM')             # can carry station data (RDS, PMDS...)

# group: (title on the panel, its row)
GROUPS = {'shortwave': ('SHORTWAVE (METRES)', 0), 'broadcast': ('BROADCAST', 0),
          'amateur': ('AMATEUR (METRES)', 1), 'public': ('PUBLIC SERVICE', 2),
          'personal': ('PERSONAL', 2)}

Band = namedtuple('Band', 'key label group name lo hi step mode parts origin ranges offsets')
# key: unique id; label: on the key; name: on the display; lo, hi, step in Hz; mode: usual mode
# (or None); parts: ((lo, hi, mode), ...) with a usual mode of their own; origin: the channel
# raster starts here (FM's odd tenths), else lo; ranges: for a key that steps through ranges
# ((lo, hi, step, name), ...); offsets: repeater offsets ((lo, hi, Hz), ...)


def _b(key, label, group, name, lo, hi, step, mode, parts=(), origin=None, ranges=(), offsets=()):
    return Band(key, label, group, name, lo, hi, step, mode, tuple(parts), lo if origin is None else origin,
                tuple(ranges), tuple(offsets))


k, M = 1e3, 1e6
# the 2 m repeater sub-bands (US): input below the output on 145.1-145.5 and 146.6-147.0
_2M_OFF = ((145.1 * M, 145.5 * M, -600 * k), (146.0 * M, 146.4 * M, 600 * k), (146.6 * M, 147.0 * M, -600 * k),
           (147.0 * M, 147.4 * M, 600 * k))

BANDS = (
    # ---- shortwave broadcast (ITU), AM, 5 kHz channels
    _b('SW120', '120', 'shortwave', '120 m BROADCAST', 2300 * k, 2495 * k, 5 * k, 'AM'),
    _b('SW90', '90', 'shortwave', '90 m BROADCAST', 3200 * k, 3400 * k, 5 * k, 'AM'),
    _b('SW75', '75', 'shortwave', '75 m BROADCAST', 3900 * k, 4000 * k, 5 * k, 'AM'),
    _b('SW60', '60', 'shortwave', '60 m BROADCAST', 4750 * k, 5060 * k, 5 * k, 'AM'),
    _b('SW49', '49', 'shortwave', '49 m BROADCAST', 5900 * k, 6200 * k, 5 * k, 'AM'),
    _b('SW41', '41', 'shortwave', '41 m BROADCAST', 7200 * k, 7450 * k, 5 * k, 'AM'),
    _b('SW31', '31', 'shortwave', '31 m BROADCAST', 9400 * k, 9900 * k, 5 * k, 'AM'),
    _b('SW25', '25', 'shortwave', '25 m BROADCAST', 11600 * k, 12100 * k, 5 * k, 'AM'),
    _b('SW22', '22', 'shortwave', '22 m BROADCAST', 13570 * k, 13870 * k, 5 * k, 'AM'),
    _b('SW19', '19', 'shortwave', '19 m BROADCAST', 15100 * k, 15800 * k, 5 * k, 'AM'),
    _b('SW16', '16', 'shortwave', '16 m BROADCAST', 17480 * k, 17900 * k, 5 * k, 'AM'),
    _b('SW15', '15', 'shortwave', '15 m BROADCAST', 18900 * k, 19020 * k, 5 * k, 'AM'),
    _b('SW13', '13', 'shortwave', '13 m BROADCAST', 21450 * k, 21850 * k, 5 * k, 'AM'),
    _b('SW11', '11', 'shortwave', '11 m BROADCAST', 25670 * k, 26100 * k, 5 * k, 'AM'),
    # ---- broadcast
    _b('LW', 'LW', 'broadcast', 'LONG WAVE', 153 * k, 279 * k, 9 * k, 'AM'),
    _b('MW', 'MW', 'broadcast', 'MEDIUM WAVE', 530 * k, 1710 * k, 10 * k, 'AM'),
    _b('FM', 'FM', 'broadcast', 'FM BROADCAST', 87.5 * M, 107.9 * M, 200 * k, 'FM'),
    # ---- amateur (US privileges, simplified: CW at the bottom, then phone)
    _b('HAM160', '160', 'amateur', '160 m AMATEUR', 1800 * k, 2000 * k, 1 * k, 'LSB',
       parts=((1800 * k, 1840 * k, 'CW'),)),
    _b('HAM80', '80', 'amateur', '80 m AMATEUR', 3500 * k, 4000 * k, 1 * k, 'LSB',
       parts=((3500 * k, 3600 * k, 'CW'),)),
    _b('HAM60', '60', 'amateur', '60 m AMATEUR', 5330.5 * k, 5406.5 * k, 0.5 * k, 'USB'),
    _b('HAM40', '40', 'amateur', '40 m AMATEUR', 7000 * k, 7300 * k, 1 * k, 'LSB',
       parts=((7000 * k, 7125 * k, 'CW'),)),
    _b('HAM30', '30', 'amateur', '30 m AMATEUR', 10100 * k, 10150 * k, 0.5 * k, 'CW'),
    _b('HAM20', '20', 'amateur', '20 m AMATEUR', 14000 * k, 14350 * k, 1 * k, 'USB',
       parts=((14000 * k, 14150 * k, 'CW'),)),
    _b('HAM17', '17', 'amateur', '17 m AMATEUR', 18068 * k, 18168 * k, 1 * k, 'USB',
       parts=((18068 * k, 18110 * k, 'CW'),)),
    _b('HAM15', '15', 'amateur', '15 m AMATEUR', 21000 * k, 21450 * k, 1 * k, 'USB',
       parts=((21000 * k, 21200 * k, 'CW'),)),
    _b('HAM12', '12', 'amateur', '12 m AMATEUR', 24890 * k, 24990 * k, 1 * k, 'USB',
       parts=((24890 * k, 24930 * k, 'CW'),)),
    _b('HAM10', '10', 'amateur', '10 m AMATEUR', 28000 * k, 29700 * k, 1 * k, 'USB',
       parts=((28000 * k, 28300 * k, 'CW'), (29000 * k, 29200 * k, 'AM'), (29500 * k, 29700 * k, 'NFM')),
       offsets=((29620 * k, 29680 * k, -100 * k),)),
    _b('HAM6', '6', 'amateur', '6 m AMATEUR', 50 * M, 54 * M, 5 * k, 'NFM',
       parts=((50.0 * M, 50.1 * M, 'CW'), (50.1 * M, 50.6 * M, 'USB')),
       offsets=((52.8 * M, 54.0 * M, -1 * M), (51.6 * M, 52.0 * M, -500 * k))),
    _b('HAM2', '2', 'amateur', '2 m AMATEUR', 144 * M, 148 * M, 5 * k, 'NFM',
       parts=((144.0 * M, 144.1 * M, 'CW'), (144.1 * M, 144.3 * M, 'USB')), offsets=_2M_OFF),
    _b('HAM125', '1.25', 'amateur', '1.25 m AMATEUR', 222 * M, 225 * M, 5 * k, 'NFM',
       parts=((222.0 * M, 222.05 * M, 'CW'), (222.05 * M, 222.15 * M, 'USB')),
       offsets=((223.85 * M, 225.0 * M, -1.6 * M),)),
    _b('HAM70', '70cm', 'amateur', '70 cm AMATEUR', 420 * M, 450 * M, 5 * k, 'NFM',
       parts=((432.0 * M, 432.07 * M, 'CW'), (432.07 * M, 433.0 * M, 'USB')),
       offsets=((442 * M, 445 * M, 5 * M), (447 * M, 450 * M, -5 * M))),
    _b('HAM33', '33cm', 'amateur', '33 cm AMATEUR', 902 * M, 928 * M, 12.5 * k, 'NFM',
       parts=((903.0 * M, 903.1 * M, 'CW'), (903.1 * M, 903.4 * M, 'USB')),
       offsets=((927 * M, 928 * M, -25 * M),)),
    _b('HAM23', '23cm', 'amateur', '23 cm AMATEUR', 1240 * M, 1300 * M, 25 * k, 'NFM',
       parts=((1296.0 * M, 1296.05 * M, 'CW'), (1296.05 * M, 1296.3 * M, 'USB')),
       offsets=((1282 * M, 1288 * M, -12 * M),)),
    # ---- public service
    _b('AIR', 'Air', 'public', 'AIR BAND', 118 * M, 137 * M, 25 * k, 'AM'),
    _b('MARINE', 'Marine', 'public', 'MARINE VHF', 156.0 * M, 162.025 * M, 25 * k, 'NFM'),
    _b('WX', 'Weather', 'public', 'NOAA WEATHER', 162.400 * M, 162.550 * M, 25 * k, 'NFM'),
    _b('PUBLIC', 'Police/Fire', 'public', 'PUBLIC SAFETY', 150.8 * M, 174 * M, 12.5 * k, 'NFM2',
       ranges=((150.8 * M, 174 * M, 12.5 * k, 'PUBLIC SAFETY VHF'),
               (450 * M, 470 * M, 12.5 * k, 'PUBLIC SAFETY UHF'),
               (851 * M, 869 * M, 12.5 * k, 'PUBLIC SAFETY 800'))),
    # ---- personal
    _b('CB', 'CB', 'personal', 'CB', 26.965 * M, 27.405 * M, 10 * k, 'AM'),
    _b('FRS', 'FRS/GMRS', 'personal', 'FRS / GMRS', 462.5500 * M, 467.7250 * M, 12.5 * k, 'NFM',
       origin=462.5500 * M, offsets=((462.5500 * M, 462.7250 * M, 5 * M),)),
    _b('MURS', 'MURS', 'personal', 'MURS', 151.820 * M, 154.600 * M, 5 * k, 'NFM'),
)

BY_KEY = {b.key: b for b in BANDS}

# Where a band starts the first time its key is pressed (else its bottom edge): a calling
# or watch channel, or where its phone part begins.
HOME = {'FM': 90.5 * M, 'MW': 1000 * k, 'HAM160': 1900 * k, 'HAM80': 3850 * k, 'HAM40': 7200 * k,
        'HAM20': 14200 * k, 'HAM17': 18130 * k, 'HAM15': 21300 * k, 'HAM12': 24950 * k, 'HAM10': 28400 * k,
        'HAM6': 52.525 * M, 'HAM2': 146.52 * M, 'HAM125': 223.5 * M, 'HAM70': 446.0 * M,
        'HAM33': 927.5 * M, 'HAM23': 1294.5 * M, 'AIR': 118.0 * M, 'MARINE': 156.8 * M, 'WX': 162.55 * M,
        'CB': 27.185 * M, 'FRS': 462.5625 * M, 'MURS': 151.82 * M}


def home(b, i=0):
    """Where band b (or range i of a stepping key) starts the first time."""
    if b.ranges:
        return b.ranges[i % len(b.ranges)][0]
    return HOME.get(b.key, b.lo)


def band(key):
    return BY_KEY[key]


def in_band(b, freq):
    """freq is within the band (or within one of a stepping key's ranges)."""
    return any(lo - 1 <= freq <= hi + 1 for lo, hi in spans(b))


def spans(b):
    return [(lo, hi) for lo, hi, _, _ in b.ranges] if b.ranges else [(b.lo, b.hi)]


def band_for(freq, prefer=None):
    """The band a frequency falls in: `prefer` if it's in it, else the narrowest that holds it."""
    if prefer is not None and prefer in BY_KEY and in_band(BY_KEY[prefer], freq):
        return BY_KEY[prefer]
    held = [b for b in BANDS if in_band(b, freq)]
    return min(held, key=lambda b: b.hi - b.lo) if held else None


def range_for(b, freq):
    """For a key that steps through ranges, the one freq is in: (lo, hi, step, name)."""
    for r in b.ranges:
        if r[0] - 1 <= freq <= r[1] + 1:
            return r
    return (b.lo, b.hi, b.step, b.name)


def usual_mode(b, freq):
    """The mode freq is usually heard in on band b (a part's own, else the band's), or None."""
    for lo, hi, mode in b.parts:
        if lo <= freq < hi:
            return mode
    return b.mode


def snap(b, freq):
    """The nearest channel on band b, within its edges (or its range's)."""
    lo, hi, step, _ = range_for(b, freq)
    origin = b.origin if not b.ranges else lo
    n = round((freq - origin) / step)
    f = origin + n * step
    while f < lo - 1:
        f += step
    while f > hi + 1:
        f -= step
    return round(f, 1)


def step(b, freq):
    return range_for(b, freq)[2]


def repeater_offset(b, freq):
    """The usual repeater offset for an output on freq (Hz, signed), or 0."""
    for lo, hi, off in b.offsets:
        if lo <= freq < hi:
            return off
    return 0.0


def display_name(b, freq):
    return range_for(b, freq)[3]
