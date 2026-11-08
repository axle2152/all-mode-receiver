#!/usr/bin/env python3
"""
test_gnuradio.py - do the receivers really run in GNU Radio? Needs GNU Radio, not a dongle.

    python3 tests/test_gnuradio.py

Runs the real AM and FM receivers in your GNU Radio for 6 seconds each, with a pretend
dongle (a test station: carrier, a 1 kHz tone and noise, at the frequency tuned) and a
silent sound card, then checks samples flow at the right rates, the receivers' updates run
without errors, and what they measure fits the test station. If GNU Radio isn't installed,
it says so and stops.
"""
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

try:
    from gnuradio import gr, blocks, analog
except ImportError:
    print('GNU Radio is not installed here, so there is nothing to test.')
    sys.exit(0)

RUN_S = 6.0
failed = 0


class FakeDongle(gr.hier_block2):
    """Stands in for osmosdr.source: a station on `station_hz`, AM or FM, plus noise, at the
    receiver's sample rate and in real time. Retuning moves the station in the band."""

    def __init__(self, rate, mode, station_hz, cnr_db):
        gr.hier_block2.__init__(self, 'fake_dongle', gr.io_signature(0, 0, 0),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.station_hz = self.centre = station_hz
        if mode == 'AM':                       # 1 + 0.5 sin(1 kHz)
            tone = analog.sig_source_f(rate, analog.GR_SIN_WAVE, 1000.0, 0.5)
            one = analog.sig_source_f(rate, analog.GR_CONST_WAVE, 0, 0, 1.0)
            env, base = blocks.add_ff(), blocks.float_to_complex()
            self.connect(tone, (env, 0))
            self.connect(one, (env, 1))
            self.connect(env, base)
            amp = 0.1
        else:                                  # 1 kHz tone at 50 kHz deviation, mono
            tone = analog.sig_source_f(rate, analog.GR_SIN_WAVE, 1000.0, 1.0)
            base = analog.frequency_modulator_fc(2 * math.pi * 50e3 / rate)
            self.connect(tone, base)
            amp = 0.3
        self.lo = analog.sig_source_c(rate, analog.GR_COS_WAVE, 0.0, 1.0)
        mix, scale, add = blocks.multiply_cc(), blocks.multiply_const_cc(amp), blocks.add_cc()
        n0 = amp ** 2 * 10 ** (-cnr_db / 10) / 1e4 * rate      # noise for this C/N in 10 kHz
        noise = analog.noise_source_c(analog.GR_GAUSSIAN, math.sqrt(n0), 7)
        thr = blocks.throttle(gr.sizeof_gr_complex, rate)
        self.connect(base, (mix, 0))
        self.connect(self.lo, (mix, 1))
        self.connect(mix, scale, (add, 0))
        self.connect(noise, (add, 1))
        self.connect(add, thr, self)

    def set_center_freq(self, f, *a):
        self.centre = f
        self.lo.set_frequency(self.station_hz - self.centre)
        return f

    def get_gain_range(self, *a):
        raise RuntimeError('no gain table: the receiver uses its own')

    def __getattr__(self, name):               # the other osmosdr settings: accepted
        if name.startswith('__'):
            raise AttributeError(name)
        return lambda *a, **k: 0


def receiver(band, station_hz, cnr_db=40.0):
    """The real receiver class, with the pretend dongle and a silent sound card."""
    from radio.am import AMReceiver
    from radio.fm.receiver import FMStereoReceiver

    class Rx(AMReceiver if band == 'AM' else FMStereoReceiver):
        def _make_source(self, args):
            return FakeDongle(self.samp_rate, band, station_hz, cnr_db)

        def _make_audio_sink(self, rate, device):
            return blocks.null_sink(gr.sizeof_float)
    return Rx


def check(name, ok, detail):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:40s} {detail}")


def run(band, freq):
    tb = receiver(band, freq)(freq=freq, agc=False)
    tb.set_output_gain(0.5, 0.5)
    tb.set_tone(0, 0)
    tb.set_mute(False)
    if tb.has('am_width'):
        tb.set_auto_ceiling(8000)
        tb.set_if_mode('auto')
        tb.set_wide(False)
    tb.start()
    t0, errors = time.monotonic(), []
    while time.monotonic() - t0 < RUN_S:
        try:
            tb.update_blend()
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
        time.sleep(0.02)
    secs = time.monotonic() - t0
    rate_in, rate_out = tb._samples_in() / secs, tb.audio_sink.nitems_read(0) / secs
    mon = tb.monitor()
    print(f"{band} receiver, {freq / 1e6:g} MHz")
    check('samples from the dongle', abs(rate_in / tb.samp_rate - 1) < 0.1,
          f"{rate_in / 1e3:.0f}k a second (expected {tb.samp_rate / 1e3:.0f}k)")
    check('samples to the sound card', abs(rate_out / tb.audio_rate - 1) < 0.1,
          f"{rate_out / 1e3:.1f}k a second (expected {tb.audio_rate / 1e3:.0f}k)")
    check('updates run without errors', not errors, errors[0] if errors else f"{int(secs / 0.02)} updates")
    check('meters see the audio', bool(mon and mon['audio']['blocks']),
          f"{len(mon['audio']['blocks']) if mon else 0} loudness blocks")
    if band == 'AM':
        check('carrier found and locked', tb.locked and abs(tb.carrier_hz) < 5,
              f"offset {tb.carrier_hz:+.1f} Hz, locked {tb.locked}")
        check('C/N reads what was sent (40 dB)', 38 <= tb.cnr_db <= 42, f"{tb.cnr_db:.1f} dB")
        check('filter opens to the chosen 8 kHz', abs(tb.bw - 8000) < 300, f"{tb.bw / 1e3:.1f} kHz")
        check('no stereo pilot on a mono station', not tb.pilot_on, f"pilot {tb.pilot_on}")
    else:
        check('signal quality reads clean', tb.cnr_db > 15, f"{tb.cnr_db:.1f} dB")
        check('no stereo pilot on a mono station', not tb.stereo_indicator, f"pilot {tb.pilot_level:.3f}")
    tb.stop()
    tb.wait()


def main():
    print('GNU Radio', gr.version())
    run('AM', 1080e3)
    run('FM', 90.5e6)
    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
