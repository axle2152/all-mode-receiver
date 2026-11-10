#!/usr/bin/env python3
"""
test_crosby.py - does the Crosby add-on play Crosby stereo, and only Crosby stereo?

    python3 addons/crosby/test_crosby.py

Needs GNU Radio, not a dongle, and not the add-on installed (it plugs this folder's copy in
the way an installed one plugs in). Runs the real FM receiver with the add-on in real time
on pretend stations, each a few seconds:

  Crosby stereo, clean      found, CROSBY STEREO, separation at 1 and 10 kHz, the echo
                            canceller stays out
  the same station in mono  not Crosby; plays at the same loudness as the stereo one
                            (the make-up gain for the 50% main channel is right)
  GE-Zenith pilot stereo    the pilot decoder plays; not Crosby
  mono with a 67 kHz SCA    mono: a constant-amplitude subcarrier at 30%, but not at 50 kHz
  Crosby stereo, weak       found, blended part way to mono
  retune away and back      let go at once, found again
  half the deviation        L+R raised to match the subcarrier, so still separated

The pretend Crosby transmitter is crosby_tx.grc's design with the half-sample correction
(README.md, "The transmitter"); without it, separation at 10 kHz is about 28 dB. Both
channels play at 90% at once, the hard case: at 10 kHz the 19-81 kHz subcarrier band (which
both ends use; Carson's rule would want 10-90 kHz) costs some separation, about 40 dB left.
At 1 kHz the limit is how exactly the subcarrier, the reference L+R is balanced to, can be
measured: about 52 dB.
"""
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(os.path.dirname(HERE)), HERE]

try:
    import numpy as np
    from gnuradio import gr, blocks, analog, filter
    from gnuradio.filter import firdes
except ImportError:
    print('GNU Radio is not installed here, so there is nothing to test.')
    sys.exit(0)

TX = 384000                      # the transmitter's composite rate, as in crosby_tx.grc
failed = 0


