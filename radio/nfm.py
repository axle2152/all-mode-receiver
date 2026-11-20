"""
radio/nfm.py - narrowband FM: amateur repeaters and simplex, marine, NOAA weather, FRS/GMRS,
MURS, public safety
===============================================================================================
The receiver for the voice FM channels (12.5 to 25 kHz apart), with the controls a scanner or a
ham rig has: squelch, and CTCSS (PL) tones decoded, shown and used for tone squelch.

Signal chain (960 ksps from the RTL -> 48 kHz complex channel)
  source        the quadrature tuner (or direct sampling below 28.8 MHz), the LO LO_OFFSET above
                the station so nothing sits on the dongle's DC; this receiver's own AGC sets the
                tuner gain, so the S-meter always knows it
  channel       two decimating filters, then AFC (the dongle's crystal error, followed while a
                station is heard) and the IF filter: +/-8 kHz for 5 kHz deviation ('NFM', 25 kHz
                channels) or +/-5.5 kHz for 2.5 kHz ('NFM2', 12.5 kHz channels)
  demod         quadrature discriminator, in kHz of deviation
  audio         de-emphasis (750 us, 0 dB at 1 kHz: the 6 dB/octave pre-emphasis land-mobile
                and amateur transmitters use), then 300 Hz to 3 kHz, steep at the bottom so
                the CTCSS tone (67 to 254 Hz) stays out of the speaker, then the squelch gate,
                volume and the same bass / treble as the other receivers
  squelch       noise squelch, as in a scanner: the discriminator's noise just above the voice
                band, against what it gives with no signal at all (worked out at start for the
                filters in use). That "quieting" follows the carrier-to-noise ratio in 12.5 kHz
                almost dB for dB from 3 to 40 dB, whatever the gain, so the squelch levels are
                set in dB of C/N, and the same figure is the signal quality the window shows.
                The gate opens and closes in a few ms without a click; it closes before the
                noise burst at the end of a transmission reaches the speaker (the voice filter
                holds the audio back about 47 ms)
  CTCSS         the discriminator below 300 Hz at 1 kHz, half a second at a time (stepping
                every 128 ms): the strongest line from 60 to 260 Hz, its frequency to a fraction
                of a hertz, standing clear of its neighbourhood, within 0.8 Hz of a standard tone.
                Tone squelch opens only while that tone is heard

Squelch levels (set_squelch): 0 = open, 1..9 open at SQUELCH_DB C/N (in 12.5 kHz).
"""

import math
import threading
import time

import numpy as np
from gnuradio import gr, blocks, filter, analog

from . import meters as rx_meters
from .receiver import Receiver

# The EIA / TIA-603 CTCSS tones, Hz (the 50 in common use)
CTCSS_TONES = (67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0,
               103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4,
               156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9,
               192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3,
               254.1)

# R82xx tuner gain steps (dB), when the dongle doesn't list its own
R82XX_GAINS = [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9,
               25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5,
               48.0, 49.6]


def _kaiser_lp(fc, fs, n, beta=7.0):
    """Windowed-sinc low-pass, unity gain at DC."""
    m = (n - 1) / 2
    h = 2 * fc / fs * np.sinc(2 * fc / fs * (np.arange(n) - m)) * np.kaiser(n, beta)
    return h / np.sum(h)


def _kaiser_bp(f1, f2, fs, n, beta=7.0):
    """Windowed-sinc band-pass, unity gain in the passband's middle."""
    k = np.arange(n) - (n - 1) / 2
    h = (2 * f2 / fs * np.sinc(2 * f2 / fs * k) - 2 * f1 / fs * np.sinc(2 * f1 / fs * k)) * np.kaiser(n, beta)
    fm = math.sqrt(f1 * f2)
    g = abs(np.sum(h * np.exp(-2j * np.pi * fm * np.arange(n) / fs)))
    return h / g


def _floats(h):
    return [float(v) for v in h]


