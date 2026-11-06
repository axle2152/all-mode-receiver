#!/usr/bin/env python3
"""
am_receiver.py - AM broadcast band (530-1710 kHz) with C-QUAM AM stereo
======================================================================
The AM half of the head unit. It presents the same interface as
FMStereoReceiver, so the GUI can drive either one.

Signal chain (2.4 Msps from the RTL -> 48 kHz complex channel)
  source        Q-branch direct sampling (RTL-SDR Blog V3 and similar),
                native HF tuning (RTL-SDR Blog V4) or an external upconverter
  channel       two-stage decimating filter, station 20 kHz below the LO so
                nothing sits on DC
  carrier       coarse frequency correction from the channel spectrum, then a
                slow PLL (about 4 Hz, well below the 25 Hz stereo pilot)
  IF filter     adaptive bandwidth: wide (10 kHz audio) on a clean station,
                narrower as the carrier-to-noise drops or when the stations
                10 kHz either side get strong
  L+R           envelope detection (what every AM radio does, so it is
                exactly mono-compatible with C-QUAM)
  L-R           C-QUAM: the transmitter sends envelope 1+L+R and phase
                atan((L-R)/(1+L+R)), so L-R = envelope x tan(phase)
  stereo        only while the 25 Hz pilot in L-R is present, blended in
                with the carrier-to-noise
  audio         low-pass at the IF bandwidth, then the same bass / treble,
                make-up gain, volume / balance and mute as the FM side

Quality figure: carrier-to-noise in 10 kHz (C/N), measured every 20 ms from the
coherent carrier against the noise floor of the whole 2.4 MHz.
"""

import math
import threading
import time
from collections import deque

import numpy as np
from gnuradio import gr, blocks, filter, analog, audio
from gnuradio.filter import firdes
import osmosdr
try:                                     # optional read-only meters for the monitor UI
    import rx_meters
    if rx_meters.AudioMeterBlock is None:
        rx_meters = None
except ImportError:
    rx_meters = None

W = filter.window.WIN_HAMMING


