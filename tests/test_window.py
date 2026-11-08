#!/usr/bin/env python3
"""
test_window.py - does the tuner window build and run, on AM and on FM?

    python3 tests/test_window.py                   # needs a screen
    xvfb-run -a python3 tests/test_window.py       # Linux without a screen
    python3 tests/test_window.py --fonts /usr/share/fonts/truetype/dejavu

Opens the real tuner window against the GNU Radio stand-in (tests/standin), with a pretend
dongle sending a steady signal, for 12 seconds on each band, and fails on any error, crash or
'Dongle lost'. On FM it also clicks Static cut and Noise reduction in Settings and checks each
one changes only its own setting. A third run is AM with a pretend add-on installed: its
station data must reach the dot matrix and the Advanced window, an alert must blink, and key I
must open its window. --fonts limits the window to the fonts in one folder (for example only
Ubuntu's basic DejaVu set, or an empty folder for none at all).

The window runs from a copy of the program in a temporary folder, so it can't touch your
own settings or presets.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SECS = 12


def child(appdir, band, fonts, secs):
    sys.path[:0] = [os.path.join(HERE, 'standin'), appdir]
    import tkinter as tk
    from gnuradio._sim import SIM
    from radio.ui import app as A, fonts as F
    SIM.reset()
    addon = band == 'AM+addon'
    if addon:
        install_pretend_addon()
        band = 'AM'
    if fonts is not None:
        F._FONT_DIRS = [fonts]
    log = []
    real_print = print

    def tee(*a, **k):
        log.append(' '.join(str(x) for x in a))
        real_print(*a, **k)
    A.print = tee
    root = tk.Tk()
    app = A.TunerApp(root, scale=0.6, freq=1080e3 if band == 'AM' else 90.5e6)
    states = []
    problems = []

    def find(w, text):
        for c in w.winfo_children():
            try:
                if c.cget('text') == text:
                    return c
            except tk.TclError:
                pass
            r = find(c, text)
            if r is not None:
                return r

    def settings_clicks():
        """Static cut and Noise reduction each change only their own setting."""
        app._toggle_settings()
        root.update()
        sc, nr = find(root, 'Static cut'), find(root, 'Noise reduction')
        if sc is None or nr is None:
            problems.append('Settings: Static cut or Noise reduction checkbox not found')
            return
        for widget, name in ((sc, 'dip_hicut'), (nr, 'noise_red')):
            for _ in range(2):
                before = (app.dip_hicut, app.noise_red)
                widget.invoke()
                after = (app.dip_hicut, app.noise_red)
                mine = 0 if name == 'dip_hicut' else 1
                if not (isinstance(after[mine], bool) and after[mine] == (not before[mine])
                        and after[1 - mine] == before[1 - mine]):
                    problems.append(f'Settings: clicking {name} went {before} -> {after}')
        app._toggle_settings()
        real_print('INFO Settings clicks:', 'ok' if not problems else '; '.join(problems))

    def sample():
        states.append(app._status)
        root.after(250, sample)

    def finish():
        path = getattr(F.font('vfd', 20), 'path', None)
        real_print('INFO band', app.band, '| display font:',
                   path if isinstance(path, str) else "Pillow's built-in font")
        seen = [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]]
        real_print('INFO status:', ' > '.join(seen))
        bad = [m for m in log + seen if any(w in m for w in ('Dongle lost', 'Receiver stopped', 'Error'))]
        bad += problems
        real_print('RESULT', 'FAIL' if bad or app.band != band or 'Ready' not in seen else 'PASS')
        app._on_close()
    def addon_checks():
        """The pretend add-on's station data on the dot matrix and in Advanced; alert; key I."""
        now = time.monotonic()
        call, ps, rt = app._rds_fields(app.tb, now)
        if (call.strip(), ps.strip()) != ('WAAA', 'TESTING') or not rt.startswith('Testing one'):
            problems.append(f'add-on: dot matrix shows {(call, ps, rt)!r}')
        app._toggle_advanced()
        root.update()
        root.after(1500, addon_checks_2)

    def addon_checks_2():
        if app._ps_var.get() != 'TESTING' or 'Pretend add-on: decoding' not in app._rdsd_var.get():
            problems.append(f'add-on: Advanced shows {app._ps_var.get()!r} / {app._rdsd_var.get()!r}')
        app.tb.station['alert'] = 2
        t0 = float(int(time.monotonic()) + 1)          # a whole second: the first half shows ALERT
        shown = {app._rds_fields(app.tb, t)[1].strip() for t in (t0, t0 + 0.5)}
        if shown != {'ALERT', ''}:
            problems.append(f'add-on: alert blinks {shown!r}')
        app.tb.station['alert'] = 0
        app._addon_window()
        w = PretendAddon.windows
        if len(w) != 1 or w[0].toggles != 1 or w[0].current() is not app.tb.addons[0].obj:
            problems.append('add-on: key I did not open its window')
        app._toggle_advanced()
        real_print('INFO add-on station data:', 'ok' if not problems else '; '.join(problems))

    root.after(250, sample)
    if band == 'FM':
        root.after(2000, settings_clicks)
    if addon:
        root.after(5000, addon_checks)
    root.after(int(secs * 1000), finish)
    root.mainloop()


