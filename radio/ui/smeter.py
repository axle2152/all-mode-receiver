"""
radio/ui/smeter.py - The S-meter: dBm from what the dongle reports, S units, and the bar.

The dongle can't say how strong a signal is in dBm. It reports the level against its own
converter's full scale (dBFS), after whatever gain the tuner had. So:

    dBm = dBFS - tuner gain + calibration

The calibration is one number per input path (direct sampling, the tuner below 30 MHz, the
tuner from 30 MHz up), measured once against a signal of known strength (Settings > Reception
> S-meter). Until then a rough guess stands in and the display lights UNCAL.

S units follow the IARU convention: S9 is -73 dBm below 30 MHz and -93 dBm from there up,
6 dB per S unit down to S1, then 10 dB steps over S9. The bar has 15 segments: S1 to S9, then
+10 to +60.
"""

S9_HF_DBM, S9_VHF_DBM, VHF_FROM_HZ = -73.0, -93.0, 30e6
DB_PER_S = 6.0
N_SEGS = 15                          # S1..S9, +10..+60
GUESS_CAL_DB = 0.0                   # until calibrated: roughly 0 dBm at full scale with no gain
PATHS = {'direct': 'direct sampling', 'hf': 'the tuner below 30 MHz', 'vhf': 'the tuner from 30 MHz up'}


def path_for(direct, freq_hz):
    """Which calibration a reading uses: direct sampling, or the tuner below / above 30 MHz."""
    if direct:
        return 'direct'
    return 'hf' if freq_hz < VHF_FROM_HZ else 'vhf'


def s9_dbm(freq_hz):
    return S9_HF_DBM if freq_hz < VHF_FROM_HZ else S9_VHF_DBM


def to_dbm(dbfs, gain_db, cal_db):
    return dbfs - gain_db + cal_db


def segments(dbm, freq_hz):
    """How many of the bar's 15 segments a level lights, as a float (the window smooths it):
    S1 lights the first, S9 the ninth, S9+60 all fifteen."""
    over = dbm - s9_dbm(freq_hz)
    if over <= 0:
        return max(0.0, 9.0 + over / DB_PER_S)
    return min(float(N_SEGS), 9.0 + over / 10.0)


def s_text(dbm, freq_hz):
    """'S7', 'S9+20', or 'below S1'."""
    over = dbm - s9_dbm(freq_hz)
    if over > 0:
        return f'S9+{over:.0f}'
    s = 9 + int(round(over / DB_PER_S))
    return f'S{s}' if s >= 1 else 'below S1'


def calibration_for(known_dbm, dbfs, gain_db):
    """The calibration that makes this reading show the known level."""
    return known_dbm - (dbfs - gain_db)