class NFMReceiver(Receiver):
    BAND = 'NFM'
    FEATURES = frozenset({'squelch', 'ctcss', 'nfm_width'})

    # ---- source ----
    SAMP_RATE        = 960_000      # 48 kHz x 20, as AM: the RTL drops samples at 2.4 Msps
    DECIM1, DECIM2   = 10, 2        # 960 k -> 96 k -> 48 k
    CH_RATE          = 48_000
    LO_OFFSET        = 100e3        # the LO this far above the station: off the dongle's DC
    AFC_MAX_HZ       = 5000.0       # the crystal error AFC will follow (12 ppm at 430 MHz)
    AFC_MIN_DB       = 12.0         # ... only while a station this clean is heard

    # ---- widths: mode -> (peak deviation, IF filter half-width, squelch noise band) ----
    WIDTHS = {'NFM':  (5000.0, 8000.0, (5000.0, 7500.0)),
              'NFM2': (2500.0, 5500.0, (4000.0, 5500.0))}
    IF_TAPS          = 129
    NOISE_TAPS       = 257

    # ---- audio ----
    DEEMPH_US        = 750.0
    AUDIO_LO_HZ      = 300.0        # -6 dB here; the CTCSS tones are 70 dB down
    AUDIO_HI_HZ      = 3000.0
    VOICE_TAPS       = 4501         # 47 ms at 48 kHz: steep enough below 300 Hz
    NFM_AUDIO_GAIN   = 0.5          # full deviation at 1 kHz -> half the sound card's range
    GATE_S           = 0.003        # the squelch gate's ramp

    # ---- squelch ----
    SQUELCH_DB       = (None, 4.0, 6.0, 8.0, 10.0, 13.0, 16.0, 19.0, 22.0, 25.0)
    SQUELCH_HYST_DB  = 3.0          # closes this far below where it opened
    SQ_WIN           = 480          # 10 ms noise windows
    TUNE_SETTLE_S    = 0.15

    # ---- CTCSS ----
    CT_RATE          = 1000
    CT_HOP           = 128          # a new look every 128 ms...
    CT_WIN           = 512          # ...at the last 0.512 s
    CT_TOL_HZ        = 0.8          # within this of a standard tone
    CT_PROM_DB       = 10.0         # the line stands this far over the 60-260 Hz median
    CT_MIN_KHZ       = 0.12         # and deviates at least this much (tones are 0.35-0.75 kHz)
    CT_LOSE          = 2            # looks without the tone before it counts as gone

    MPX_FULL         = 1.0          # the audio snapshot: 1.0 = full deviation at 1 kHz

    # ---- AGC (tuner gain; not with direct sampling) ----
    AGC_HI_DBFS      = -10.0        # wideband level: above this (or clipping), back off
    AGC_LO_DBFS      = -24.0        # below this, clean for AGC_UP_WAIT_S, more gain
    AGC_CLIP_PCT     = 0.01
    AGC_UP_WAIT_S    = 2.0
    AGC_STEP_DB      = 3.0
    AGC_MAX_DB       = 40.2
    AGC_SETTLE_S     = 0.25

    def __init__(self, freq=146.52e6, rf_gain=29.7, agc=True, audio_device="",
                 device_args="numchan=1 rtl=0", width='NFM', **_):
        Receiver.__init__(self, "NFM Receiver")
        self._lock = threading.RLock()
        self.freq = freq
        self.samp_rate, self.ch_rate = self.SAMP_RATE, self.CH_RATE
        self.audio_rate = self.mpx_rate = self.CH_RATE
        self.direct = 'direct_samp' in device_args
        self.agc_enabled = bool(agc) and not self.direct
        c = self.connect

        # ---------------- source ----------------
        self.src = self._make_source(device_args)
        self.src.set_sample_rate(self.samp_rate)
        self.src.set_center_freq(freq + self.LO_OFFSET, 0)
        self.src.set_freq_corr(0, 0)
        self.src.set_dc_offset_mode(0, 0)
        self.src.set_iq_balance_mode(0, 0)
        self.src.set_gain_mode(False, 0)                # this receiver's own AGC, or manual
        try:
            vals = list(self.src.get_gain_range().values())
        except Exception:
            vals = []
        self.gains = sorted(set(round(v, 1) for v in vals)) if len(vals) >= 5 else list(R82XX_GAINS)
        self._gain_idx = int(np.argmin([abs(g - float(rf_gain)) for g in self.gains]))
        self.gain_db = 0.0 if self.direct else self.gains[self._gain_idx]
        if not self.direct:
            self.src.set_gain(self.gain_db, 0)

        # ---------------- channel ----------------
        r1 = self.samp_rate / self.DECIM1
        self.xlate = filter.freq_xlating_fir_filter_ccf(
            self.DECIM1, _floats(_kaiser_lp(22e3, self.samp_rate, 121)), -self.LO_OFFSET, self.samp_rate)
        self.dec2 = filter.fir_filter_ccf(self.DECIM2, _floats(_kaiser_lp(16e3, r1, 63)))
        self.rot = blocks.rotator_cc(0.0)               # AFC
        self.if_filter = filter.fir_filter_ccf(1, [1.0])
        self.demod = analog.quadrature_demod_cf(self.ch_rate / (2 * math.pi * 1000.0))   # kHz
        c(self.src, self.xlate, self.dec2, self.rot, self.if_filter, self.demod)

        # ---------------- audio ----------------
        p = math.exp(-1.0 / (self.DEEMPH_US * 1e-6 * self.ch_rate))
        w1 = 2 * math.pi * 1000.0 / self.ch_rate
        g = abs(1 - p * complex(math.cos(w1), -math.sin(w1))) / (1 - p)   # 0 dB at 1 kHz
        self.norm = blocks.multiply_const_ff(1.0)        # kHz -> 1.0 = full deviation
        self.deemph = filter.iir_filter_ffd([g * (1 - p)], [1.0, -p], False)
        self.voice = filter.fft_filter_fff(1, _floats(_kaiser_bp(self.AUDIO_LO_HZ, self.AUDIO_HI_HZ, self.ch_rate,
                                                                  self.VOICE_TAPS)))
        self.gate_src = analog.sig_source_f(self.ch_rate, analog.GR_CONST_WAVE, 0, 0.0, 0.0)
        self.gate_ramp = filter.single_pole_iir_filter_ff(1 - math.exp(-1.0 / (self.GATE_S * self.ch_rate)))
        self.gate = blocks.multiply_ff(1)
        c(self.demod, self.norm, self.deemph, self.voice, (self.gate, 0))
        c(self.gate_src, self.gate_ramp, (self.gate, 1))
        self._out_gain, self._muted = (1.0, 1.0), False
        hr = 10 ** (-self._tone_atten_db(0, 0) / 20)
        self.audio_sink = self._make_audio_sink(self.audio_rate, audio_device)
        for idx, ch in enumerate(("l", "r")):
            vol = blocks.multiply_const_ff(0.0)
            setattr(self, f"vol_{ch}", vol)
            self._make_tone(ch, hr)
            c(self.gate, vol)
            self._connect_tone(vol, ch, idx)

        # ---------------- squelch: noise just above the voice ----------------
        self.sq_bp = filter.fir_filter_fff(1, [1.0])
        self.sq_sq = blocks.multiply_ff(1)
        self.sq_int = blocks.integrate_ff(self.SQ_WIN)
        self.sq_probe = blocks.probe_signal_f()
        c(self.demod, self.sq_bp)
        c(self.sq_bp, (self.sq_sq, 0))
        c(self.sq_bp, (self.sq_sq, 1))
        c(self.sq_sq, self.sq_int, self.sq_probe)

        # ---------------- CTCSS: below 300 Hz at 1 kHz ----------------
        ct_d = self.ch_rate // self.CT_RATE
        self.ct_dec = filter.fir_filter_fff(ct_d, _floats(_kaiser_lp(280.0, self.ch_rate, 1023)))
        self.ct_vec = blocks.stream_to_vector(gr.sizeof_float, self.CT_HOP)
        self.ct_probe = blocks.probe_signal_vf(self.CT_HOP)
        c(self.demod, self.ct_dec, self.ct_vec, self.ct_probe)

        # ---------------- measurements ----------------
        self._win = self.ch_rate // 50                   # 20 ms
        self.ch_mag = blocks.complex_to_mag_squared(1)   # channel power, after the IF filter
        self.ch_int = blocks.integrate_ff(self._win)
        self.ch_probe = blocks.probe_signal_f()
        c(self.if_filter, self.ch_mag, self.ch_int, self.ch_probe)
        self.dc_int = blocks.integrate_ff(self._win)     # mean deviation: the carrier's offset
        self.dc_probe = blocks.probe_signal_f()
        c(self.demod, self.dc_int, self.dc_probe)
        self.CH_N = 4096
        self.snap_ch_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, self.CH_N)
        self.snap_ch = blocks.probe_signal_vc(self.CH_N)
        c(self.rot, self.snap_ch_vec, self.snap_ch)
        self.snap_if_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, 2048)
        self.snap_if = blocks.probe_signal_vc(2048)
        c(self.if_filter, self.snap_if_vec, self.snap_if)
        self.snap_aud_vec = blocks.stream_to_vector(gr.sizeof_float, 4096)
        self.snap_aud = blocks.probe_signal_vf(4096)
        c(self.voice, self.snap_aud_vec, self.snap_aud)
        self.WB_N = 4096
        self.snap_wb_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, self.WB_N)
        self.snap_wb_keep = blocks.keep_one_in_n(gr.sizeof_gr_complex * self.WB_N,
                                                 max(1, int(self.samp_rate / self.WB_N / 4)))
        self.snap_wb = blocks.probe_signal_vc(self.WB_N)
        c(self.src, self.snap_wb_vec, self.snap_wb_keep, self.snap_wb)

        # ---------------- monitoring taps (read only, see radio/meters.py) ----------------
        # Audio as heard (after the squelch gate, before volume; 0 dB = full deviation at 1 kHz)
        # and the deviation in kHz.
        self.audio_meter = self.mod_meter = None
        if rx_meters is not None:
            kb1, ka1, kb2, ka2 = rx_meters.k_weighting(self.audio_rate)
            self.audio_meter = rx_meters.AudioMeterBlock(self.audio_rate, ref=1.0)
            for idx, ch in enumerate(("l", "r")):
                k1 = filter.iir_filter_ffd(kb1, ka1, False)
                k2 = filter.iir_filter_ffd(kb2, ka2, False)
                setattr(self, f"kw1_{ch}", k1)
                setattr(self, f"kw2_{ch}", k2)
                c(self.gate, (self.audio_meter, idx))
                c(self.gate, k1, k2, (self.audio_meter, idx + 2))
            self.mod_meter = rx_meters.ModMeterBlock(scale=1.0, over_pos=5.0, over_neg=5.0)
            c(self.demod, self.mod_meter)

        # ---------------- state ----------------
        self.width = None
        self.squelch_level = 0                           # open until set
        self.squelch_open = False                        # a station is heard (noise squelch)
        self.tone_squelch = None                         # Hz, or None: no tone squelch
        self.tone_ok = False                             # the tone squelch's tone is heard
        self.ctcss_hz = None                             # the standard tone heard, or None
        self.ctcss_khz = 0.0                             # its deviation
        self.ctcss_line_hz = None                        # the strongest line, as measured
        self.gate_open = False
        self.quieting_db = 0.0
        self.cnr_db = self.raw_cnr_db = self.pre_cnr_db = 0.0
        self.spec_snr_db = None
        self.channel_dbfs, self.composite_dbfs, self.clip_pct = -120.0, -120.0, 0.0
        self.carrier_hz = 0.0                            # offset AFC has taken out, plus what's left
        self.afc_hz = 0.0
        self.if_khz = 0.0
        self.bw = 0.0
        self.adj_db, self.occupied_khz = -60.0, None
        self.current_blend, self.current_treble = 0.0, 1.0
        self.locked = False
        # What the window asks every receiver (stereo and station data: none on narrow FM)
        self.pilot_level, self.pilot_ratio_db = 0.0, -99.0
        self.pilot_on, self.stereo_indicator = False, False
        self.rds_ps, self.rds_rt, self.rds_pi, self.rds_call, self.rds_pty = '', '', None, None, None
        self.rds_synced = False
        self.eq_mode, self.eq_active, self.eq_cnr_db, self.eq_gain_db = 'off', False, 0.0, 0.0
        self.eq_gate, self.mp_excess_db = False, 0.0
        self._noise_ref = {}
        self._last_sq = self._last_ch = self._last_dc = None
        self._last_wb = self._last_ct = None
        self._ct_buf = np.zeros(0, np.float32)
        self._ct_miss = 0
        self._q_slow = None
        self._tune_cnr = []
        self._blank_until = time.monotonic() + self.TUNE_SETTLE_S
        self._agc_t = self._agc_clean_t = time.monotonic()
        self._wbw = np.hanning(self.WB_N)
        self._ct_w = np.hanning(self.CT_WIN)
        self._ct_f = np.fft.rfftfreq(16384, 1.0 / self.CT_RATE)
        self._ct_band = (self._ct_f > 60.0) & (self._ct_f < 260.0)
        self.set_width(width)
        self._apply_gain()

    @property
    def SEEK_MIN_DB(self):
        """Search stops, and TUNED lights, on a station that would open the squelch (at level 3
        while it's off)."""
        return self.SQUELCH_DB[self.squelch_level or 3]

    # ------------------------------------------------------------------ helpers
    def _noise_reference(self, width):
        """What the squelch's noise band reads with no signal at all, for these filters: white
        noise through the IF filter, the discriminator and the noise band (seeded, so the same
        every time). A discriminator fed noise alone gives the same output whatever the level."""
        if width not in self._noise_ref:
            _, hw, band = self.WIDTHS[width]
            rng = np.random.default_rng(7)
            n = self.ch_rate
            x = rng.standard_normal(n + 400) + 1j * rng.standard_normal(n + 400)
            x = np.convolve(x, _kaiser_lp(hw, self.ch_rate, self.IF_TAPS), 'valid')
            d = np.angle(x[1:] * np.conj(x[:-1])) * self.ch_rate / (2 * math.pi * 1000.0)
            y = np.convolve(d, _kaiser_bp(band[0], band[1], self.ch_rate, self.NOISE_TAPS), 'valid')
            self._noise_ref[width] = float(np.mean(y * y))
        return self._noise_ref[width]

    def _apply_gain(self):
        gl, gr_ = (0.0, 0.0) if self._muted else self._out_gain
        k = self.NFM_AUDIO_GAIN
        self.vol_l.set_k(k * gl)
        self.vol_r.set_k(k * gr_)

    def _set_gate(self, on):
        if on != self.gate_open:
            self.gate_open = on
            self.gate_src.set_amplitude(1.0 if on else 0.0)

    # --------------------------------------------------------------- controls
    def set_width(self, mode):
        """'NFM': 5 kHz deviation, 25 kHz channels; 'NFM2': 2.5 kHz, 12.5 kHz channels."""
        if mode not in self.WIDTHS or mode == self.width:
            return
        with self._lock:
            self.width = mode
            dev, hw, band = self.WIDTHS[mode]
            self.if_filter.set_taps(_floats(_kaiser_lp(hw, self.ch_rate, self.IF_TAPS)))
            self.sq_bp.set_taps(_floats(_kaiser_bp(band[0], band[1], self.ch_rate, self.NOISE_TAPS)))
            self.norm.set_k(1000.0 / dev)
            self._noise_reference(mode)
            self.dev_khz = dev / 1e3
            self.if_khz = hw / 1e3
            self.bw = hw
            if self.mod_meter is not None:
                self.mod_meter.core.set_limits(dev / 1e3, dev / 1e3)
            self._q_slow = None

    def set_squelch(self, level):
        """0: open; 1..9: open on a station this clean (SQUELCH_DB, C/N in 12.5 kHz)."""
        self.squelch_level = int(min(max(level, 0), len(self.SQUELCH_DB) - 1))

    def set_tone_squelch(self, hz):
        """Play only while this CTCSS tone (Hz) is heard; None: any station."""
        self.tone_squelch = None if hz is None else float(hz)
        self.tone_ok = self.tone_squelch is not None and self.ctcss_hz == self.tone_squelch

    def set_freq(self, freq):
        with self._lock:
            old = self.freq
            self.freq = freq
            self.src.set_center_freq(freq + self.LO_OFFSET, 0)
            if old:                                      # the crystal's error scales with frequency
                self.afc_hz = min(max(self.afc_hz * freq / old, -self.AFC_MAX_HZ), self.AFC_MAX_HZ)
                self.rot.set_phase_inc(-2 * math.pi * self.afc_hz / self.ch_rate)
            self._reset_station()
            self._blank_until = time.monotonic() + self.TUNE_SETTLE_S

    def _reset_station(self):
        self.squelch_open = self.tone_ok = False
        self.ctcss_hz, self.ctcss_line_hz, self.ctcss_khz = None, None, 0.0
        self._ct_buf = np.zeros(0, np.float32)
        self._ct_miss = 0
        self._q_slow = None
        self._tune_cnr = []
        self._set_gate(self.squelch_level == 0 and self.tone_squelch is None)

    def set_output_gain(self, left, right):
        with self._lock:
            self._out_gain = (max(0.0, float(left)), max(0.0, float(right)))
            self._apply_gain()

    def set_mute(self, muted):
        with self._lock:
            self._muted = bool(muted)
            self._apply_gain()

    def set_force_mono(self, on):
        """Narrow FM is mono already."""

    def set_manual_gain(self, db):
        with self._lock:
            self.agc_enabled = False
            if not self.direct:
                self._gain_idx = int(np.argmin([abs(g - float(db)) for g in self.gains]))
                self.gain_db = self.gains[self._gain_idx]
                self.src.set_gain(self.gain_db, 0)

    def set_agc(self, on):
        with self._lock:
            self.agc_enabled = bool(on) and not self.direct
            self._agc_clean_t = time.monotonic()

    def cnr_since_tune(self):
        with self._lock:
            return list(self._tune_cnr)

    def audio_lag(self):
        """(meters, front end) seconds the sound card trails the measurements, or None."""
        if not self._counting():
            return None
        try:
            played = self.audio_sink.nitems_read(0)
            tap = self.gate.nitems_written(0)
            front = self.xlate.nitems_written(0) * self.audio_rate / (self.samp_rate / self.DECIM1)
        except Exception:
            return None
        r = float(self.audio_rate)
        return max(0.0, tap - played) / r, max(0.0, front - played) / r

    def monitor(self):
        """Meter readings since the last call (deviation in kHz), or None."""
        if self.audio_meter is None:
            return None
        return dict(audio=self.audio_meter.read(), mod=self.mod_meter.read())

    def signal_level(self):
        """(channel dBFS, tuner gain dB): this receiver's own AGC sets the gain, so it's known
        (no tuner with direct sampling)."""
        return self.channel_dbfs, (0.0 if self.direct else self.gain_db)

    def snapshot(self, kind):
        if kind == 'iq':
            return np.asarray(self.snap_ch.level(), dtype=np.complex64)
        if kind in ('pre', 'post'):
            return np.asarray(self.snap_if.level(), dtype=np.complex64)
        if kind == 'mpx':
            return np.asarray(self.snap_aud.level(), dtype=np.float32)
        return np.zeros(0, np.complex64)

    def readout(self):
        """ADV text for narrow FM."""
        sq = 'open' if self.squelch_level == 0 else f'level {self.squelch_level} ({self.SQUELCH_DB[self.squelch_level]:g} dB)'
        heard = 'station heard' if self.squelch_open else 'no station'
        tone = f'{self.ctcss_hz:5.1f} Hz ({self.ctcss_khz:.2f} kHz)' if self.ctcss_hz else 'none'
        tsq = f'   tone squelch {self.tone_squelch:5.1f} Hz' if self.tone_squelch else ''
        src = 'direct sampling' if self.direct else f'tuner, gain {self.gain_db:4.1f} dB ' + \
            ('(AGC)' if self.agc_enabled else '(manual)')
        return (f"C/N      {self.cnr_db:5.1f} dB in 12.5 kHz (quieting)   channel {self.channel_dbfs:6.1f} dBFS\n"
                f"IF       +/-{self.if_khz:3.1f} kHz for {self.dev_khz:3.1f} kHz deviation"
                f"   carrier offset {self.carrier_hz:+7.1f} Hz (AFC {self.afc_hz:+7.1f})\n"
                f"Squelch  {sq}, {heard}{'' if self.gate_open else ', muted'}   CTCSS {tone}{tsq}\n"
                f"Clip     {self.clip_pct:6.3f} %   source: {src}")

    def addon_tap(self):
        return None

    # ---------------------------------------------------------------- update
    def _dongle_block(self):
        return self.xlate

    def update_blend(self):
        """Called every ~20 ms by the window: measurements, AGC, AFC, squelch, CTCSS."""
        now = time.monotonic()
        with self._lock:
            wb = self.snap_wb.level()
            fresh = False
            if len(wb) == self.WB_N and np.any(np.asarray(wb)):
                if self._last_wb is None or wb[0] != self._last_wb:
                    self._last_wb = wb[0]
                    fresh = True
                    x = np.asarray(wb)
                    self.composite_dbfs = 10 * math.log10(max(float(np.mean(np.abs(x) ** 2)), 1e-12))
                    self.clip_pct = 100.0 * float(np.mean((np.abs(x.real) > 0.98) | (np.abs(x.imag) > 0.98)))
                    self._agc(now)
            self._watchdog(now, fresh)
            try:
                self._measure(now)
            except Exception as e:                       # never a reason to stop
                if not getattr(self, '_said', False):
                    self._said = True
                    print(f"NFM measurement: {type(e).__name__}: {e}")

    def _agc(self, now):
        """Tuner gain: back off on clipping or a loud band, step up after a clean spell."""
        if not self.agc_enabled or now - self._agc_t < self.AGC_SETTLE_S:
            return
        loud = self.clip_pct > self.AGC_CLIP_PCT or self.composite_dbfs > self.AGC_HI_DBFS
        quiet = self.composite_dbfs < self.AGC_LO_DBFS
        if not quiet or loud:
            self._agc_clean_t = now
        idx = self._gain_idx
        if loud:
            while idx > 0 and self.gains[self._gain_idx] - self.gains[idx] < self.AGC_STEP_DB:
                idx -= 1
        elif quiet and now - self._agc_clean_t > self.AGC_UP_WAIT_S:
            while idx + 1 < len(self.gains) and self.gains[idx + 1] <= self.AGC_MAX_DB and \
                    self.gains[idx] - self.gains[self._gain_idx] < self.AGC_STEP_DB:
                idx += 1
            self._agc_clean_t = now
        if idx != self._gain_idx:
            self._gain_idx = idx
            self.gain_db = self.gains[idx]
            self.src.set_gain(self.gain_db, 0)
            self._agc_t = now

    def _measure(self, now):
        settled = now >= self._blank_until
        # channel power (20 ms)
        cp = self.ch_probe.level()
        if cp != self._last_ch:
            self._last_ch = cp
            self.channel_dbfs = 10 * math.log10(max(cp / self._win, 1e-15))

        # noise squelch: quieting = C/N in 12.5 kHz
        sq = self.sq_probe.level()
        if sq != self._last_sq:
            self._last_sq = sq
            ref = self._noise_reference(self.width)
            q = 10 * math.log10(ref / max(sq / self.SQ_WIN, 1e-12))
            q = min(max(q, 0.0), 60.0)
            self.quieting_db = q
            if settled:
                self._q_slow = q if self._q_slow is None else self._q_slow + 0.2 * (q - self._q_slow)
                self.cnr_db = self.raw_cnr_db = self.pre_cnr_db = self.spec_snr_db = self._q_slow
                self._tune_cnr.append(q)
                if len(self._tune_cnr) > 200:
                    del self._tune_cnr[:100]
            thr = self.SQUELCH_DB[self.squelch_level] if self.squelch_level else 0.0
            was = self.squelch_open
            if not settled:
                self.squelch_open = False
            elif self.squelch_open:
                self.squelch_open = q >= thr - self.SQUELCH_HYST_DB
            else:
                self.squelch_open = q >= thr
            if was and not self.squelch_open:            # the station went: start the tone afresh
                self.ctcss_hz, self.ctcss_line_hz, self.ctcss_khz = None, None, 0.0
                self.tone_ok = False
                self._ct_buf = np.zeros(0, np.float32)
            self.locked = self.squelch_open

        # AFC: the mean deviation, while a clean station is heard
        dc = self.dc_probe.level()
        if dc != self._last_dc:
            self._last_dc = dc
            off = dc / self._win * 1000.0                 # Hz
            self.carrier_hz = self.afc_hz + off
            if settled and self.squelch_open and self.quieting_db >= self.AFC_MIN_DB and abs(off) > 20.0:
                self.afc_hz = min(max(self.afc_hz + 0.3 * off, -self.AFC_MAX_HZ), self.AFC_MAX_HZ)
                self.rot.set_phase_inc(-2 * math.pi * self.afc_hz / self.ch_rate)

        # CTCSS
        ct = self.ct_probe.level()
        if len(ct) == self.CT_HOP and np.any(np.asarray(ct)):
            key = tuple(ct[:4])
            if key != self._last_ct:
                self._last_ct = key
                self._ct_buf = np.concatenate((self._ct_buf, np.asarray(ct, np.float32)))[-self.CT_WIN:]
                self._ctcss()

        heard = self.squelch_level == 0 or self.squelch_open
        self._set_gate(heard and (self.tone_squelch is None or self.tone_ok))

    def _ctcss(self):
        """The tone in the last half second, if it's a standard one standing clear."""
        x = self._ct_buf
        found = None
        if len(x) == self.CT_WIN:
            Y = np.abs(np.fft.rfft((x - np.mean(x)) * self._ct_w, 16384))
            band = np.where(self._ct_band, Y, 0.0)
            i = int(np.argmax(band))
            if 0 < i < len(Y) - 1 and band[i] > 0:
                a, b, c = np.log(Y[i - 1:i + 2] + 1e-12)
                den = a - 2 * b + c
                d = 0.5 * (a - c) / den if den != 0 else 0.0
                f = self._ct_f[i] + d * (self._ct_f[1] - self._ct_f[0])
                khz = 2 * Y[i] / np.sum(self._ct_w)          # the tone's deviation
                prom = 20 * math.log10(max(Y[i], 1e-12) / max(float(np.median(Y[self._ct_band])), 1e-12))
                std = min(CTCSS_TONES, key=lambda t: abs(t - f))
                self.ctcss_line_hz = f
                if abs(std - f) <= self.CT_TOL_HZ and prom >= self.CT_PROM_DB and khz >= self.CT_MIN_KHZ:
                    found = (std, khz)
        if found is not None:
            self._ct_miss = 0
            self.ctcss_hz, self.ctcss_khz = found
        else:
            self._ct_miss += 1
            if self._ct_miss >= self.CT_LOSE:
                self.ctcss_hz, self.ctcss_khz = None, 0.0
        self.tone_ok = self.tone_squelch is not None and self.ctcss_hz == self.tone_squelch
