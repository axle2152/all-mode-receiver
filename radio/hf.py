"""
radio/hf.py - SSB, CW and synchronous AM: the amateur and utility bands, and shortwave in sync
=============================================================================================
One receiver for the modes that need a beat oscillator or a carrier lock. Changing between
them (USB, LSB, CW, synchronous AM on either sideband) and changing the filter width never
reopen the dongle. Plain AM stays with the AM receiver (radio/am.py), which has C-QUAM and the
add-ons.

Signal chain (960 ksps from the RTL -> 48 kHz complex channel)
  source        as the AM receiver: Q-branch direct sampling below 28.8 MHz, the quadrature
                tuner above it (6 m, 2 m and 70 cm SSB too), native HF on a V4, or an
                upconverter; the LO LO_OFFSET above the dial. This receiver's own tuner AGC
                (radio/receiver.py) when there's a tuner
  blankers      on the full 960 ksps, ahead of every filter (radio/blanker.py): pulses patched
                with a straight line across; the impulse blanker takes only those far over the
                band, the noise blanker (LOW, MED, HIGH) smaller ones too
  channel       two decimating filters; a coarse frequency correction that only synchronous
                AM uses (from the channel spectrum, as the AM receiver does)
  detector      SSB and CW straight from the channel: the dial frequency is the carrier's, so
                USB voice sits 300 Hz to 3 kHz above it and LSB below; CW turns the carrier
                into a CW_PITCH tone. Synchronous AM: a PLL (about 25 Hz) locks to the carrier
                alone (+/-150 Hz around it, so a strong neighbour can't wobble it) and turns the
                whole channel by its phase, with the delay matched, putting the carrier on the
                real axis, so either sideband can be heard alone, clear of interference on the
                other
  filter        one complex band-pass: the chosen sideband (with the carrier, for sync AM) at
                the chosen width, steep enough (about 150 Hz) to keep the other sideband out
  AGC           radio/agc.py: look-ahead attack, hang and decay by mode; its ceiling comes from
                the channel's noise floor, so an empty frequency plays its noise NOISE_BELOW_DB
                under a station
  audio         the real part, a DC block (the carrier, on sync AM), up to 48 kHz, then the
                same volume and bass / treble as the other receivers

The detector, filter and AGC run at 24 kHz (DET_RATE) with plain FIR filters, so the audio
flows on steadily: as FFT filters at 48 kHz they passed it on in 30 to 50 ms lumps, which with
the dongle's own (136 ms at 960 ksps) left the sound card short now and then ("aU").

Quality figure: the signal-to-noise ratio in the passband (the filter's output power against
the channel's noise floor across that width; the floor is a low percentile of the channel
spectrum, as on AM).
"""

import math
import threading
import time

import numpy as np
from gnuradio import gr, blocks, filter, analog

from . import meters as rx_meters
from .agc import AGCBlock, max_gain_for
from .blanker import BlankerBlock, describe
from .receiver import Receiver, TunerGain


def _kaiser_lp(fc, fs, n, beta=7.0):
    m = (n - 1) / 2
    h = 2 * fc / fs * np.sinc(2 * fc / fs * (np.arange(n) - m)) * np.kaiser(n, beta)
    return h / np.sum(h)


def _band_taps(lo, hi, fs, n, beta=8.0):
    """Complex band-pass from lo to hi Hz (either side of 0), unity gain in the passband."""
    h = _kaiser_lp((hi - lo) / 2, fs, n, beta)
    k = np.arange(n) - (n - 1) / 2
    return h * np.exp(2j * np.pi * (hi + lo) / 2 * k / fs)


