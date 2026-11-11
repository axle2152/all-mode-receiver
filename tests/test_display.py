#!/usr/bin/env python3
"""
test_display.py - does the tuner's frequency display read right? Needs neither GNU Radio nor
a screen.

    python3 tests/test_display.py

Checks what the digits show for broadcast, shortwave, amateur and microwave frequencies at
their tuning steps, off the step, and at the limits (kHz below 30 MHz, MHz above, small
digits below 1 kHz), and the S-meter's arithmetic (S units both sides of 30 MHz, the bar,
calibration), then draws the tuner's display for each reading and both meter views.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from radio.ui import smeter  # noqa: E402
from radio.ui.frequency import N_BIG, N_FINE, as_text, freq_digits  # noqa: E402

failed = 0


def check(name, ok, detail=''):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:44s} {detail}")


CASES = (
    # what, Hz, step Hz, reads
    ('AM broadcast', 1080e3, 10e3, '1080 kHz'),
    ('AM, 9 kHz channels', 1602e3, 9e3, '1602 kHz'),
    ('long wave', 153e3, 9e3, '153 kHz'),
    ('FM broadcast', 90.5e6, 200e3, '90.5 MHz'),
    ('FM broadcast, top of the band', 107.9e6, 200e3, '107.9 MHz'),
    ('FM off its raster', 90.55e6, 200e3, '90.55 MHz'),
    ('shortwave at 5 kHz', 9580e3, 5e3, '9580 kHz'),
    ('shortwave off the step', 9585e3, 10e3, '9585 kHz'),
    ('20 m SSB, 10 Hz steps', 14230.5e3, 10, '14230.50 kHz'),
    ('20 m SSB, 1 Hz steps', 14230.5e3, 1, '14230.500 kHz'),
    ('just below 30 MHz', 29999e3, 1e3, '29999 kHz'),
    ('30 MHz reads in MHz', 30e6, 10e3, '30.00 MHz'),
    ('2 m FM', 146.52e6, 5e3, '146.520 MHz'),
    ('12.5 kHz channels', 146.5125e6, 12.5e3, '146.512.5 MHz'),
    ('the RTL-SDR\'s top', 1766e6, 200e3, '1766.0 MHz'),
    ('the most it holds', 9_999_999_999, 1, '9999.999.999 MHz'),
    ('beyond it', 10e9, 1, '------- MHz'),
)


def main():
    print('Frequency readout')
    for what, hz, step, want in CASES:
        got = as_text(hz, step)
        check(what, got == want, f"{got!r}" + ('' if got == want else f" (expected {want!r})"))
    big, dots, fine, _ = freq_digits(90.5e6, 200e3)
    check('big digits right-aligned', big == (' ',) * 4 + ('9', '0', '5'), repr(big))
    check('one decimal point, after the MHz', dots == (False,) * 5 + (True, False), repr(dots))
    check('small digits unlit', fine == (' ',) * N_FINE, repr(fine))
    big, dots, fine, _ = freq_digits(14230.5e3, 10)
    check('fine digits left-aligned', fine == ('5', '0', ' '), repr(fine))
    check('point before the fine digits', dots[-1] and sum(dots) == 1, repr(dots))
    lengths = {len(x) for hz, step in ((1e5, 1), (9e9, 1), (5e6, 1e3))
               for x in freq_digits(hz, step)[:2]}
    check('always seven big digits', lengths == {N_BIG}, repr(lengths))

    print('S-meter')
    for dbm, hz, segs, text in ((-73, 1e6, 9, 'S9'), (-121, 7e6, 1, 'S1'), (-97, 14e6, 5, 'S5'),
                                (-53, 1e6, 11, 'S9+20'), (-13, 1e6, 15, 'S9+60'), (0, 1e6, 15, 'S9+73'),
                                (-130, 1e6, 0, 'below S1'), (-93, 146e6, 9, 'S9'), (-73, 146e6, 11, 'S9+20')):
        got = (round(smeter.segments(dbm, hz)), smeter.s_text(dbm, hz))
        check(f'{dbm} dBm at {hz / 1e6:g} MHz', got == (segs, text),
              f'{got[0]} bars, {got[1]}' + ('' if got == (segs, text) else f' (expected {segs}, {text})'))
    cal = smeter.calibration_for(-73.0, -40.0, 20.0)
    check('calibration reads back the known level', smeter.to_dbm(-40.0, 20.0, cal) == -73.0, f'{cal:+.1f} dB')
    check('and follows the gain', smeter.to_dbm(-30.0, 30.0, cal) == -73.0, '10 dB more gain, same dBm')
    paths = (smeter.path_for(True, 1e6), smeter.path_for(False, 14e6), smeter.path_for(False, 90e6))
    check('a calibration per input path', paths == ('direct', 'hf', 'vhf'), repr(paths))

    print('The tuner display')
    try:
        from radio.ui.vfd import TunerVfd
    except ImportError as e:                       # no numpy or Pillow here
        print(f'  (skipped: {e})')
    else:
        vfd = TunerVfd(0.5)
        frames = set()
        for what, hz, step, _ in CASES:
            big, dots, fine, unit = freq_digits(hz, step)
            st = dict(legends=frozenset(), fixed=True, unit=unit, freq=(big, dots, fine),
                      sig=15, ch=(' ', '7'), rds=('', '', ''))
            try:
                frames.add(vfd.render(st).tobytes())
            except Exception as e:
                check(f'draws {what}', False, f'{type(e).__name__}: {e}')
        check('every reading draws, each one different', len(frames) == len(CASES),
              f'{len(frames)} different frames for {len(CASES)} readings')
        big, dots, fine, unit = freq_digits(14230.5e3, 10)
        views = set()
        for meter, uncal in (('Q', False), ('S', True), ('S', False)):
            st = dict(legends=frozenset(), fixed=True, unit=unit, freq=(big, dots, fine), sig=9,
                      meter=meter, uncal=uncal, ch=(' ', ' '), rds=('', '', ''))
            views.add(vfd.render(st).tobytes())
        check('quality, S units, and UNCAL each draw', len(views) == 3, f'{len(views)} different frames')

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