class PretendAddon:
    """Stands in for an installed add-on: steady station data on AM."""
    BANDS = ('AM',)
    NAME = 'TEST'
    windows = []

    def __init__(self, rx):
        from gnuradio import blocks, gr
        rx.connect(rx.addon_tap(), blocks.null_sink(gr.sizeof_gr_complex))

    def update(self, now):
        return {'pi': 0x54A8, 'call': 'WAAA', 'ps': 'TESTING', 'rt': 'Testing one two three',
                'pty': 3, 'synced': True, 'rate': 11.0, 'bler': 2.0, 'tp': False, 'ta': False}

    def readout(self):
        return 'Pretend add-on: decoding'

    def meter(self):
        return ('Test data', 0.8, 'good')

    @classmethod
    def make_window(cls, parent, current):
        class Win:
            toggles = 0

            def toggle(self):
                self.toggles += 1
        w = Win()
        w.current = current
        cls.windows.append(w)
        return w


def install_pretend_addon():
    import radio.addons

    class EntryPoint:
        name = 'pretend'

        def load(self):
            return PretendAddon
    radio.addons._entry_points = lambda: [EntryPoint()]


def run(band, fonts):
    tmp = tempfile.mkdtemp(prefix='tuner_test_')
    try:
        for f in glob.glob(os.path.join(REPO, '*.py')):
            shutil.copy(f, tmp)
        shutil.copytree(os.path.join(REPO, 'radio'), os.path.join(tmp, 'radio'),
                        ignore=shutil.ignore_patterns('__pycache__'))
        env = dict(os.environ, HOME=tmp, USERPROFILE=tmp)
        cmd = [sys.executable, os.path.abspath(__file__), '--child', band, '--appdir', tmp]
        if fonts is not None:
            cmd += ['--fonts', fonts]
        try:
            p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=SECS + 60)
            out = p.stdout
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or '') + '\nRESULT FAIL (timed out)'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = 'RESULT PASS' in out
    print(f"{'ok  ' if ok else 'FAIL'} {band}")
    for line in out.splitlines():
        if line.startswith('INFO '):
            print('       ' + line[5:])
        elif not ok and not line.startswith('RESULT'):
            print('     | ' + line)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('--fonts', default=None, help='use only the fonts in this folder')
    ap.add_argument('--child', choices=('AM', 'FM', 'AM+addon'), help=argparse.SUPPRESS)
    ap.add_argument('--appdir', help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        child(a.appdir, a.child, a.fonts, SECS)
        return 0
    results = [run(band, a.fonts) for band in ('AM', 'FM', 'AM+addon')]
    print('\nAll passed.' if all(results) else f'\n{results.count(False)} failed.')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