class Station(gr.hier_block2):
    """Stands in for the dongle: one station on `station_hz`, real time.
    kind: 'crosby' (stereo), 'crosby_mono' (the same station switched to mono), 'gez'
    (pilot stereo), 'mono_sca' (mono with a 67 kHz SCA at 30%). Left gets a 997 Hz tone and
    right a 10007 Hz one: not whole kHz, so no sideband sits on the receiver's DC canceller, and
    not a harmonic of the other, so distortion can't pass for crosstalk.
    cnr_db is the carrier-to-noise ratio in 200 kHz."""

    def __init__(self, rate, kind, station_hz, cnr_db, deviation=75e3):
        gr.hier_block2.__init__(self, 'station', gr.io_signature(0, 0, 0),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.station_hz = self.centre = station_hz
        c = self.connect
        tl = analog.sig_source_f(TX, analog.GR_COS_WAVE, 997.0, 0.9)
        tr = analog.sig_source_f(TX, analog.GR_COS_WAVE, 10007.0, 0.9)
        s, d, mpx = blocks.add_ff(), blocks.sub_ff(), blocks.add_ff()
        c(tl, (s, 0)); c(tr, (s, 1)); c(tl, (d, 0)); c(tr, (d, 1))
        if kind in ('crosby', 'crosby_mono'):
            main_inj, sub_inj = (0.5, 0.5) if kind == 'crosby' else (1.0, 0.0)
            taps = firdes.low_pass(1.0, TX, 31e3, 4e3)
            fm = analog.frequency_modulator_fc(2 * math.pi * 25e3 / TX)
            lo = analog.sig_source_c(TX, analog.GR_COS_WAVE, 50e3, sub_inj)
            mul, re = blocks.multiply_cc(), blocks.complex_to_real()
            c(s, blocks.multiply_const_ff(main_inj / 2),
              blocks.delay(gr.sizeof_float, (len(taps) - 1) // 2), (mpx, 0))
            # [0.5, 0.5]: the modulator's half-sample advance undone (README.md)
            c(d, blocks.multiply_const_ff(0.5), filter.fir_filter_fff(1, [0.5, 0.5]), fm,
              filter.fir_filter_ccf(1, taps), (mul, 0))
            c(lo, (mul, 1)); c(mul, re, (mpx, 1))
        elif kind == 'gez':
            pil = analog.sig_source_f(TX, analog.GR_SIN_WAVE, 19e3, 0.1)
            sc = analog.sig_source_f(TX, analog.GR_SIN_WAVE, 38e3, 1.0)
            m, a3 = blocks.multiply_ff(), blocks.add_ff()
            c(s, blocks.multiply_const_ff(0.45), (a3, 0))
            c(d, blocks.multiply_const_ff(0.45), (m, 0)); c(sc, (m, 1)); c(m, (a3, 1))
            c(pil, (a3, 2)); c(a3, (mpx, 0))
            c(analog.sig_source_f(TX, analog.GR_CONST_WAVE, 0, 0, 0.0), (mpx, 1))
        else:                                       # mono, 70%, and a 67 kHz SCA at 30%
            at = analog.sig_source_f(TX, analog.GR_SIN_WAVE, 3000.0, 1.0)
            fm = analog.frequency_modulator_fc(2 * math.pi * 7.5e3 / TX)
            lo = analog.sig_source_c(TX, analog.GR_COS_WAVE, 67e3, 0.3)
            mul, re = blocks.multiply_cc(), blocks.complex_to_real()
            c(s, blocks.multiply_const_ff(0.35), (mpx, 0))
            c(d, blocks.null_sink(gr.sizeof_float))
            c(at, fm, (mul, 0)); c(lo, (mul, 1)); c(mul, re, (mpx, 1))
        up = filter.rational_resampler_fff(25, 4) if rate == 2_400_000 else \
            filter.rational_resampler_fff(int(rate // 1000), TX // 1000)
        fm = analog.frequency_modulator_fc(2 * math.pi * deviation / rate)
        self.lo = analog.sig_source_c(rate, analog.GR_COS_WAVE, 0.0, 1.0)
        amp = 0.3
        noise = analog.noise_source_c(analog.GR_GAUSSIAN,
                                      math.sqrt(amp ** 2 * 10 ** (-cnr_db / 10) * rate / 200e3), 7)
        mix, add = blocks.multiply_cc(), blocks.add_cc()
        c(mpx, up, fm, (mix, 0)); c(self.lo, (mix, 1))
        c(mix, blocks.multiply_const_cc(amp), (add, 0)); c(noise, (add, 1))
        c(add, blocks.throttle(gr.sizeof_gr_complex, rate), self)

    def set_center_freq(self, f, *a):
        self.centre = f
        self.lo.set_frequency(self.station_hz - self.centre)
        return f

    def get_gain_range(self, *a):
        raise RuntimeError('no gain table: the receiver uses its own')

    def __getattr__(self, name):                    # the other osmosdr settings: accepted
        if name.startswith('__'):
            raise AttributeError(name)
        return lambda *a, **k: 0


class Speakers(gr.sync_block):
    """Stands in for the sound card: keeps what it's given."""

    def __init__(self):
        gr.sync_block.__init__(self, 'speakers', [np.float32, np.float32], None)
        self.blocks = []

    def work(self, ins, outs):
        self.blocks.append((ins[0].copy(), ins[1].copy()))      # left and right together
        return len(ins[0])

    def last(self, secs, rate):
        n, got = int(secs * rate), list(self.blocks)
        return (np.concatenate([b[0] for b in got])[-n:], np.concatenate([b[1] for b in got])[-n:])


def receiver(kind, cnr_db, deviation=75e3):
    """The real FM receiver, the add-on plugged in, a pretend station and speakers."""
    import radio.addons
    from crosby_stereo import Addon
    from radio.fm.receiver import FMStereoReceiver

    class EntryPoint:                               # as pip install would declare it
        name = 'crosby'

        def load(self):
            return Addon
    radio.addons._entry_points = lambda: [EntryPoint()]

    class Rx(FMStereoReceiver):
        def _make_source(self, args):
            return Station(self.samp_rate, kind, 90.5e6, cnr_db, deviation)

        def _make_audio_sink(self, rate, device):
            return Speakers()
    tb = Rx(freq=90.5e6, agc=False)
    tb.set_output_gain(1.0, 1.0)
    tb.set_tone(0, 0)
    tb.set_mute(False)
    return tb


def play(tb, secs, until=None):
    """Drive the receiver as the tuner does for secs; returns when `until` first held, or None."""
    t0, first = time.monotonic(), None
    while time.monotonic() - t0 < secs:
        tb.update_blend()
        if until is not None and first is None and until():
            first = time.monotonic() - t0
        time.sleep(0.02)
    return first


def tone(x, f, rate=48000):
    n, w = np.arange(len(x)), np.hanning(len(x))
    return 2 * abs(np.sum(x * w * np.exp(-2j * np.pi * f * n / rate))) / np.sum(w)


def check(name, ok, detail):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:44s} {detail}")


def station(kind, cnr_db=40.0, secs=4.0):
    tb = receiver(kind, cnr_db)
    tb.start()
    found = play(tb, secs, lambda: tb.addon_stereo == 'CROSBY')
    return tb, found


def main():
    print('GNU Radio', gr.version())
    db = lambda a, b: 20 * math.log10(max(a, 1e-12) / max(b, 1e-12))

    print('Crosby stereo, clean')
    tb, found = station('crosby')
    a = tb.addons[0].obj if tb.addons else None
    L, R = tb.audio_sink.last(1.5, tb.audio_rate)
    check('the add-on started and joined the matrix', a is not None and tb._stereo is not None,
          f"{len(tb.addons)} add-on(s)")
    check('found within 1.5 s', found is not None and found < 1.5,
          'not found' if found is None else f"after {found:.2f} s")
    check('CROSBY STEREO, full stereo', tb.addon_stereo == 'CROSBY' and tb.stereo_indicator
          and tb.current_blend > 0.99, f"blend {tb.current_blend:.2f}")
    check('subcarrier reads 50% at 50 kHz', a is not None and abs(a.injection_pct() - 50) < 1.5
          and abs(a.offset_hz) < 500, f"{a.injection_pct():.1f}% at {50 + a.offset_hz / 1e3:.2f} kHz")
    g = 20 * math.log10(a.main_gain)
    check('L+R raised 6 dB', abs(g - 6.02) < 0.3 and abs(tb._stereo['gain_db'] - g) < 0.1,
          f"{tb._stereo['gain_db']:.2f} dB")
    s1, s10 = db(tone(L, 997.0), tone(R, 997.0)), db(tone(R, 10007.0), tone(L, 10007.0))
    check('separation at 1 kHz', s1 >= 45, f"{s1:.1f} dB (45 or more)")
    check('separation at 10 kHz', s10 >= 35, f"{s10:.1f} dB (35 or more)")
    check('the echo canceller stays out', not tb.eq_active and not tb.eq_gate,
          f"active {tb.eq_active}, gate {tb.eq_gate}")
    stereo_sum = tone(L + R, 997.0)
    tb.set_freq(90.7e6)                             # away: nothing there
    check('retuned away: let go at once', tb.addon_stereo is None and tb._stereo['gain_db'] == 0.0
          and tb._stereo['route'] == 'own', f"main channel {tb._stereo['gain_db']:+.1f} dB")
    play(tb, 1.5)
    check('nothing there: not Crosby', not a.found and tb.addon_stereo is None, f"found {a.found}")
    tb.set_freq(90.5e6)
    back = play(tb, 2.0, lambda: tb.addon_stereo == 'CROSBY')
    check('tuned back: found again', back is not None and back < 1.5,
          'not found' if back is None else f"after {back:.2f} s")
    tb.stop()
    tb.wait()

    print('The same station switched to mono')
    tb, found = station('crosby_mono')
    a = tb.addons[0].obj
    L, R = tb.audio_sink.last(1.5, tb.audio_rate)
    check('not Crosby, no stereo', found is None and not tb.stereo_indicator and not a.found,
          f"subcarrier {a.injection_pct():.1f}%")
    lv = db(stereo_sum, tone(L + R, 997.0))
    check('stereo plays as loud as mono (make-up gain)', abs(lv) < 0.5, f"{lv:+.2f} dB")
    tb.stop()
    tb.wait()

    print('GE-Zenith pilot stereo')
    tb, found = station('gez')
    a = tb.addons[0].obj
    check('the pilot decoder plays', tb.pilot_present and tb.stereo_indicator
          and tb._stereo['route'] == 'own', f"pilot {tb.pilot_level:.3f}")
    check('not Crosby', found is None and not a.found and tb.addon_stereo is None,
          f"offset {a.offset_hz / 1e3:+.1f} kHz")
    tb.stop()
    tb.wait()

    print('Mono with a 67 kHz SCA at 30%')
    tb, found = station('mono_sca')
    a = tb.addons[0].obj
    check('mono, not Crosby', found is None and not tb.stereo_indicator and not a.found,
          f"subcarrier {a.injection_pct():.0f}%, C/N {a.circle_db:.1f} dB, "
          f"offset {a.offset_hz / 1e3:+.1f} kHz")
    tb.stop()
    tb.wait()

    print('A Crosby station at half its deviation (39 kHz)')
    tb = receiver('crosby', 40.0, deviation=39e3)
    tb.start()
    found = play(tb, 5.0, lambda: tb.addon_stereo == 'CROSBY')
    a = tb.addons[0].obj
    L, R = tb.audio_sink.last(1.5, tb.audio_rate)
    check('found; the subcarrier reads about 26%', found is not None and abs(a.injection_pct() - 26) < 2,
          f"{a.injection_pct():.1f}%")
    check('L+R raised to match it (about 11.7 dB)', abs(tb._stereo['gain_db'] - 11.7) < 0.6,
          f"{tb._stereo['gain_db']:.1f} dB")
    s1 = db(tone(L, 997.0), tone(R, 997.0))
    check('separation at 1 kHz still', s1 >= 35, f"{s1:.1f} dB (35 or more; 10 dB unbalanced)")
    lv = db(stereo_sum, tone(L + R, 997.0))
    check('as loud as the station at full deviation', abs(lv) < 0.5, f"{lv:+.2f} dB")
    tb.stop()
    tb.wait()

    print('Crosby stereo, weak (12 dB)')
    tb, found = station('crosby', cnr_db=12.0)
    check('found', found is not None, 'not found' if found is None else f"after {found:.2f} s")
    check('blended part way to mono', 0.05 < tb.current_blend < 0.95,
          f"signal {tb.cnr_db:.1f} dB, blend {tb.current_blend:.2f}")
    tb.stop()
    tb.wait()

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
