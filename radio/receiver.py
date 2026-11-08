"""
radio/receiver.py - what every receiver shares
==============================================
Receiver is the base of the AM and FM receivers (and the modes still to come):

  dongle input   _make_source() and _make_audio_sink(), the one place the dongle and the
                 sound card are opened, so tests (and later recordings) can stand in
  watchdog       no samples from the dongle for STALL_S (START_S just after start()) raises
                 from update_blend(), so the tuner can reconnect. Samples are counted at the
                 receiver's first block, so a steady signal can't look like a lost dongle.
  tone controls  bass and treble first-order shelves at the audio rate, then just enough
                 headroom that a full boost can't clip
  add-ons        decoders installed separately (radio/addons.py, docs/ADDONS.md): a
                 receiver that offers addon_tap() starts them once its flowgraph is built

A receiver says which band it is (BAND) and what it can do (FEATURES). The window shows
the controls for the features a receiver has, and calls nothing else on it.
"""
import math
import time

from gnuradio import gr, blocks, filter, audio
import osmosdr

from . import addons as _addons


class Receiver(gr.top_block):
    BAND = None
    # What this receiver can do, and what each feature promises:
    #   'mono'          set_force_mono(on)
    #   'fm_helpers'    set_enhancements(blend, hicut), set_dip_hicut(on), set_noise_reduction(on)
    #   'echo'          the echo canceller: set_eq_mode(mode), eq_taps()
    #   'fm_bandwidth'  broadcast FM's filter: set_if_mode('auto' | 'wide' | 'narrow')
    #   'am_width'      set_auto_ceiling(hz), set_if_mode('auto' | 'fixed'), set_wide(on)
    #   'am_stereo'     set_stereo_system('auto' | 'cquam' | 'kahn')
    #   'nrsc'          set_nrsc_deemphasis(on)
    #   'blankers'      set_blankers(impulse, noise), blanker_stats()
    #   'rds'           station data in rds_ps, rds_rt, rds_pi and the other rds_ fields
    FEATURES = frozenset()

    # What the window may call on any receiver: set_freq, set_output_gain, set_mute, set_tone,
    # set_agc, set_manual_gain, update_blend, snapshot, cnr_since_tune, audio_lag, monitor,
    # and the gr.top_block start / stop / wait.

    # ---- watchdog ----
    STALL_S          = 2.0    # no samples for this long = dongle gone (raises, so a GUI can
                              # reconnect; e.g. after the laptop sleeps)
    START_S          = 8.0    # first samples may take this long after start()

    # ---- tone controls ----
    TONE_BASS_HZ     = 120.0  # first-order shelf corners
    TONE_TREBLE_HZ   = 4000.0
    TONE_RANGE_DB    = 12.0
    HEADROOM_DB      = 12.0   # fixed attenuation so a full boost can't clip (TONE_MAKEUP off)
    TONE_MAKEUP      = True   # attenuate only by the biggest boost (+ TONE_SAFETY_DB):
                              # flat tone plays at full level, +6 dB bass gives back 6 dB
    TONE_SAFETY_DB   = 1.0

    def __init__(self, name):
        gr.top_block.__init__(self, name)
        self._wb_seen = time.monotonic()      # last time samples were seen arriving
        self._got_samples = False             # any since start()
        self._n_in = 0                        # samples taken from the dongle, at last look
        self.addons = []                      # add-ons running on this receiver
        self.station = {}                     # what they last reported (radio/addons.py)

    def has(self, feature):
        return feature in self.FEATURES

    # ------------------------------------------------- the rest of the interface
    def audio_lag(self):
        """(meters, front end) seconds the sound card trails the measurements, or None."""
        return None

    def monitor(self):
        """Meter readings since the last call (see radio/meters.py), or None."""
        return None

    # -------------------------------------------------- hardware (tests replace these)
    def _make_source(self, args):
        return osmosdr.source(args=args)

    def _make_audio_sink(self, rate, device):
        return audio.sink(rate, device, True)

    # ---------------------------------------------------------------- watchdog
    def _dongle_block(self):
        """The first block after the dongle, where samples are counted."""
        raise NotImplementedError

    def _samples_in(self):
        """Samples taken from the dongle so far, or None."""
        try:
            return int(self._dongle_block().nitems_read(0))
        except Exception:
            return None

    def _flow(self):
        """Samples moved so far from the dongle and to the sound card, for stall reports."""
        try:
            return (f"from dongle {self._dongle_block().nitems_read(0)} samples, "
                    f"to sound card {self.audio_sink.nitems_read(0)} samples")
        except Exception as e:
            return f"counters unavailable ({e})"

    def start(self, *args, **kwargs):
        """Start the flowgraph; the stall watchdog counts from here, not from construction
        (opening the dongle can take seconds, longer on Linux than on Windows)."""
        gr.top_block.start(self, *args, **kwargs)
        with self._lock:
            self._wb_seen = time.monotonic()
            self._got_samples = False
            self._n_in = 0

    def _watchdog(self, now, fresh=False):
        """Call from update_blend(). fresh: the receiver's own snapshot shows new samples.
        Raises RuntimeError once the dongle has been silent too long."""
        n_in = self._samples_in()                # counted, so a steady signal can't look stalled
        if fresh or (n_in is not None and n_in != self._n_in):
            if n_in is not None:
                self._n_in = n_in
            self._wb_seen = now
            self._got_samples = True
        elif now - self._wb_seen > (self.STALL_S if self._got_samples else self.START_S):
            raise RuntimeError(f"no samples from the dongle for {now - self._wb_seen:.1f} s; "
                               f"{self._flow()}")

    # ----------------------------------------------------------- tone controls
    # x + (gb-1)*LP(x) + (gt-1)*(x - LP'(x)) are exact first-order shelves; only gains
    # change, so nothing clicks.
    def _shelf_a(self, hz):
        return 1 - math.exp(-2 * math.pi * hz / self.audio_rate)

    def _make_tone(self, ch, headroom):
        """The tone and headroom blocks for one channel ('l' or 'r'), flat."""
        ab, at = self._shelf_a(self.TONE_BASS_HZ), self._shelf_a(self.TONE_TREBLE_HZ)
        setattr(self, f"tone_bass_lp_{ch}", filter.single_pole_iir_filter_ff(ab))
        setattr(self, f"tone_bass_k_{ch}", blocks.multiply_const_ff(0.0))
        setattr(self, f"tone_tre_lp_{ch}", filter.single_pole_iir_filter_ff(at))
        setattr(self, f"tone_tre_hp_{ch}", blocks.sub_ff(1))
        setattr(self, f"tone_tre_k_{ch}", blocks.multiply_const_ff(0.0))
        setattr(self, f"tone_sum_{ch}", blocks.add_ff(1))
        setattr(self, f"headroom_{ch}", blocks.multiply_const_ff(headroom))

    def _connect_tone(self, src, ch, idx):
        """src -> tone controls -> headroom -> sound card input idx."""
        c = self.connect
        g = lambda n: getattr(self, f"{n}_{ch}")
        c(src, (g("tone_sum"), 0))
        c(src, g("tone_bass_lp"), g("tone_bass_k"), (g("tone_sum"), 1))
        c(src, (g("tone_tre_hp"), 0))
        c(src, g("tone_tre_lp"), (g("tone_tre_hp"), 1))
        c(g("tone_tre_hp"), g("tone_tre_k"), (g("tone_sum"), 2))
        c(g("tone_sum"), g("headroom"), (self.audio_sink, idx))

    def set_tone(self, bass_db, treble_db):
        """Bass / treble in dB (+/-TONE_RANGE_DB). For a cut the shelf corner
        moves (bass up by 1/g, treble down by g) so cut mirrors boost exactly;
        the one-pole filters keep their state, so nothing clicks."""
        r = self.TONE_RANGE_DB
        gb = 10 ** (min(max(bass_db, -r), r) / 20)
        gt = 10 ** (min(max(treble_db, -r), r) / 20)
        fb = self.TONE_BASS_HZ / (gb if gb < 1 else 1.0)
        ft = self.TONE_TREBLE_HZ * (gt if gt < 1 else 1.0)
        ab, at = self._shelf_a(fb), self._shelf_a(ft)
        hr = 10 ** (-self._tone_atten_db(bass_db, treble_db) / 20)
        for ch in ("l", "r"):
            getattr(self, f"tone_bass_lp_{ch}").set_taps(ab)
            getattr(self, f"tone_bass_k_{ch}").set_k(gb - 1)
            getattr(self, f"tone_tre_lp_{ch}").set_taps(at)
            getattr(self, f"tone_tre_k_{ch}").set_k(gt - 1)
            getattr(self, f"headroom_{ch}").set_k(hr)

    def _tone_atten_db(self, bass_db, treble_db):
        """Attenuation after the tone controls. With TONE_MAKEUP, just enough
        for the biggest boost to fit (plus a little), so the level stays put."""
        if not self.TONE_MAKEUP:
            return self.HEADROOM_DB
        r = self.TONE_RANGE_DB
        return max(0.0, min(bass_db, r), min(treble_db, r)) + self.TONE_SAFETY_DB

    # ----------------------------------------------------------------- add-ons
    def addon_tap(self):
        """(block, port) an add-on listens to, or None if this receiver takes no add-ons."""
        return None

    def _attach_addons(self):
        """Start the installed add-ons for this band. Call once the flowgraph is built."""
        if self.addon_tap() is not None:
            self.addons = _addons.attach(self)

    def _reset_addons(self):
        """On a retune: the add-ons start over, and the old station's data is cleared."""
        if not self.addons:
            return
        for a in self.addons:
            a.reset()
        self.station = {}
        self.rds_ps, self.rds_rt, self.rds_pi, self.rds_call, self.rds_pty = '', '', None, None, None
        self.rds_synced = False

    def _update_addons(self, now):
        """From update_blend(): what the add-ons report fills the station data fields."""
        for a in self.addons:
            data = a.update(now)
            if data:
                self.station.update(data)
                for key, attr in _addons.STATION_FIELDS.items():
                    if key in data:
                        setattr(self, attr, data[key])

    def _addons_readout(self):
        return ''.join('\n' + a.readout() for a in self.addons if a.readout())
