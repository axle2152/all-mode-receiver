#!/usr/bin/env python3
"""
crosby_check.py - why isn't the radio playing Crosby stereo? No window, no sound.

    python3 addons/crosby/crosby_check.py --freq 90.5e6
    python3 addons/crosby/crosby_check.py --freq 90.5e6 --record 3
    python3 addons/crosby/crosby_check.py --replay crosby_check.cs8

Close the tuner first: this opens the dongle itself. It runs the FM receiver for 10 seconds
the way the tuner does, with the Crosby add-on, then:

  1. says whether the add-on is installed in the Python you ran this with (if it isn't, the
     radio plays a Crosby station in mono, 6 dB quiet, and that's the whole story)
  2. prints every second what the receiver and the add-on measure
  3. says in plain words what it found, and what to check

Paste what it prints into the chat. --record 3 also saves the first 3 seconds of the radio
signal, exactly as the dongle sent it, to crosby_check.cs8 (about 14 MB) beside this file,
so it can be played back through the receiver elsewhere (--replay) without the dongle.
"""
import argparse
import inspect
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)


def addon_class():
    """The Crosby add-on as the tuner would find it, and what to say about that."""
    import radio.addons
    for ep in radio.addons._entry_points():
        if ep.name == 'crosby':
            try:
                cls = ep.load()
            except Exception as e:
                return None, (f"installed, but it won't load: {type(e).__name__}: {e}")
            return cls, f"installed, from {os.path.dirname(inspect.getfile(cls))}"
    sys.path.insert(1, HERE)                     # use this folder's copy for the check
    from crosby_stereo import Addon

    class EntryPoint:
        name = 'crosby'

        def load(self):
            return Addon
    found = radio.addons._entry_points()
    radio.addons._entry_points = lambda: found + [EntryPoint()]
    return None, 'NOT INSTALLED in this Python'


def make_receiver(a):
    from gnuradio import gr, blocks
    from radio.fm.receiver import FMStereoReceiver

    class Rx(FMStereoReceiver):
        def _make_audio_sink(self, rate, device):           # no sound card needed
            return blocks.null_sink(gr.sizeof_float)

        if a.replay:
            def _make_source(self, args):
                return Replay(a.replay, self.samp_rate)
        elif a.pretend:
            def _make_source(self, args):
                sys.path.insert(1, HERE)
                from test_crosby import Station
                return Station(self.samp_rate, a.pretend, a.freq, 40.0)

    rec = os.path.join(HERE, 'crosby_check.cs8') if a.record else None
    return Rx(freq=a.freq, rf_gain=a.gain if a.gain is not None else 8.7, agc=a.gain is None,
              device_args=a.device_args, record_iq=rec), rec


def Replay(path, rate):
    """A saved crosby_check.cs8 in place of the dongle, in real time, over and over."""
    from gnuradio import gr, blocks

    class _Replay(gr.hier_block2):
        def __init__(self):
            gr.hier_block2.__init__(self, 'replay', gr.io_signature(0, 0, 0),
                                    gr.io_signature(1, 1, gr.sizeof_gr_complex))
            src = blocks.file_source(gr.sizeof_char, path, True)
            self.connect(src, blocks.interleaved_char_to_complex(False, 128.0),
                         blocks.throttle(gr.sizeof_gr_complex, rate), self)

        def get_gain_range(self, *a):
            raise RuntimeError('a recording has no gain table')

        def __getattr__(self, name):                         # tuning, gain: nothing to do
            if name.startswith('__'):
                raise AttributeError(name)
            return lambda *a, **k: 0
    return _Replay()


def composite(tb):
    """Peak deviation (kHz) in the latest composite snapshot."""
    import numpy as np
    x = np.asarray(tb.snapshot('mpx'))
    return float(np.max(np.abs(x))) / tb.MPX_FULL * 75.0 if len(x) and np.any(x) else 0.0


