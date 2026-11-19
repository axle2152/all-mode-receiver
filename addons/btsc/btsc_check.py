#!/usr/bin/env python3
"""
btsc_check.py - why isn't the radio playing BTSC (TV) stereo? No window, no sound.

    python3 addons/btsc/btsc_check.py                     # TV channel 6's sound, 87.75 MHz
    python3 addons/btsc/btsc_check.py --freq 87.7e6 --record 3
    python3 addons/btsc/btsc_check.py --replay btsc_check.cs8

Close the tuner first: this opens the dongle itself. It runs the FM receiver for 10 seconds
the way the tuner does, with the BTSC add-on, then:

  1. says whether the add-on is installed in the Python you ran this with (if it isn't, the
     radio plays a TV station in mono, and that's the whole story)
  2. prints every second what the receiver and the add-on measure
  3. says in plain words what it found, and what to check

Paste what it prints into the chat. --record 3 also saves the first 3 seconds of the radio
signal, exactly as the dongle sent it, to btsc_check.cs8 (about 14 MB) beside this file, so
it can be played back through the receiver elsewhere (--replay) without the dongle.
"""
import argparse
import inspect
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
NAME, DIST = 'btsc', 'btsc-stereo'


def install_command():
    """The pip command that installs this folder in the Python running this (as tried on
    Ubuntu 22.04 with its own pip and an upgraded one, Ubuntu 24.04, and virtual environments;
    radioconda on Windows is a conda environment like the last)."""
    import sysconfig
    try:
        import pip
        ver = tuple(int(x) for x in pip.__version__.split('.')[:2])
    except Exception:
        ver = (99, 0)
    env = sys.prefix != sys.base_prefix or os.path.isdir(os.path.join(sys.prefix, 'conda-meta'))
    opts = []
    if not env:
        opts.append('--user')                    # the system's Python: into your own folder
        if ver < (23, 0):                        # Ubuntu 22.04's pip runs the old install
            opts.append('--no-build-isolation')  # method in a sandbox that switches --user off
        elif os.path.exists(os.path.join(sysconfig.get_path('stdlib'), 'EXTERNALLY-MANAGED')):
            opts.append('--break-system-packages')   # Ubuntu 23.04 and newer ask for it
    return f'"{sys.executable}" -m pip install {" ".join(opts + ["-e"])} "{HERE}"'


def addon_class():
    """The BTSC add-on as the tuner would find it, and what to say about that."""
    import radio.addons
    for ep in radio.addons._entry_points():
        if ep.name == NAME:
            try:
                cls = ep.load()
            except Exception as e:
                return None, f"installed, but it won't load: {type(e).__name__}: {e}"
            return cls, f"installed, from {os.path.dirname(inspect.getfile(cls))}"
    sys.path.insert(1, HERE)                     # use this folder's copy for the check
    from btsc_stereo import Addon

    class EntryPoint:
        name = NAME

        def load(self):
            return Addon
    found = radio.addons._entry_points()
    radio.addons._entry_points = lambda: found + [EntryPoint()]
    return None, 'NOT INSTALLED in this Python'


def make_receiver(a):
    from gnuradio import gr, blocks
    from radio.fm.receiver import FMStereoReceiver
    pretend = None
    if a.pretend:                                 # for checking this script: kind[@deviation]
        sys.path.insert(1, HERE)
        import numpy as np
        import test_btsc as T
        kind, _, scale = a.pretend.partition('@')
        t = np.arange(int(6 * T.FS_C)) / T.FS_C
        pretend = T.station_file(kind, 0.5 * np.cos(2 * np.pi * 1100.0 * t),
                                 0.2 * np.cos(2 * np.pi * 10007.0 * t), float(scale or 1.0))

    class Rx(FMStereoReceiver):
        def _make_audio_sink(self, rate, device):           # no sound card needed
            return blocks.null_sink(gr.sizeof_float)

        if a.replay:
            def _make_source(self, args):
                return Replay(a.replay, self.samp_rate)
        elif pretend:
            def _make_source(self, args):
                import test_btsc as T
                return T.FileStation(pretend, self.samp_rate, a.freq)

    rec = os.path.join(HERE, 'btsc_check.cs8') if a.record else None
    return Rx(freq=a.freq, rf_gain=a.gain if a.gain is not None else 8.7, agc=a.gain is None,
              device_args=a.device_args, record_iq=rec), rec, pretend