class HFReceiver(TunerGain, Receiver):
    BAND = 'HF'
    FEATURES = frozenset({'hf_mode', 'hf_width', 'blankers'})

    # ---- source (as the AM receiver) ----
    AM_SOURCE        = 'direct_q'   # 'direct_q' | 'native' | 'upconverter'
    UPCONVERTER_HZ   = 125e6
    LO_OFFSET        = 20e3
    SAMP_RATE        = 960_000
    DECIM1, DECIM2   = 10, 2        # 960 k -> 96 k -> 48 k
    CH_RATE          = 48_000

    # ---- modes and filters ----
    MODES  = ('USB', 'LSB', 'CW', 'SAM_U', 'SAM_L')
    WIDTHS = {'USB': (1800, 2400, 2700, 3000), 'LSB': (1800, 2400, 2700, 3000),
              'CW': (250, 500, 1000, 2400), 'SAM_U': (3000, 4500, 6000, 8000),
              'SAM_L': (3000, 4500, 6000, 8000)}
    DEFAULT_WIDTH = {'USB': 2700, 'LSB': 2700, 'CW': 500, 'SAM_U': 4500, 'SAM_L': 4500}
    SSB_LOW_HZ       = 300.0        # voice starts this far from the carrier (200 Hz at 3 kHz wide)
    CW_PITCH         = 600.0        # the tone a CW signal tuned on the dial plays at
    SAM_EDGE_HZ      = 50.0         # sync AM's filter reaches this far past the carrier
    DET_DECIM        = 2            # the detector runs at 24 kHz
    SB_TAPS          = 769          # about 150 Hz from pass to stop (80 dB), at 24 kHz
    RESAMPLE_TAPS    = 47           # 48 -> 24 kHz and back: flat to 9 kHz, 70 dB from 15 kHz
    RESAMPLE_HZ      = 12000.0
    PLL_BW_HZ        = 25.0
    PLL_RANGE_HZ     = 300.0
    CARRIER_HZ       = 150.0        # the PLL hears the carrier alone: +/-150 Hz around it
    CARRIER_TAPS     = 361          # at 24 kHz

    # ---- AGC: (hang s, decay dB/s) by mode ----
    AGC_REF          = 0.5
    AGC_SPEED        = {'USB': (0.4, 15.0), 'LSB': (0.4, 15.0), 'CW': (0.2, 30.0),
                        'SAM_U': (0.0, 6.0), 'SAM_L': (0.0, 6.0)}
    NOISE_BELOW_DB   = 20.0         # an empty frequency plays its noise this far under a station
    SAM_GAIN         = 2.0          # sync AM's audio is half the sideband: as loud as SSB
    MPX_FULL         = AGC_REF      # the audio snapshot: the AGC's level

    # ---- blankers (radio/blanker.py), both on the dongle's full stream ----
    # SSB and CW have no carrier to measure a spike against, so the impulse blanker takes only
    # pulses standing 24 dB over the band's median (static crashes, sparks); the noise blanker's
    # LOW, MED and HIGH go down to 18, 15 and 12 dB. Speech and keying are far longer than a
    # pulse (50 us at most), so they're never taken for one
    NB_K             = {'low': 8.0, 'med': 5.6, 'high': 4.0}
    IB_K             = 16.0
    NB_PRE, NB_POST, NB_MAX = 3, 6, 48

    # ---- measurements ----
    NOISE_PCT        = 30.0         # the channel's noise floor: this percentile of its spectrum
    SEEK_MIN_DB      = 10.0         # search stops, and TUNED lights, this far over the noise
    TUNE_SETTLE_S    = 0.25

    def __init__(self, freq=14.2e6, rf_gain=29.7, agc=True, audio_device="",
                 device_args="numchan=1 rtl=0", am_source=None, upconverter_hz=None,
                 mode='USB', width=None, **_):
        Receiver.__init__(self, "HF Receiver")
        self._lock = threading.RLock()
        self.am_source = am_source or self.AM_SOURCE
        if upconverter_hz:
            self.UPCONVERTER_HZ = float(upconverter_hz)
        self.freq = freq
        self.samp_rate, self.ch_rate = self.SAMP_RATE, self.CH_RATE
        self.det_rate = self.CH_RATE // self.DET_DECIM
        self.audio_rate = self.mpx_rate = self.CH_RATE
        c = self.connect

        # ---------------- source ----------------
        args = device_args
        if self.am_source == 'direct_q':
            args = f"{device_args},direct_samp=2"
        self.src = self._make_source(args)
        self.src.set_sample_rate(self.samp_rate)
        self.src.set_center_freq(self._lo(freq), 0)
        self.src.set_freq_corr(0, 0)
        self.src.set_dc_offset_mode(0, 0)
        self.src.set_iq_balance_mode(0, 0)
        self._gain_setup(rf_gain, agc, self.am_source == 'direct_q')

        # ---------------- blankers: the full stream, ahead of every filter ----------------
        self.nb = BlankerBlock(self.samp_rate, pre=self.NB_PRE, post=self.NB_POST,
                               max_run=self.NB_MAX, backoff=True)
        self.nb.set_output_multiple(self.samp_rate // 100)      # 10 ms a call: Python costs per call

        # ---------------- channel ----------------
        r1 = self.samp_rate / self.DECIM1
        self.xlate = filter.freq_xlating_fir_filter_ccf(
            self.DECIM1, [float(v) for v in _kaiser_lp(22e3, self.samp_rate, 121)], -self.LO_OFFSET,
            self.samp_rate)
        self.dec2 = filter.fir_filter_ccf(self.DECIM2, [float(v) for v in _kaiser_lp(16e3, r1, 63)])
        self.rot = blocks.rotator_cc(0.0)               # sync AM's coarse carrier correction
        c(self.src, self.nb, self.xlate, self.dec2, self.rot)

        # ---------------- detectors: straight (SSB, CW) or carrier-locked (sync AM), at 24 kHz ----------------
        rs = _kaiser_lp(self.RESAMPLE_HZ, self.ch_rate, self.RESAMPLE_TAPS)
        self.dec3 = filter.fir_filter_ccf(self.DET_DECIM, [float(v) for v in rs])
        c(self.rot, self.dec3)
        self.sel_d = blocks.multiply_const_cc(1.0)
        self.car_lp = filter.fir_filter_ccf(1, [float(v) for v in _kaiser_lp(self.CARRIER_HZ, self.det_rate,
                                                                              self.CARRIER_TAPS)])
        w = 2 * math.pi / self.det_rate
        self.pll = analog.pll_refout_cc(w * self.PLL_BW_HZ, w * self.PLL_RANGE_HZ, -w * self.PLL_RANGE_HZ)
        self.sam_dly = blocks.delay(gr.sizeof_gr_complex, (self.CARRIER_TAPS - 1) // 2)
        self.sam = blocks.multiply_conjugate_cc(1)      # the channel turned by the carrier's phase
        self.sel_s = blocks.multiply_const_cc(0.0)
        self.det = blocks.add_cc(1)
        c(self.dec3, self.sel_d, (self.det, 0))
        c(self.dec3, self.car_lp, self.pll, (self.sam, 1))
        c(self.dec3, self.sam_dly, (self.sam, 0))
        c(self.sam, self.sel_s, (self.det, 1))
        self.bfo = blocks.rotator_cc(0.0)               # CW: the carrier up to the pitch
        self.sb = filter.fir_filter_ccc(1, [1.0 + 0j])
        self.agc = AGCBlock(self.det_rate, ref=self.AGC_REF, max_gain=1e3)
        self.agc.set_output_multiple(self.det_rate // 100)   # 10 ms a call: Python costs per call
        c(self.det, self.bfo, self.sb, self.agc)

        # ---------------- audio ----------------
        self.dc_lp = filter.single_pole_iir_filter_ff(1 - math.exp(-2 * math.pi * 30.0 / self.det_rate))
        self.dc_hp = blocks.sub_ff(1)
        self.post = blocks.multiply_const_ff(1.0)
        self.up = filter.interp_fir_filter_fff(self.DET_DECIM, [float(v) for v in rs * self.DET_DECIM])
        c(self.agc, (self.dc_hp, 0))
        c(self.agc, self.dc_lp, (self.dc_hp, 1))
        c(self.dc_hp, self.post, self.up)
        self._out_gain, self._muted, self._ready = (1.0, 1.0), False, False
        hr = 10 ** (-self._tone_atten_db(0, 0) / 20)
        self.audio_sink = self._make_audio_sink(self.audio_rate, audio_device)
        for idx, ch in enumerate(("l", "r")):
            vol = blocks.multiply_const_ff(0.0)
            setattr(self, f"vol_{ch}", vol)
            self._make_tone(ch, hr)
            c(self.up, vol)
            self._connect_tone(vol, ch, idx)

        # ---------------- measurements ----------------
        self._win = self.det_rate // 50                  # 20 ms
        self.sb_mag = blocks.complex_to_mag_squared(1)   # power in the passband
        self.sb_int = blocks.integrate_ff(self._win)
        self.sb_probe = blocks.probe_signal_f()
        c(self.sb, self.sb_mag, self.sb_int, self.sb_probe)
        self.CH_N = 4096
        self.snap_ch_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, self.CH_N)
        self.snap_ch = blocks.probe_signal_vc(self.CH_N)
        c(self.rot, self.snap_ch_vec, self.snap_ch)
        self.snap_sb_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, 2048)
        self.snap_sb = blocks.probe_signal_vc(2048)
        c(self.sb, self.snap_sb_vec, self.snap_sb)
        self.snap_aud_vec = blocks.stream_to_vector(gr.sizeof_float, 4096)
        self.snap_aud = blocks.probe_signal_vf(4096)
        c(self.up, self.snap_aud_vec, self.snap_aud)
        self.WB_N = 4096
        self.snap_wb_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, self.WB_N)
        self.snap_wb_keep = blocks.keep_one_in_n(gr.sizeof_gr_complex * self.WB_N,
                                                 max(1, int(self.samp_rate / self.WB_N / 4)))
        self.snap_wb = blocks.probe_signal_vc(self.WB_N)
        c(self.src, self.snap_wb_vec, self.snap_wb_keep, self.snap_wb)

        # ---------------- monitoring taps (read only, see radio/meters.py) ----------------
        # The audio as heard (0 dB = the AGC's level), and on sync AM the envelope over the
        # carrier minus 1 (x100 = percent modulation), as on AM.
        self.audio_meter = self.mod_meter = None
        if rx_meters is not None:
            kb1, ka1, kb2, ka2 = rx_meters.k_weighting(self.audio_rate)
            self.audio_meter = rx_meters.AudioMeterBlock(self.audio_rate, ref=self.AGC_REF)
            for idx, ch in enumerate(("l", "r")):
                k1 = filter.iir_filter_ffd(kb1, ka1, False)
                k2 = filter.iir_filter_ffd(kb2, ka2, False)
                setattr(self, f"kw1_{ch}", k1)
                setattr(self, f"kw2_{ch}", k2)
                c(self.up, (self.audio_meter, idx))
                c(self.up, k1, k2, (self.audio_meter, idx + 2))
            self.env = blocks.complex_to_mag(1)
            self.env_car = filter.single_pole_iir_filter_ff(1 - math.exp(-1.0 / (0.25 * self.det_rate)))
            self.env_floor = blocks.add_const_ff(1e-9)
            self.env_norm = blocks.divide_ff(1)
            self.env_m1 = blocks.add_const_ff(-1.0)
            self.mod_meter = rx_meters.ModMeterBlock(scale=100.0, over_pos=125.0, over_neg=100.0)
            c(self.sam, self.env)
            c(self.env, (self.env_norm, 0))
            c(self.env, self.env_car, self.env_floor, (self.env_norm, 1))
            c(self.env_norm, self.env_m1, self.mod_meter)
            self.audio_meter.set_output_multiple(self.audio_rate // 100)   # 10 ms a call, as the AGC
            self.mod_meter.set_output_multiple(self.det_rate // 100)

        # ---------------- state ----------------
        self.mode, self.width = None, None
        self.widths = dict(self.DEFAULT_WIDTH)
        self.cnr_db = self.raw_cnr_db = self.pre_cnr_db = 0.0
        self.spec_snr_db = None
        self.channel_dbfs, self.composite_dbfs, self.clip_pct = -120.0, -120.0, 0.0
        self.carrier_hz = 0.0
        self.locked = False
        self.agc_gain_db = 0.0
        self.if_khz, self.bw = 0.0, 0.0
        self.adj_db, self.occupied_khz = -60.0, None
        self.current_blend, self.current_treble = 0.0, 1.0
        # What the window asks every receiver (stereo and station data: none here)
        self.pilot_level, self.pilot_ratio_db = 0.0, -99.0
        self.pilot_on, self.stereo_indicator = False, False
        self.rds_ps, self.rds_rt, self.rds_pi, self.rds_call, self.rds_pty = '', '', None, None, None
        self.rds_synced = False
        self.eq_mode, self.eq_active, self.eq_cnr_db, self.eq_gain_db = 'off', False, 0.0, 0.0
        self.eq_gate, self.mp_excess_db = False, 0.0
        self._n0_hz = None
        self._rot_hz = 0.0
        self._psd = None
        self._q_slow = None
        self._tune_cnr = []
        self._last_sb = self._last_wb = self._last_ch = None
        self._blank_until = time.monotonic() + self.TUNE_SETTLE_S
        fr = np.fft.fftshift(np.fft.fftfreq(self.CH_N, 1 / self.ch_rate))
        self._fr = fr
        self._floor_bins = np.abs(fr) < 15e3            # inside the channel filters' passband
        self._hann = np.hanning(self.CH_N)
        self._hann_wp = float(np.sum(self._hann ** 2))
        self._pct_mean = -math.log(1.0 - self.NOISE_PCT / 100.0)
        self.set_mode(mode if mode in self.MODES else 'USB', width)
        self.set_blankers(impulse=True, noise='off')     # as the window starts them
        self._apply_gain()

    # ------------------------------------------------------------------ helpers
    def _lo(self, f):
        lo = f + self.LO_OFFSET
        return lo + self.UPCONVERTER_HZ if self.am_source == 'upconverter' else lo

    def passband(self, mode=None, width=None):
        """(lo, hi) Hz from the carrier that the filter passes for this mode and width."""
        mode = mode or self.mode
        w = float(width or self.widths[mode])
        if mode in ('USB', 'LSB'):
            lo = min(self.SSB_LOW_HZ, 3200.0 - w)
            band = (lo, lo + w)
            return band if mode == 'USB' else (-band[1], -band[0])
        if mode == 'CW':
            return (self.CW_PITCH - w / 2, self.CW_PITCH + w / 2)
        return (-self.SAM_EDGE_HZ, w) if mode == 'SAM_U' else (-w, self.SAM_EDGE_HZ)

    def _apply_gain(self):
        gl, gr_ = (0.0, 0.0) if self._muted or not self._ready else self._out_gain
        self.vol_l.set_k(gl)
        self.vol_r.set_k(gr_)

    # --------------------------------------------------------------- controls
    def set_mode(self, mode, width=None):
        """USB, LSB, CW, SAM_U or SAM_L, at its width (or the last one it had), without a restart."""
        if mode not in self.MODES:
            return
        with self._lock:
            sam = mode.startswith('SAM')
            if mode != self.mode:
                self.mode = mode
                self.sel_d.set_k(0.0 if sam else 1.0)
                self.sel_s.set_k(1.0 if sam else 0.0)
                self.bfo.set_phase_inc(2 * math.pi * self.CW_PITCH / self.det_rate if mode == 'CW' else 0.0)
                self.post.set_k(self.SAM_GAIN if sam else 1.0)
                self.agc.core.set_speed(*self.AGC_SPEED[mode])
                if not sam:                              # the coarse correction is sync AM's alone
                    self._rot_hz = 0.0
                    self.rot.set_phase_inc(0.0)
                    self.carrier_hz = 0.0
                    self.locked = False
                self.width = None
            self.set_width(width or self.widths[mode])

    def set_width(self, hz):
        """The filter's width for the mode in use (one of WIDTHS, else the nearest)."""
        with self._lock:
            hz = min(self.WIDTHS[self.mode], key=lambda w: abs(w - float(hz)))
            self.widths[self.mode] = hz
            if hz == self.width:
                return
            self.width = hz
            lo, hi = self.passband()
            self.sb.set_taps([complex(v) for v in _band_taps(lo, hi, self.det_rate, self.SB_TAPS)])
            self.bw = float(hz)
            self.if_khz = hz / 1e3

    def set_freq(self, freq):
        with self._lock:
            self.freq = freq
            self.src.set_center_freq(self._lo(freq), 0)
            self._rot_hz = 0.0
            self.rot.set_phase_inc(0.0)
            self._psd, self._q_slow = None, None
            self._tune_cnr = []
            self.agc.core.reset()
            self.nb.core.reset()
            self._blank_until = time.monotonic() + self.TUNE_SETTLE_S

    def set_blankers(self, impulse, noise):
        """impulse: True / False; noise: 'off', 'low', 'med' or 'high'. One blanker does both:
        its line is the noise blanker's (backing off when it patches too much, but never past
        the impulse blanker's), and pulses over the impulse line count as impulses."""
        self.ib_on = bool(impulse)
        self.nb_level = noise if noise in self.NB_K else 'off'
        k = self.NB_K.get(self.nb_level) or (self.IB_K if self.ib_on else None)
        self.nb.core.set_k(k, self.IB_K if self.ib_on else None)

    def blanker_stats(self):
        """(impulses patched a minute, share of the time patched in percent, 0: no stereo)."""
        core = self.nb.core
        return core.big.per_min, (100.0 * core.frac if self.nb_level != 'off' else 0.0), 0.0

    def set_output_gain(self, left, right):
        with self._lock:
            self._out_gain = (max(0.0, float(left)), max(0.0, float(right)))
            self._apply_gain()

    def set_mute(self, muted):
        with self._lock:
            self._muted = bool(muted)
            self._apply_gain()

    def cnr_since_tune(self):
        with self._lock:
            return list(self._tune_cnr)

    def audio_lag(self):
        """(meters, front end) seconds the sound card trails the measurements, or None."""
        if not self._counting():
            return None
        try:
            played = self.audio_sink.nitems_read(0)
            tap = self.up.nitems_written(0)
            front = self.xlate.nitems_written(0) * self.audio_rate / (self.samp_rate / self.DECIM1)
        except Exception:
            return None
        r = float(self.audio_rate)
        return max(0.0, tap - played) / r, max(0.0, front - played) / r

    def monitor(self):
        """Meter readings since the last call (sync AM's modulation in percent), or None."""
        if self.audio_meter is None:
            return None
        return dict(audio=self.audio_meter.read(), mod=self.mod_meter.read())

    def signal_level(self):
        """(passband dBFS, tuner gain dB): no tuner with direct sampling."""
        return self.channel_dbfs, (0.0 if self.direct else self.gain_db)

    def snapshot(self, kind):
        if kind == 'iq':
            return np.asarray(self.snap_ch.level(), dtype=np.complex64)
        if kind in ('pre', 'post'):
            return np.asarray(self.snap_sb.level(), dtype=np.complex64)
        if kind == 'mpx':
            return np.asarray(self.snap_aud.level(), dtype=np.float32)
        return np.zeros(0, np.complex64)

    def readout(self):
        """ADV text for the HF receiver."""
        lo, hi = self.passband()
        src = {'direct_q': 'Q-branch direct sampling', 'native': 'quadrature tuner',
               'upconverter': f'upconverter +{self.UPCONVERTER_HZ / 1e6:.0f} MHz'}[self.am_source]
        if not self.direct:
            src += f', gain {self.gain_db:4.1f} dB ' + ('(AGC)' if self.agc_enabled else '(manual)')
        sync = (f"   carrier {'locked' if self.locked else 'searching'}, offset {self.carrier_hz:+6.1f} Hz"
                if self.mode.startswith('SAM') else '')
        return (f"S/N      {self.cnr_db:5.1f} dB in the passband   level {self.channel_dbfs:6.1f} dBFS\n"
                f"Filter   {self.mode} {self.width:g} Hz: {lo:+.0f} to {hi:+.0f} Hz from the carrier{sync}\n"
                f"AGC      gain {self.agc_gain_db:+5.1f} dB   hang {self.agc.core.hang_s:g} s\n"
                f"Clip     {self.clip_pct:6.3f} %   source: {src}"
                f"   blankers: {describe(self.ib_on, self.nb_level)}")

    def addon_tap(self):
        return None

    # ---------------------------------------------------------------- update
    def _dongle_block(self):
        return self.xlate

    def update_blend(self):
        """Called every ~20 ms by the window: measurements, the AGC's ceiling, sync AM's carrier."""
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
                    print(f"HF measurement: {type(e).__name__}: {e}")

    def _measure(self, now):
        settled = now >= self._blank_until
        # the channel spectrum: its noise floor; on sync AM, the carrier's offset
        ch = self.snap_ch.level()
        if len(ch) == self.CH_N and (self._last_ch is None or ch[0] != self._last_ch) and np.any(np.asarray(ch)):
            self._last_ch = ch[0]
            X = np.abs(np.fft.fftshift(np.fft.fft(np.asarray(ch) * self._hann))) ** 2 / self._hann_wp
            n0 = float(np.percentile(X[self._floor_bins], self.NOISE_PCT)) / self._pct_mean / self.ch_rate
            if n0 > 0 and math.isfinite(n0):
                self._n0_hz = n0 if self._n0_hz is None else 0.8 * self._n0_hz + 0.2 * n0
                noise = math.sqrt(self._n0_hz * self.width)
                self.agc.core.set_max_gain(max_gain_for(noise, self.AGC_REF, self.NOISE_BELOW_DB))
                if not self._ready:                      # the first ceiling: the sound can start
                    self._ready = True
                    self._apply_gain()
            self._psd = X if self._psd is None else 0.7 * self._psd + 0.3 * X
            if self.mode.startswith('SAM'):
                fr, P = self._fr, self._psd
                near = np.where(np.abs(fr) < 400)[0]
                i = near[np.argmax(P[near])]
                a, b, c = (np.log(P[j] + 1e-30) for j in (i - 1, i, i + 1))
                d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
                off = fr[i] + d * (fr[1] - fr[0])
                self.carrier_hz = self._rot_hz + off
                if settled and abs(off) > 0.5:
                    self._rot_hz += 0.5 * off
                    self.rot.set_phase_inc(-2 * math.pi * self._rot_hz / self.ch_rate)
                self.locked = bool(self.pll.lock_detector()) if hasattr(self.pll, 'lock_detector') else True

        # the passband's power: the signal-to-noise ratio and the level for the S-meter
        sb = self.sb_probe.level()
        if sb != self._last_sb:
            self._last_sb = sb
            p = sb / self._win
            self.channel_dbfs = 10 * math.log10(max(p, 1e-15))
            self.agc_gain_db = 20 * math.log10(max(self.agc.core.gain, 1e-12))
            if self._n0_hz and settled:
                n = self._n0_hz * self.width
                q = 10 * math.log10(max(p - n, n * 1e-3) / n)
                q = min(max(q, -10.0), 80.0)
                self._tune_cnr.append(q)
                if len(self._tune_cnr) > 200:
                    del self._tune_cnr[:100]
                self._q_slow = q if self._q_slow is None else self._q_slow + 0.15 * (q - self._q_slow)
                self.cnr_db = self.raw_cnr_db = self.pre_cnr_db = self.spec_snr_db = self._q_slow
