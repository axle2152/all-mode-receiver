#!/usr/bin/env python3
"""
test_window.py - does the tuner window build and run, on AM and on FM?

    python3 tests/test_window.py                   # needs a screen
    xvfb-run -a python3 tests/test_window.py       # Linux without a screen
    python3 tests/test_window.py --fonts /usr/share/fonts/truetype/dejavu

Opens the real tuner window against the GNU Radio stand-in (tests/standin), with a pretend
dongle sending a steady signal, for 12 seconds on each band, and fails on any error, crash or
'Dongle lost'. --fonts limits the window to the fonts in one folder (for example only
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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SECS = 12


def child(appdir, band, fonts, secs):
    sys.path[:0] = [os.path.join(HERE, 'standin'), appdir]
    import tkinter as tk
    from gnuradio._sim import SIM
    from radio.ui import app as A, fonts as F
    SIM.reset()
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
        real_print('RESULT', 'FAIL' if bad or app.band != band or 'Ready' not in seen else 'PASS')
        app._on_close()
    root.after(250, sample)
    root.after(int(secs * 1000), finish)
    root.mainloop()


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
    ap.add_argument('--child', choices=('AM', 'FM'), help=argparse.SUPPRESS)
    ap.add_argument('--appdir', help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        child(a.appdir, a.child, a.fonts, SECS)
        return 0
    results = [run(band, a.fonts) for band in ('AM', 'FM')]
    print('\nAll passed.' if all(results) else f'\n{results.count(False)} failed.')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
