"""
radio/ui/constants.py - Tuning, timing and monitor constants, and where settings are kept.
"""
import os

from .. import TOP_DIR


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
AM_WIDTHS  = (2500, 5000, 8000, 10200)     # Settings > AM width (Hz); the Wide key is 12 kHz

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
SIG_RANGE      = {'FM': (3.0, 33.0), 'AM': (7.5, 43.5)}   # AM was (12, 48) before its C/N was corrected
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
AM_MOD_MIN_CNR_DB = 35.5                  # AM below this (C/N in 10 kHz): noise pushes peaks >5% high: NOISY
                                          # (40 before the AM C/N was corrected, 4.5 dB lower now)
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

_ASSET_DIR = TOP_DIR                       # beside quartz_tuner_ui.py, not in this package
SETTINGS_NAME     = 'quartz_tuner_settings.json'
SETTINGS_FALLBACK = os.path.join(os.path.expanduser('~'), '.' + SETTINGS_NAME)
DELCO_SETTINGS    = [os.path.join(_ASSET_DIR, 'fm_radio_settings.json'),
                     os.path.join(os.path.expanduser('~'), '.fm_radio_settings.json')]