def Replay(path, rate):
    """A saved btsc_check.cs8 in place of the dongle, in real time, over and over."""
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

        def __getattr__(self, name):
            if name.startswith('__'):
                raise AttributeError(name)
            try:                # GNU Radio 3.10.1 finds its own methods (connect...) this way
                return getattr(object.__getattribute__(self, '_impl'), name)
            except AttributeError:
                return lambda *a, **k: 0
    return _Replay()


def peak_dev(tb):
    """Peak deviation (kHz) in the latest composite snapshot."""
    import numpy as np
    x = np.asarray(tb.snapshot('mpx'))
    return float(np.max(np.abs(x))) / tb.MPX_FULL * 75.0 if len(x) and np.any(x) else 0.0


def median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('--freq', type=float, default=87.7e6,
                    help='the TV sound carrier (Hz); 87.7e6 is channel 6, received on 87.75 MHz')
    ap.add_argument('--secs', type=int, default=10)
    ap.add_argument('--gain', type=float, default=None, help='fixed tuner gain in dB (default: AGC)')
    ap.add_argument('--device-args', default='numchan=1 rtl=0')
    ap.add_argument('--record', type=float, default=0, metavar='SECS',
                    help='also save this many seconds of the radio signal (4.8 MB a second)')
    ap.add_argument('--replay', default=None, help='a saved .cs8 in place of the dongle')
    ap.add_argument('--pretend', default=None, help=argparse.SUPPRESS)
    a = ap.parse_args()

    import numpy as np
    from gnuradio import gr
    print(f"Python {sys.version.split()[0]} ({sys.executable}) | GNU Radio {gr.version()} | "
          f"numpy {np.__version__}")
    try:
        import scipy
        print(f"scipy {scipy.__version__}")
    except ImportError:
        print("scipy is NOT installed: the BTSC add-on needs it (Ubuntu: sudo apt install python3-scipy)")
    cls, said = addon_class()
    print(f"BTSC add-on: {said}")
    if cls is None and 'NOT INSTALLED' in said:
        print("  (checking with this folder's copy instead)")
    try:
        tb, rec, pretend = make_receiver(a)
    except Exception as e:
        print(f"\nThe FM receiver wouldn't start: {type(e).__name__}: {e}")
        print("Is the tuner still running, or the dongle unplugged?")
        return 1
    addon = next((x for x in tb.addons if x.name == NAME), None)
    if addon is None:
        print("The add-on didn't start (the line above says why). Measuring without it.")
    src = 'replaying ' + a.replay if a.replay else f'{a.freq / 1e6:.3f} MHz'
    print(f"\nListening to {src} for {a.secs} s"
          + (f", saving the first {a.record:g} s to {rec}" if rec else '') + '\n')
    print('   s  signal  clip  gain  19 kHz   dev  15.734 kHz pilot       BTSC')
    tb.start()
    t0, rows, errors, dev = time.monotonic(), [], [], 0.0
    closed = rec is None
    for sec in range(1, a.secs + 1):
        while time.monotonic() < t0 + sec:
            try:
                tb.update_blend()
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")
            dev = max(dev, peak_dev(tb))
            if not closed and time.monotonic() - t0 >= a.record:
                tb.rec_sink.close()
                closed = True
            time.sleep(0.02)
        o = addon.obj if addon is not None and addon.alive else None
        r = dict(q=tb.cnr_db, clip=tb.clip_pct, gain=tb.gain_db, p19=tb.pilot_level,
                 has19=tb.pilot_present, dev=dev, found=bool(o and o.found),
                 pilot=o.pilot if o else None, hz=o.pilot_hz if o else None,
                 why=o.why_not() if o else 'add-on not running', blend=tb.current_blend,
                 st=tb.addon_stereo, lpr=tb._stereo['gain_db'] if tb._stereo is not None else 0.0)
        rows.append(r)
        dev = 0.0
        pil = (f"{r['pilot'] * 5:4.2f} kHz at {r['hz']:7.1f} Hz" if r['pilot'] is not None
               and r['hz'] is not None else
               f"{r['pilot'] * 5:4.2f} kHz at    ?    Hz" if r['pilot'] is not None
               else '        --           ')
        state = f"STEREO, blend {r['blend']:.2f}, L+R +{r['lpr']:.1f} dB" if r['st'] else r['why']
        print(f"  {sec:2d}  {r['q']:4.1f} dB {r['clip']:5.2f}% {r['gain']:4.1f}  "
              f"{r['p19']:.3f}{'*' if r['has19'] else ' '} {r['dev']:3.0f} kHz  {pil}  {state}", flush=True)
    tb.stop()
    tb.wait()
    if pretend:
        os.remove(pretend)
    if errors:
        print(f"\n{len(errors)} errors from the receiver, the first: {errors[0]}")
    print("\n(signal = signal quality; 19 kHz = an FM stereo pilot's level, * = taken for one;\n"
          " dev = peak deviation (TV sound: about 25 kHz for mono, up to about 75 with stereo);\n"
          " L+R = how much the main channel is raised: 9.5 dB at the proper deviation)")

    late = rows[len(rows) // 2:] or rows
    print('\nWhat it found:')
    if addon is None or cls is None and 'NOT INSTALLED' not in said:
        print(f"- The add-on isn't running ({said}).")
    if 'NOT INSTALLED' in said:
        print("- The add-on isn't installed in this Python, so the tuner runs without it: a TV\n"
              "  station plays in mono. Install it with this, the command for this machine's\n"
              "  Python and pip:\n"
              f"      {install_command()}\n"
              "  then restart the tuner. The rest below is with this folder's copy.")
    found = [r['found'] for r in late]
    q = median([r['q'] for r in late])
    has19 = sum(r['has19'] for r in late) / len(late)
    pilot = median([r['pilot'] for r in late if r['pilot'] is not None])
    hz = median([r['hz'] for r in late if r['hz'] is not None])
    if all(found):
        lpr = median([r['lpr'] for r in late])
        print(f"- BTSC stereo found and playing: pilot {pilot * 5:.2f} kHz at {hz:.1f} Hz, "
              f"L+R raised {lpr:.1f} dB.")
        b = median([r['blend'] for r in late])
        if b < 0.99:
            print(f"- Blended {100 * (1 - b):.0f}% toward mono: signal quality {q:.1f} dB "
                  "(the same blend as FM stereo).")
        if not 0.85 <= pilot <= 1.15:
            print(f"- The station runs at about {100 * pilot:.0f}% of its proper deviation (BTSC's pilot is\n"
                  "  5 kHz). The radio uses the pilot as its level reference, so the stereo still\n"
                  "  works, but check the transmitter's aural deviation.")
    elif any(found):
        print(f"- BTSC stereo comes and goes ({sum(found)} of the last {len(found)} seconds). "
              "The lines above show which test drops out.")
    elif q is not None and q < 6:
        print(f"- Signal quality is only {q:.1f} dB, too noisy to judge. Bring the antenna or the\n"
              "  transmitter closer, or raise the gain (--gain).")
    elif pilot is None or pilot < 0.1:
        print("- No 15.734 kHz pilot: the station is in mono, or isn't TV sound"
              + (" (it has a 19 kHz pilot: an FM stereo station)." if has19 > 0.5 else "."))
    elif hz is None:
        print("- Something near 15.7 kHz, but the add-on can't tell what frequency its PLL is on.\n"
              "  That's a fault in the add-on: please send this whole output.")
    elif abs(hz - 15734.27) > 25:
        print(f"- Something near 15.7 kHz, but the add-on's PLL isn't locked to it (at {hz:.1f} Hz;\n"
              "  BTSC's pilot is 15734.3 Hz, the NTSC line rate). Check the transmitter's sample\n"
              "  rate matches what the hardware runs at.")
    elif pilot < 0.5:
        print(f"- The pilot is there but weak: {pilot * 5:.2f} kHz of deviation. BTSC's is 5 kHz and the\n"
              "  add-on wants at least 2.5. Check the transmitter's aural deviation.")
    else:
        print("- Every test passes now but it hasn't held for long enough yet. Try --secs 20.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