class AMReceiver(gr.top_block):
    # ---- source ----
    AM_SOURCE        = 'direct_q'   # 'direct_q' | 'native' | 'upconverter'
    UPCONVERTER_HZ   = 125e6        # Ham It Up and similar
    LO_OFFSET        = 20e3         # tune this far above the station (keeps it off DC)
    SAMP_RATE        = 2_400_000
    CH_RATE          = 48_000

    # ---- bandwidth (one-sided audio bandwidth, Hz) ----
    AM_WIDE_HZ       = 10200.0      # NRSC full bandwidth at 10.2 kHz
    AM_NARROW_HZ     = 4000.0       # the manual "narrow" setting
    AM_XWIDE_HZ      = 15000.0      # set_wide(True): everything an NRSC-1 station sends
    NOTCH_HZ, NOTCH_R = 10000.0, 0.99   # ... with the 10 kHz whistle of adjacent carriers notched out
    AM_MIN_HZ        = 2500.0
    # carrier-to-noise (dB in 10 kHz) -> bandwidth, interpolated
    BW_TABLE         = [(15, 2500), (18, 3500), (21, 4500), (24, 6000), (27, 8000), (30, 10200)]
    # a carrier 10 kHz away this strong (dB re ours) limits the bandwidth to...
    ADJ_TABLE        = [(-35, 7000), (-25, 5000), (-15, 4000), (-8, 3000)]
    BW_SLEW_IN       = 2000.0       # Hz per second, narrowing
    BW_SLEW_OUT      = 4000.0       # Hz per second, widening
    BW_STEP          = 200.0        # redesign the filters when it has moved this far

    # ---- stereo ----
    PILOT_HZ         = 25.0
    KAHN_HILBERT_N   = 1025         # Kahn ISB: the Hilbert transformer for L-R (21 ms at 48 kHz)
    PILOT_MIN        = 0.015        # pilot amplitude (fraction of carrier) to count
    PILOT_RATIO_DB   = 10.0         # pilot above its neighbourhood to count
    STEREO_MONO_DB   = 15.0         # C/N at or below: mono even with a pilot
    STEREO_FULL_DB   = 20.0         # C/N at or above: full stereo

    # ---- level / UI ----
    AM_AUDIO_GAIN    = 1.4          # 100% modulation -> about the FM side's level
    SEEK_MIN_DB      = 22.0         # seek stops on a carrier this far above the noise
    TUNE_SETTLE_S    = 0.25
    STALL_S          = 2.0
    TONE_BASS_HZ, TONE_TREBLE_HZ, TONE_RANGE_DB = 120.0, 4000.0, 12.0
    TONE_SAFETY_DB   = 1.0
    IF_TAPS          = 97           # IF and audio filters: fixed length, fixed delay
    MPX_FULL         = 1.0          # ADV audio spectrum: 1.0 = 100% modulation

    def __init__(self, freq=1080e3, rf_gain=0.0, agc=False, audio_device="",
                 device_args="numchan=1 rtl=0", am_source=None, upconverter_hz=None, **_):
        gr.top_block.__init__(self, "AM Receiver")
        self._lock = threading.RLock()
        self.am_source = am_source or self.AM_SOURCE
        if upconverter_hz:
            self.UPCONVERTER_HZ = float(upconverter_hz)
        self.freq = freq
        self.samp_rate, self.ch_rate = self.SAMP_RATE, self.CH_RATE
        self.audio_rate = self.CH_RATE
        self.mpx_rate = self.CH_RATE
        self.gain_db, self.agc_enabled = float(rf_gain), bool(agc)   # kept for the GUI
        c = self.connect

        # ---------------- source ----------------
        args = device_args
        if self.am_source == 'direct_q':
            args = f"{device_args},direct_samp=2"
        self.src = osmosdr.source(args=args)
        self.src.set_sample_rate(self.samp_rate)
        self.src.set_center_freq(self._lo(freq), 0)
        self.src.set_freq_corr(0, 0)
        self.src.set_dc_offset_mode(0, 0)
        self.src.set_iq_balance_mode(0, 0)
        if self.am_source == 'direct_q':
            self.src.set_gain_mode(False, 0)          # no tuner in the path
        elif agc:
            self.src.set_gain_mode(True, 0)
        else:
            self.src.set_gain_mode(False, 0)
            self.src.set_gain(self.gain_db, 0)

        # ---------------- channel ----------------
        t1 = firdes.low_pass(1.0, self.samp_rate, 30e3, 70e3, W)
        self.xlate = filter.freq_xlating_fir_filter_ccf(10, t1, -self.LO_OFFSET, self.samp_rate)
        t2 = firdes.low_pass(1.0, 240e3, 14e3, 8e3, W)
        self.dec2 = filter.fir_filter_ccf(5, t2)
        self.rot = blocks.rotator_cc(0.0)               # coarse carrier correction
        self.bw = self.AM_WIDE_HZ
        self.if_filter = filter.fir_filter_ccf(1, self._lp_taps(self.bw))
        self.pll = analog.pll_carriertracking_cc(2 * math.pi * 4.0 / self.ch_rate,
                                                  2 * math.pi * 60.0 / self.ch_rate,
                                                  -2 * math.pi * 60.0 / self.ch_rate)
        c(self.src, self.xlate, self.dec2, self.rot, self.if_filter, self.pll)

        # ---------------- demod ----------------
        self.mag = blocks.complex_to_mag(1)
        self.arg = blocks.complex_to_arg(1)
        self.car = filter.single_pole_iir_filter_ff(1 - math.exp(-1 / (0.25 * self.ch_rate)))
        self.norm = blocks.divide_ff(1)                 # envelope / carrier = 1 + L + R
        self.car_floor = blocks.add_const_ff(1e-9)      # never 0/0: one NaN would poison every filter after it
        self.sum0 = blocks.add_const_ff(-1.0)           # L + R
        c(self.pll, self.mag)
        c(self.pll, self.arg)
        c(self.mag, self.car)
        c(self.mag, (self.norm, 0))
        c(self.car, self.car_floor, (self.norm, 1))
        c(self.norm, self.sum0)
        # L+R gets exactly the same high-pass as L-R (two first-order at 60 Hz),
        # so the two paths stay in phase and the stereo separation holds
        self.sum_lp = filter.single_pole_iir_filter_ff(self._a(60.0))
        self.sum_hp1 = blocks.sub_ff(1)
        self.sum_lp2 = filter.single_pole_iir_filter_ff(self._a(60.0))
        self.sum_hp = blocks.sub_ff(1)
        c(self.sum0, (self.sum_hp1, 0))
        c(self.sum0, self.sum_lp, (self.sum_hp1, 1))
        c(self.sum_hp1, (self.sum_hp, 0))
        c(self.sum_hp1, self.sum_lp2, (self.sum_hp, 1))
        # C-QUAM: L - R = envelope x tan(phase)
        self.rail = analog.rail_ff(-1.2, 1.2)
        self.tan = blocks.transcendental('tan', 'float')
        self.diff0 = blocks.multiply_ff(1)
        c(self.arg, self.rail, self.tan, (self.diff0, 0))
        c(self.norm, (self.diff0, 1))
        # pilot: L-R decimated to 1 kHz, a second at a time
        tp = firdes.low_pass(1.0, self.ch_rate, 60.0, 100.0, W)
        k = np.arange(len(tp))                          # its response at 25 Hz, divided out of the reading
        self._pil_gain = float(abs(np.sum(np.asarray(tp) * np.exp(-2j * np.pi * 25.0 * k / self.ch_rate))))
        self._pil_gain15 = float(abs(np.sum(np.asarray(tp) * np.exp(-2j * np.pi * 15.0 * k / self.ch_rate))))
        self.pil_dec = filter.fir_filter_fff(48, tp)
        self.pil_vec = blocks.stream_to_vector(gr.sizeof_float, 1024)
        self.pil_probe = blocks.probe_signal_vf(1024)
        c(self.diff0, self.pil_dec, self.pil_vec, self.pil_probe)
        # L-R audio: two first-order high-passes at 60 Hz take the pilot out
        # Kahn/Hazeltine ISB (left on the lower sideband, right on the upper):
        # L+R is the envelope, as for C-QUAM; L-R is the quadrature component,
        # through Kahn's inverse modulator (the envelope half demodulated: x2 at
        # -100%, -3.5 dB at +100%), then a Hilbert transformer for the 90 degrees
        # his +-45 degree networks put between L+R and L-R.
        self.k_sin = blocks.transcendental('sin', 'float')
        self.k_q = blocks.multiply_ff(1)                # Q / carrier = envelope x sin(phase)
        self.k_half = blocks.multiply_const_ff(0.5)
        self.k_den = blocks.add_const_ff(0.5)           # 1 + (L+R)/2, never below 0.5
        self.k_inv = blocks.divide_ff(1)
        c(self.arg, self.k_sin, (self.k_q, 0))
        c(self.norm, (self.k_q, 1))
        c(self.norm, self.k_half, self.k_den, (self.k_inv, 1))
        c(self.k_q, (self.k_inv, 0))
        self.k_lp1 = filter.single_pole_iir_filter_ff(self._a(60.0))   # the same high-pass as L+R
        self.k_hp1 = blocks.sub_ff(1)
        self.k_lp2 = filter.single_pole_iir_filter_ff(self._a(60.0))
        self.k_hp2 = blocks.sub_ff(1)
        c(self.k_inv, (self.k_hp1, 0))
        c(self.k_inv, self.k_lp1, (self.k_hp1, 1))
        c(self.k_hp1, (self.k_hp2, 0))
        c(self.k_hp1, self.k_lp2, (self.k_hp2, 1))
        self.k_hilb = filter.fft_filter_fff(1, firdes.hilbert(self.KAHN_HILBERT_N, filter.window.WIN_BLACKMAN_HARRIS))
        c(self.k_hp2, self.k_hilb)
        dly = (self.KAHN_HILBERT_N - 1) // 2             # L+R and C-QUAM L-R wait for the Hilbert transformer
        self.sum_dly = blocks.delay(gr.sizeof_float, dly)
        self.cq_dly = blocks.delay(gr.sizeof_float, dly)
        self.cq_k = blocks.multiply_const_ff(1.0)       # which decoder feeds the matrix
        self.kahn_k = blocks.multiply_const_ff(0.0)
        self.dec_sum = blocks.add_ff(1)
        self.d_lp1 = filter.single_pole_iir_filter_ff(self._a(60.0))
        self.d_hp1 = blocks.sub_ff(1)
        self.d_lp2 = filter.single_pole_iir_filter_ff(self._a(60.0))
        self.d_hp2 = blocks.sub_ff(1)
        c(self.diff0, (self.d_hp1, 0))
        c(self.diff0, self.d_lp1, (self.d_hp1, 1))
        c(self.d_hp1, (self.d_hp2, 0))
        c(self.d_hp1, self.d_lp2, (self.d_hp2, 1))
        self.blend_k = blocks.multiply_const_ff(0.0)
        c(self.d_hp2, self.cq_dly, self.cq_k, (self.dec_sum, 0))
        c(self.k_hilb, self.kahn_k, (self.dec_sum, 1))
        c(self.dec_sum, self.blend_k)
        # matrix
        self.mat_l, self.mat_r = blocks.add_ff(1), blocks.sub_ff(1)
        c(self.sum_hp, self.sum_dly)
        c(self.sum_dly, (self.mat_l, 0))
        c(self.blend_k, (self.mat_l, 1))
        c(self.sum_dly, (self.mat_r, 0))
        c(self.blend_k, (self.mat_r, 1))

        # ---------------- audio back end ----------------
        self._out_gain, self._muted = (1.0, 1.0), False
        ab = self._a(self.TONE_BASS_HZ)
        at = self._a(self.TONE_TREBLE_HZ)
        hr = 10 ** (-self._tone_atten_db(0, 0) / 20)
        self.audio_sink = audio.sink(self.audio_rate, audio_device, True)
        for idx, (ch, src) in enumerate((("l", self.mat_l), ("r", self.mat_r))):
            lp = filter.fir_filter_fff(1, self._lp_taps(self.bw))
            vol = blocks.multiply_const_ff(0.5 * self.AM_AUDIO_GAIN)
            nrsc = filter.iir_filter_ffd([1.0, 0.0], [1.0, 0.0], False)   # NRSC de-emphasis, off until set
            notch = filter.iir_filter_ffd([1.0, 0.0, 0.0], [1.0, 0.0, 0.0], False)   # 10 kHz notch, wide only
            setattr(self, f"alp_{ch}", lp)
            setattr(self, f"nrsc_{ch}", nrsc)
            setattr(self, f"notch_{ch}", notch)
            setattr(self, f"vol_{ch}", vol)
            setattr(self, f"tone_bass_lp_{ch}", filter.single_pole_iir_filter_ff(ab))
            setattr(self, f"tone_bass_k_{ch}", blocks.multiply_const_ff(0.0))
            setattr(self, f"tone_tre_lp_{ch}", filter.single_pole_iir_filter_ff(at))
            setattr(self, f"tone_tre_hp_{ch}", blocks.sub_ff(1))
            setattr(self, f"tone_tre_k_{ch}", blocks.multiply_const_ff(0.0))
            setattr(self, f"tone_sum_{ch}", blocks.add_ff(1))
            setattr(self, f"headroom_{ch}", blocks.multiply_const_ff(hr))
            g = lambda n: getattr(self, f"{n}_{ch}")
            c(src, lp, nrsc, notch, vol)
            c(vol, (g("tone_sum"), 0))
            c(vol, g("tone_bass_lp"), g("tone_bass_k"), (g("tone_sum"), 1))
            c(vol, (g("tone_tre_hp"), 0))
            c(vol, g("tone_tre_lp"), (g("tone_tre_hp"), 1))
            c(g("tone_tre_hp"), g("tone_tre_k"), (g("tone_sum"), 2))
            c(g("tone_sum"), g("headroom"), (self.audio_sink, idx))

        # ---------------- measurements ----------------
        self._win = self.ch_rate // 50                    # 20 ms
        self.car_int = blocks.integrate_cc(self._win)     # coherent carrier
        self.car_mag = blocks.complex_to_mag_squared(1)
        self.car_probe = blocks.probe_signal_f()
        c(self.rot, self.car_int, self.car_mag, self.car_probe)
        self.CH_N = 4096
        self.snap_ch_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, self.CH_N)
        self.snap_ch = blocks.probe_signal_vc(self.CH_N)
        c(self.rot, self.snap_ch_vec, self.snap_ch)
        self.snap_pll_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, 2048)
        self.snap_pll_keep = blocks.keep_one_in_n(gr.sizeof_gr_complex * 2048, 2)
        self.snap_pll = blocks.probe_signal_vc(2048)
        c(self.pll, self.snap_pll_vec, self.snap_pll_keep, self.snap_pll)
        self.snap_aud_vec = blocks.stream_to_vector(gr.sizeof_float, 4096)
        self.snap_aud = blocks.probe_signal_vf(4096)
        c(self.sum_hp, self.snap_aud_vec, self.snap_aud)
        self.WB_N = 4096
        self.snap_wb_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, self.WB_N)
        self.snap_wb_keep = blocks.keep_one_in_n(gr.sizeof_gr_complex * self.WB_N,
                                                 max(1, int(self.samp_rate / self.WB_N / 4)))
        self.snap_wb = blocks.probe_signal_vc(self.WB_N)
        c(self.src, self.snap_wb_vec, self.snap_wb_keep, self.snap_wb)

        # ---------------- monitoring taps (read only, see rx_meters.py) ----------------
        # Audio ahead of volume / tone (0 dBFS = 100% modulation, mono), and the
        # envelope over the carrier minus 1 (x100 = percent modulation).
        self.audio_meter = self.mod_meter = None
        if rx_meters is not None:
            kb1, ka1, kb2, ka2 = rx_meters.k_weighting(self.audio_rate)
            self.audio_meter = rx_meters.AudioMeterBlock(self.audio_rate, ref=1.0)
            for idx, ch in enumerate(("l", "r")):
                k1 = filter.iir_filter_ffd(kb1, ka1, False)
                k2 = filter.iir_filter_ffd(kb2, ka2, False)
                setattr(self, f"kw1_{ch}", k1)
                setattr(self, f"kw2_{ch}", k2)
                lp = getattr(self, f"notch_{ch}")             # after de-emphasis and notch: what you hear
                c(lp, (self.audio_meter, idx))
                c(lp, k1, k2, (self.audio_meter, idx + 2))
            self.mod_meter = rx_meters.ModMeterBlock(scale=100.0, over_pos=125.0, over_neg=100.0)
            c(self.sum0, self.mod_meter)

        # ---------------- state ----------------
        self.if_mode = 'auto'
        self.IF_WIDE_KHZ = self.AM_WIDE_HZ / 1e3
        self.if_khz = self.bw / 1e3
        self.cnr_db = self.raw_cnr_db = self.pre_cnr_db = 0.0
        self.spec_snr_db, self.if_snr_db, self.occupied_khz = None, None, None
        self.adj_db = -60.0
        self.carrier_hz = 0.0
        self.channel_dbfs, self.composite_dbfs, self.clip_pct = -100.0, -100.0, 0.0
        self.pilot_level, self.pilot_ratio_db = 0.0, -99.0
        self.pilot_on, self.stereo_indicator = False, False
        self.wide, self.force_mono = False, False
        self.stereo_mode, self.stereo_system, self.pilot_hz = 'auto', 'cquam', 25   # auto: by the pilot
        self.auto_ceiling = self.AM_WIDE_HZ           # auto never opens wider than this
        self.current_blend, self.current_treble = 0.0, 1.0
        self.locked = False
        # FM-only things the GUI asks about
        self.rds_ps, self.rds_rt, self.rds_pi, self.rds_call, self.rds_pty = '', '', None, None, None
        self.rds_synced = False
        self.eq_mode, self.eq_active, self.eq_cnr_db, self.eq_gain_db = 'off', False, 0.0, 0.0
        self.eq_gate, self.mp_excess_db = False, 0.0
        self._rot_hz = 0.0
        self._n0_hz = None
        self._psd = None
        self._last_car = None
        self._last_wb = None
        self._last_ch = None
        self._last_pil = None
        self._pil_hits = 0
        self._cnr_slow = None
        self._tune_cnr = []
        self._blank_until = time.monotonic() + self.TUNE_SETTLE_S
        self._wb_seen = time.monotonic()
        self._t_last = time.monotonic()
        self._bw_cur = self.bw
        fr = np.fft.fftshift(np.fft.fftfreq(self.CH_N, 1 / self.ch_rate))
        self._fr = fr
        self._hann = np.hanning(self.CH_N)
        self._hann_wp = float(np.sum(self._hann ** 2))
        self._wbw = np.hanning(self.WB_N)
        self._wbw_wp = float(np.sum(self._wbw ** 2))
        self._apply_gain()

    # ------------------------------------------------------------------ helpers
    def _a(self, hz):
        return 1 - math.exp(-2 * math.pi * hz / self.ch_rate)

    def _lo(self, f):
        lo = f + self.LO_OFFSET
        return lo + self.UPCONVERTER_HZ if self.am_source == 'upconverter' else lo

    def _lp_taps(self, hz):
        """Windowed-sinc low-pass, fixed length (fixed delay) so it can slide."""
        n, m = self.IF_TAPS, (self.IF_TAPS - 1) // 2
        fc = min(hz + 300.0, 0.45 * self.ch_rate) / self.ch_rate
        h = 2 * fc * np.sinc(2 * fc * (np.arange(n) - m)) * np.kaiser(n, 6.0)
        return [float(v) for v in h / np.sum(h)]

    def _tone_atten_db(self, bass_db, treble_db):
        r = self.TONE_RANGE_DB
        return max(0.0, min(bass_db, r), min(treble_db, r)) + self.TONE_SAFETY_DB

    def _apply_gain(self):
        gl, gr_ = (0.0, 0.0) if self._muted else self._out_gain
        k = 0.5 * self.AM_AUDIO_GAIN
        self.vol_l.set_k(k * gl)
        self.vol_r.set_k(k * gr_)

    def _apply_bw(self, hz):
        self.bw = hz
        self.if_khz = hz / 1e3
        self.current_treble = hz / self.AM_WIDE_HZ
        self.if_filter.set_taps(self._lp_taps(hz))
        taps = self._lp_taps(hz)
        self.alp_l.set_taps(taps)
        self.alp_r.set_taps(taps)

    # --------------------------------------------------------------- controls
    def set_freq(self, freq):
        with self._lock:
            self.freq = freq
            self.src.set_center_freq(self._lo(freq), 0)
            self._rot_hz = 0.0
            self.rot.set_phase_inc(0.0)
            self._psd, self._cnr_slow = None, None
            self._tune_cnr = []
            self._pil_hits, self.pilot_on, self.stereo_indicator = 0, False, False
            self.blend_k.set_k(0.0)
            self.current_blend = 0.0
            self._blank_until = time.monotonic() + self.TUNE_SETTLE_S

    def set_output_gain(self, left, right):
        with self._lock:
            self._out_gain = (max(0.0, float(left)), max(0.0, float(right)))
            self._apply_gain()

    def set_mute(self, muted):
        with self._lock:
            self._muted = bool(muted)
            self._apply_gain()

    def set_tone(self, bass_db, treble_db):
        r = self.TONE_RANGE_DB
        gb = 10 ** (min(max(bass_db, -r), r) / 20)
        gt = 10 ** (min(max(treble_db, -r), r) / 20)
        fb = self.TONE_BASS_HZ / (gb if gb < 1 else 1.0)
        ft = self.TONE_TREBLE_HZ * (gt if gt < 1 else 1.0)
        hr = 10 ** (-self._tone_atten_db(bass_db, treble_db) / 20)
        for ch in ("l", "r"):
            getattr(self, f"tone_bass_lp_{ch}").set_taps(self._a(fb))
            getattr(self, f"tone_bass_k_{ch}").set_k(gb - 1)
            getattr(self, f"tone_tre_lp_{ch}").set_taps(self._a(ft))
            getattr(self, f"tone_tre_k_{ch}").set_k(gt - 1)
            getattr(self, f"headroom_{ch}").set_k(hr)

    def set_wide(self, on):
        """Wide: the filter fixed at 10.2 kHz, which passes everything an NRSC-1
        station transmits, with a 10 kHz notch for adjacent-channel carriers.
        Off: back to the chosen IF mode (auto narrows on noise and neighbours)."""
        with self._lock:
            self.wide = bool(on)
            if self.wide:
                w0 = 2 * math.pi * self.NOTCH_HZ / self.audio_rate
                r = self.NOTCH_R
                b = [1.0, -2 * math.cos(w0), 1.0]
                a = [1.0, -2 * r * math.cos(w0), r * r]
                g = (1 + a[1] + a[2]) / (b[0] + b[1] + b[2])        # unity at low frequencies
                b = [v * g for v in b]
                self._bw_cur = self.AM_XWIDE_HZ
                self._apply_bw(self.AM_XWIDE_HZ)
            else:
                b, a = [1.0, 0.0, 0.0], [1.0, 0.0, 0.0]
                hz = {'wide': self.AM_WIDE_HZ, 'narrow': self.AM_NARROW_HZ}.get(
                    self.if_mode, min(self._bw_cur, self.auto_ceiling))
                self._bw_cur = hz
                self._apply_bw(hz)
            for ch in ("l", "r"):
                getattr(self, f"notch_{ch}").set_taps(b, a)

    def set_auto_ceiling(self, hz):
        """The widest the auto filter will open (normal width)."""
        with self._lock:
            self.auto_ceiling = float(min(max(hz, self.AM_MIN_HZ), self.AM_WIDE_HZ))
            if not self.wide and self.if_mode == 'auto' and self._bw_cur > self.auto_ceiling:
                self._bw_cur = self.auto_ceiling
                self._apply_bw(self.auto_ceiling)

    def set_stereo_system(self, mode):
        """'auto' (by the pilot: 25 Hz C-QUAM, 15 Hz Kahn), 'cquam' or 'kahn'."""
        if mode in ('auto', 'cquam', 'kahn'):
            self.stereo_mode = mode
            if mode != 'auto':
                self.stereo_system = mode
            self._apply_decoder()

    def _apply_decoder(self):
        kahn = self.stereo_system == 'kahn'
        self.cq_k.set_k(0.0 if kahn else 1.0)
        self.kahn_k.set_k(1.0 if kahn else 0.0)
        self.pilot_hz = 15 if kahn else 25

    def set_force_mono(self, on):
        """Mono: ignore the C-QUAM stereo (the blend goes to mono, STEREO goes out)."""
        self.force_mono = bool(on)

    def set_if_mode(self, mode):
        if mode in ('auto', 'wide', 'narrow'):
            with self._lock:
                self.if_mode = mode
                if self.wide:                           # Wide overrides until it's switched off
                    return
                if mode == 'wide':
                    self._bw_cur = self.AM_WIDE_HZ
                    self._apply_bw(self.AM_WIDE_HZ)
                elif mode == 'narrow':
                    self._bw_cur = self.AM_NARROW_HZ
                    self._apply_bw(self.AM_NARROW_HZ)

    def set_manual_gain(self, db):
        self.gain_db = float(db)
        if self.am_source != 'direct_q':
            self.src.set_gain_mode(False, 0)
            self.src.set_gain(self.gain_db, 0)

    def set_agc(self, on):
        self.agc_enabled = bool(on)
        if self.am_source != 'direct_q':
            self.src.set_gain_mode(bool(on), 0)

    # FM-only controls: accepted, nothing to do on AM
    def set_enhancements(self, blend=None, hicut=None):
        pass

    def set_eq_mode(self, mode):
        pass

    def set_noise_blanker(self, on):
        pass

    def set_dip_hicut(self, on):
        pass

    def set_noise_reduction(self, on):
        pass

    def eq_reset(self):
        pass

    def cnr_since_tune(self):
        with self._lock:
            return list(self._tune_cnr)

    NRSC_POLE_HZ, NRSC_ZERO_HZ = 2122.0, 8700.0      # NRSC-1-C receiver de-emphasis

    def set_nrsc_deemphasis(self, on):
        """NRSC-1 modified 75 us de-emphasis: a pole at 2122 Hz and a zero at
        8700 Hz, the complement of the AM pre-emphasis most US stations transmit
        (-10 dB at 10 kHz). Matched-z at the audio rate, which lands within
        0.04 dB of NRSC-1-C Table 2 from 50 Hz to 10 kHz. Off = flat."""
        self.nrsc_on = bool(on)
        if self.nrsc_on:
            fs = float(self.audio_rate)
            p = math.exp(-2 * math.pi * self.NRSC_POLE_HZ / fs)
            z = math.exp(-2 * math.pi * self.NRSC_ZERO_HZ / fs)
            g = (1 - p) / (1 - z)                        # unity at low frequencies
            b, a = [g, -g * z], [1.0, -p]
        else:
            b, a = [1.0, 0.0], [1.0, 0.0]
        for ch in ("l", "r"):
            getattr(self, f"nrsc_{ch}").set_taps(b, a)

    def audio_lag(self):
        """How far the sound card trails what this receiver measures, in seconds:
        (meters, front end), from the samples queued between the audio low-pass
        (the meter tap) or the channel translator and the audio sink."""
        try:
            played = self.audio_sink.nitems_read(0)
            tap = self.notch_l.nitems_written(0) * self.audio_rate / self.ch_rate
            front = self.xlate.nitems_written(0) * self.audio_rate / (self.samp_rate / 10.0)
        except Exception:
            return None
        r = float(self.audio_rate)
        return max(0.0, tap - played) / r, max(0.0, front - played) / r

    def monitor(self):
        """Meter readings since the last call (mod in percent), or None."""
        if self.audio_meter is None:
            return None
        return dict(audio=self.audio_meter.read(), mod=self.mod_meter.read())

    def snapshot(self, kind):
        if kind == 'iq':
            return np.asarray(self.snap_ch.level(), dtype=np.complex64)
        if kind in ('pre', 'post'):
            return np.asarray(self.snap_pll.level(), dtype=np.complex64)
        if kind == 'mpx':
            return np.asarray(self.snap_aud.level(), dtype=np.float32)
        return np.zeros(0, np.complex64)

    def readout(self):
        """ADV text for AM."""
        st = 'STEREO' if self.stereo_indicator else ('pilot, blended to mono' if self.pilot_on else 'mono')
        bw = 'wide' if self.bw >= self.AM_WIDE_HZ else f'{self.bw / 1e3:4.1f} kHz'
        src = {'direct_q': 'Q-branch direct sampling', 'native': 'native HF tuning',
               'upconverter': f'upconverter +{self.UPCONVERTER_HZ / 1e6:.0f} MHz'}[self.am_source]
        return (f"C/N      {self.cnr_db:5.1f} dB in 10 kHz   carrier {self.channel_dbfs:6.1f} dBFS"
                f"{'  (PLL locked)' if self.locked else ''}\n"
                f"IF       {bw:9s} {self.if_mode:6s}  adjacent carriers {self.adj_db:+6.1f} dB"
                f"   carrier offset {self.carrier_hz:+6.1f} Hz\n"
                f"Stereo   {'Kahn ISB' if self.stereo_system == 'kahn' else 'C-QUAM'} {st}   "
                f"pilot {self.pilot_hz} Hz {100 * self.pilot_level:4.1f}% "
                f"({self.pilot_ratio_db:+5.1f} dB)  blend {self.current_blend:4.2f}\n"
                f"Clip     {self.clip_pct:6.3f} %   source: {src}")

    # ---------------------------------------------------------------- update
    def update_blend(self):
        """Called every ~20 ms by the GUI: measurements, IF bandwidth, stereo."""
        now = time.monotonic()
        with self._lock:
            dt = min(now - self._t_last, 0.2)
            self._t_last = now

            # stall watchdog (the GUI reconnects on repeated errors)
            wb = self.snap_wb.level()
            if len(wb) == self.WB_N and np.any(np.asarray(wb)):
                if self._last_wb is None or wb[0] != self._last_wb:
                    self._last_wb = wb[0]
                    self._wb_seen = now
                    x = np.asarray(wb)
                    P = np.abs(np.fft.fftshift(np.fft.fft(x * self._wbw))) ** 2 / self._wbw_wp
                    k = self.WB_N // 10
                    # (with |FFT(x w)|^2 / sum(w^2), white noise shows its total power in
                    # every bin: per Hz is that over the whole sample rate)
                    n0 = float(np.percentile(P[k:-k], 30)) / self.samp_rate
                    if n0 > 0 and math.isfinite(n0):
                        self._n0_hz = n0 if self._n0_hz is None else 0.8 * self._n0_hz + 0.2 * n0
                    self.composite_dbfs = 10 * math.log10(max(float(np.mean(np.abs(x) ** 2)), 1e-12))
                    self.clip_pct = 100.0 * float(np.mean((np.abs(x.real) > 0.98) | (np.abs(x.imag) > 0.98)))
            if now - self._wb_seen > self.STALL_S:
                raise RuntimeError(f"no samples from the dongle for {now - self._wb_seen:.1f} s")

            try:
                self._measure(now)
            except Exception as e:                       # never a reason to stop
                if not getattr(self, '_said', False):
                    self._said = True
                    print(f"AM measurement: {type(e).__name__}: {e}")

    def _measure(self, now):
        # carrier-to-noise, 20 ms windows
        cp = self.car_probe.level()
        if cp != self._last_car:
            self._last_car = cp
            C = cp / (self._win * self._win)
            self.channel_dbfs = 10 * math.log10(max(C, 1e-15))
            if self._n0_hz and now >= self._blank_until:
                q = 10 * math.log10(max(C, 1e-20) / (self._n0_hz * 1e4))
                q = min(max(q, -10.0), 80.0)
                self._tune_cnr.append(q)
                if len(self._tune_cnr) > 200:
                    del self._tune_cnr[:100]
                self._cnr_slow = q if self._cnr_slow is None else self._cnr_slow + 0.1 * (q - self._cnr_slow)
                self.cnr_db = self.raw_cnr_db = self.pre_cnr_db = self.spec_snr_db = self._cnr_slow

        # channel spectrum: carrier offset, adjacent carriers
        ch = self.snap_ch.level()
        if len(ch) == self.CH_N and (self._last_ch is None or ch[0] != self._last_ch) \
                and np.any(np.asarray(ch)):
            self._last_ch = ch[0]
            X = np.abs(np.fft.fftshift(np.fft.fft(np.asarray(ch) * self._hann))) ** 2 / self._hann_wp
            self._psd = X if self._psd is None else 0.7 * self._psd + 0.3 * X
            fr, P = self._fr, self._psd
            near = np.where(np.abs(fr) < 400)[0]
            i = near[np.argmax(P[near])]
            if 0 < i < len(P) - 1:                       # parabolic peak
                a, b, c = np.log(P[i - 1] + 1e-30), np.log(P[i] + 1e-30), np.log(P[i + 1] + 1e-30)
                d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
                off = fr[i] + d * (fr[1] - fr[0])
            else:
                off = fr[i]
            self.carrier_hz = off
            own = P[i]
            if now >= self._blank_until and abs(off) > 0.5:  # steer the coarse correction
                self._rot_hz += 0.5 * off
                self.rot.set_phase_inc(-2 * math.pi * self._rot_hz / self.ch_rate)
            adj = []
            for side in (-10e3, 10e3):
                m = np.where(np.abs(fr - side) < 300)[0]
                adj.append(float(np.max(P[m])))
            self.adj_db = 10 * math.log10(max(max(adj), 1e-30) / max(own, 1e-30))
            self.locked = bool(self.pll.lock_detector()) if hasattr(self.pll, 'lock_detector') else True

        # IF bandwidth
        if not self.wide and self.if_mode == 'auto' and self._cnr_slow is not None and now >= self._blank_until:
            tgt = np.interp(self._cnr_slow, [t[0] for t in self.BW_TABLE], [t[1] for t in self.BW_TABLE])
            for lvl, lim in self.ADJ_TABLE:
                if self.adj_db >= lvl:
                    tgt = min(tgt, lim)
            tgt = float(min(max(tgt, self.AM_MIN_HZ), self.auto_ceiling))
            dt = 0.02
            rate = self.BW_SLEW_IN if tgt < self._bw_cur else self.BW_SLEW_OUT
            self._bw_cur += max(-rate * dt, min(rate * dt, tgt - self._bw_cur))
            if abs(self._bw_cur - self.bw) >= self.BW_STEP or \
                    (self._bw_cur >= self.AM_WIDE_HZ) != (self.bw >= self.AM_WIDE_HZ):
                self._apply_bw(self._bw_cur)

        # C-QUAM pilot: 25 Hz in L-R, a second at a time
        pv = self.pil_probe.level()
        if len(pv) == 1024 and (self._last_pil is None or pv[0] != self._last_pil) and np.any(np.asarray(pv)):
            self._last_pil = pv[0]
            x = np.asarray(pv)
            w = np.hanning(len(x))
            Y = np.abs(np.fft.rfft((x - np.mean(x)) * w, 8 * len(x)))      # zero-padded: 25 Hz lands on a bin,
            f = np.fft.rfftfreq(8 * len(x), 1 / 1000.0)                     # not between two (1.8 dB low before)
            nb = np.median(Y[((f > 4) & (f < 11.5)) | ((f > 19) & (f < 21.5)) | ((f > 31) & (f < 45))])
            pk25, pk15 = Y[(f > 23) & (f < 27.5)].max(), Y[(f > 13) & (f < 17.5)].max()
            lv25, lv15 = 2 * pk25 / np.sum(w) / self._pil_gain, 2 * pk15 / np.sum(w) / self._pil_gain15
            r25, r15 = (20 * math.log10(max(p, 1e-12) / max(nb, 1e-12)) for p in (pk25, pk15))
            ok = lambda lv, r: lv >= self.PILOT_MIN and r >= self.PILOT_RATIO_DB
            if self.stereo_mode == 'auto':                  # like the multi-system radios: the pilot picks
                was = self.stereo_system
                if ok(lv15, r15) and (not ok(lv25, r25) or lv15 > lv25):
                    self.stereo_system = 'kahn'
                elif ok(lv25, r25):
                    self.stereo_system = 'cquam'
                if self.stereo_system != was:
                    self._apply_decoder()
            kahn = self.stereo_system == 'kahn'
            self.pilot_level, self.pilot_ratio_db = (lv15, r15) if kahn else (lv25, r25)
            present = self.pilot_level >= self.PILOT_MIN and self.pilot_ratio_db >= self.PILOT_RATIO_DB
            gone = self.pilot_level < self.PILOT_MIN * 0.5 or self.pilot_ratio_db < self.PILOT_RATIO_DB - 4
            if present and now >= self._blank_until:
                self._pil_hits = min(self._pil_hits + 1, 3)
            elif gone:
                self._pil_hits = max(self._pil_hits - 1, 0)
            if self._pil_hits >= 2:
                self.pilot_on = True
            elif self._pil_hits == 0:
                self.pilot_on = False

        # stereo blend: only with the pilot, and as the carrier-to-noise allows
        tb = 0.0
        if self.pilot_on and self._cnr_slow is not None and not self.force_mono:
            tb = (self._cnr_slow - self.STEREO_MONO_DB) / (self.STEREO_FULL_DB - self.STEREO_MONO_DB)
            tb = min(max(tb, 0.0), 1.0)
        self.current_blend += max(-0.04, min(0.04, tb - self.current_blend))
        self.blend_k.set_k(self.current_blend)
        self.stereo_indicator = self.pilot_on and self.current_blend >= 0.3