def median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('--freq', type=float, default=90.5e6, help='the Crosby station (Hz)')
    ap.add_argument('--secs', type=int, default=10)
    ap.add_argument('--gain', type=float, default=None, help='fixed tuner gain in dB (default: AGC)')
    ap.add_argument('--device-args', default='numchan=1 rtl=0')
    ap.add_argument('--record', type=float, default=0, metavar='SECS',
                    help='also save this many seconds of the radio signal (4.8 MB a second)')
    ap.add_argument('--replay', default=None, help='a saved .cs8 in place of the dongle')
    ap.add_argument('--pretend', default=None, help=argparse.SUPPRESS)   # test_crosby.Station kind
    a = ap.parse_args()

    import numpy as np
    from gnuradio import gr
    print(f"Python {sys.version.split()[0]} ({sys.executable}) | GNU Radio {gr.version()} | "
          f"numpy {np.__version__}")
    cls, said = addon_class()
    print(f"Crosby add-on: {said}")
    if cls is None and 'NOT INSTALLED' in said:
        print("  (checking with this folder's copy instead)")
    try:
        tb, rec = make_receiver(a)
    except Exception as e:
        print(f"\nThe FM receiver wouldn't start: {type(e).__name__}: {e}")
        print("Is the tuner still running, or the dongle unplugged?")
        return 1
    addon = next((x for x in tb.addons if x.name == 'crosby'), None)
    if addon is None:
        print("The add-on didn't start (the line above says why). Measuring without it.")
    src = 'replaying ' + a.replay if a.replay else f'{a.freq / 1e6:.3f} MHz'
    print(f"\nListening to {src} for {a.secs} s"
          + (f", saving the first {a.record:g} s to {rec}" if rec else '') + '\n')
    print('   s  signal  clip  gain  pilot    dev  subcarrier       C/N   Crosby')
    tb.start()
    t0, rows, errors, dev = time.monotonic(), [], [], 0.0
    closed = rec is None
    for sec in range(1, a.secs + 1):
        while time.monotonic() < t0 + sec:
            try:
                tb.update_blend()
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")
            dev = max(dev, composite(tb))
            if not closed and time.monotonic() - t0 >= a.record:
                tb.rec_sink.close()
                closed = True
            time.sleep(0.02)
        o = addon.obj if addon is not None and addon.alive else None
        r = dict(q=tb.cnr_db, clip=tb.clip_pct, gain=tb.gain_db, pilot=tb.pilot_level,
                 has_pilot=tb.pilot_present, dev=dev, found=bool(o and o.found),
                 inj=o.injection_pct() if o else None,
                 centre=(o.SUB_HZ + o.offset_hz) / 1e3 if o and o.offset_hz is not None else None,
                 cn=o.circle_db if o else None, why=o.why_not() if o else 'add-on not running',
                 blend=tb.current_blend, st=tb.addon_stereo)
        rows.append(r)
        dev = 0.0
        sub = (f"{r['inj']:3.0f}% {r['centre']:5.1f} kHz" if r['centre'] is not None else '     --       ')
        cn = f"{r['cn']:5.1f} dB" if r['cn'] is not None else '   --   '
        state = (f"STEREO, blend {r['blend']:.2f}" if r['st'] else r['why'])
        print(f"  {sec:2d}  {r['q']:4.1f} dB {r['clip']:5.2f}% {r['gain']:4.1f}  "
              f"{r['pilot']:.3f}{'*' if r['has_pilot'] else ' '} {r['dev']:3.0f} kHz  {sub}  {cn}  {state}",
              flush=True)
    tb.stop()
    tb.wait()
    if errors:
        print(f"\n{len(errors)} errors from the receiver, the first: {errors[0]}")
    print("\n(signal = signal quality; pilot = 19 kHz pilot, * = heard; dev = peak deviation;\n"
          " subcarrier = its share of full deviation and where it's centred; C/N = how steady it is)")

    # ---- what it means: judged on the second half, after settling ----
    late = rows[len(rows) // 2:] or rows
    print('\nWhat it found:')
    if addon is None or cls is None and 'NOT INSTALLED' not in said:
        print(f"- The add-on isn't running ({said}).")
    if 'NOT INSTALLED' in said:
        print("- The add-on isn't installed in this Python, so the tuner runs without it: a Crosby\n"
              "  station plays in mono, 6 dB quieter than other stations. Install it with\n"
              f"      \"{sys.executable}\" -m pip install -e \"{HERE}\"\n"
              "  (on Ubuntu add --user, and on 23.04 or newer --break-system-packages), then\n"
              "  restart the tuner. The rest below is with this folder's copy.")
    found = [r['found'] for r in late]
    q = median([r['q'] for r in late])
    pil = sum(r['has_pilot'] for r in late) / len(late)
    inj = median([r['inj'] for r in late if r['inj'] is not None])
    cen = median([r['centre'] for r in late if r['centre'] is not None])
    cn = median([r['cn'] for r in late if r['cn'] is not None])
    devmax = max(r['dev'] for r in late)
    clip = max(r['clip'] for r in late)
    if all(found):
        print(f"- Crosby stereo found and playing: subcarrier {inj:.0f}% at {cen:.1f} kHz, "
              f"C/N {cn:.1f} dB.")
        b = median([r['blend'] for r in late])
        if b < 0.99:
            print(f"- Blended {100 * (1 - b):.0f}% toward mono: signal quality {q:.1f} dB "
                  f"(full stereo from 15 dB, mono by 10).")
        print("- If the stereo still sounds wrong (left and right leaking into each other), check\n"
              "  crosby_tx.grc has the [0.5, 0.5] filter before sub_fm (the add-on's README).")
    elif any(found):
        print(f"- Crosby stereo comes and goes ({sum(found)} of the last {len(found)} seconds). "
              "The lines above show which test drops out.")
    elif pil > 0.5:
        print(f"- The receiver hears a 19 kHz stereo pilot ({100 * median([r['pilot'] for r in late]) / 1.8:.1f}%"
              " of full deviation), so it plays\n  ordinary stereo and leaves Crosby alone, as it should. "
              "Crosby has no pilot: something in\n  the transmitter's audio carries a 19 kHz tone. "
              "Stereo Tool's own stereo encoder or pilot\n  left on is the usual one: it should send "
              "plain left and right.")
    elif q is not None and q < 10:
        print(f"- Signal quality is only {q:.1f} dB, too noisy to judge. Bring the antenna or the\n"
              "  transmitter closer, or raise the gain (--gain).")
    elif inj is None or inj < 5:
        print("- No subcarrier at all: the transmitter is sending mono. In crosby_tx.grc, is\n"
              "  'Stereo (Crosby subcarrier on)' ticked?")
    elif inj < 18:
        print(f"- The subcarrier is there but weak: {inj:.0f}% of full deviation. Crosby's is 50% and\n"
              f"  the add-on wants at least 18%. The whole station peaks at {devmax:.0f} kHz here; at full\n"
              "  level Crosby stereo should reach about 75 kHz. Check sub_inj (0.5) in crosby_tx.grc\n"
              "  and that nothing after mpx_sum scales the composite down.")
    elif cen is not None and abs(cen - 50.0) > 3:
        print(f"- There is a subcarrier, but centred at {cen:.1f} kHz instead of 50. Check sub_freq\n"
              "  in crosby_tx.grc, and that its rates match what the hardware runs at.")
    elif cn is not None and cn < 6:
        print(f"- There's energy around 50 kHz ({inj:.0f}%), but not a steady FM subcarrier (C/N\n"
              f"  {cn:.1f} dB; a clean one reads 15 to 30). Overload (clip {clip:.2f}%), noise or echoes.")
    else:
        print("- Every test passes now but it hasn't held for long enough yet. Try --secs 20.")
    if devmax > 90:
        print(f"- The station peaks at {devmax:.0f} kHz: over 75 kHz (100%). Turn the audio down.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
