#!/usr/bin/env python3
"""
test_bandplan.py - the bands behind the Bands panel. Needs neither GNU Radio nor a screen.

    python3 tests/test_bandplan.py

Checks every band is sane (edges, step, keys unique, the panel's labels), the mode each band
and each part of a band is usually heard in (CW at the bottom of the amateur bands, AM on
shortwave, narrow FM on 2 m...), snapping to channels (FM's odd tenths, CB, a stepping key's
ranges, SSB's finer raster), the channelized bands' channel tables, the HF filters (CB's out-of-order 23, stepping, GMRS repeater
pairs), the repeater offsets, and which band a frequency falls in.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from radio.ui import bandplan as B  # noqa: E402

failed = 0
k, M = 1e3, 1e6


def check(name, ok, detail=''):
    global failed
    failed += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name:52s} {detail}")


def main():
    print('Every band')
    keys = [b.key for b in B.BANDS]
    check('keys are unique', len(keys) == len(set(keys)))
    bad = [b.key for b in B.BANDS if not (0 < b.lo < b.hi and 0 < b.step < b.hi - b.lo)]
    check('edges and steps make sense', not bad, ', '.join(bad))
    bad = [b.key for b in B.BANDS if b.mode is not None and b.mode not in B.MODES]
    bad += [f'{b.key} part' for b in B.BANDS for lo, hi, m in b.parts
            if m not in B.MODES or not (b.lo <= lo < hi <= b.hi)]
    check('modes are known, parts lie inside their band', not bad, ', '.join(bad))
    bad = [b.key for b in B.BANDS if b.group not in B.GROUPS]
    check('every band belongs to a panel group', not bad, ', '.join(bad))
    sw = [b.label for b in B.BANDS if b.group == 'shortwave']
    ham = [b.label for b in B.BANDS if b.group == 'amateur']
    check("shortwave keys as on the mock-up", sw == ['120', '90', '75', '60', '49', '41', '31', '25',
                                                     '22', '19', '16', '15', '13', '11'], ' '.join(sw))
    check("amateur keys as on the mock-up", ham == ['160', '80', '60', '40', '30', '20', '17', '15', '12',
                                                    '10', '6', '2', '1.25', '70cm', '33cm', '23cm'], ' '.join(ham))
    check('broadcast: LW, MW, FM together', [b.label for b in B.BANDS if b.group == 'broadcast'] == ['LW', 'MW', 'FM'])
    bad = [b.key for b in B.BANDS if b.hi > 1766 * M or b.lo < 100 * k]
    check("all within an RTL-SDR's reach (100 kHz to 1766 MHz)", not bad, ', '.join(bad))

    print('The mode each band and part is usually heard in')
    for key, f, want in (('MW', 1080 * k, 'AM'), ('SW41', 7335 * k, 'AM'), ('FM', 90.5 * M, 'FM'),
                         ('HAM40', 7030 * k, 'CW'), ('HAM40', 7200 * k, 'LSB'), ('HAM80', 3550 * k, 'CW'),
                         ('HAM80', 3885 * k, 'LSB'), ('HAM20', 14070 * k, 'CW'), ('HAM20', 14250 * k, 'USB'),
                         ('HAM30', 10120 * k, 'CW'), ('HAM10', 28400 * k, 'USB'), ('HAM10', 29100 * k, 'AM'),
                         ('HAM10', 29600 * k, 'NFM'), ('HAM6', 50.125 * M, 'USB'), ('HAM6', 52.525 * M, 'NFM'),
                         ('HAM2', 144.2 * M, 'USB'), ('HAM2', 146.52 * M, 'NFM'), ('HAM70', 446.0 * M, 'NFM'),
                         ('AIR', 121.5 * M, 'AM'), ('WX', 162.55 * M, 'NFM'), ('MARINE', 156.8 * M, 'NFM'),
                         ('CB', 27.185 * M, 'AM'), ('FRS', 462.5625 * M, 'NFM'), ('PUBLIC', 155.0 * M, 'NFM2')):
        got = B.usual_mode(B.band(key), f)
        check(f'{key} at {f / M:g} MHz', got == want, f'{got} (want {want})')
    bad = [m for m in B.MODES if m not in B.MODE_RX or m not in B.MODE_KEY or m not in B.MODE_LEGEND]
    check('every mode has a receiver, a key and a legend', not bad, ', '.join(bad))
    check('only AM and FM kinds can carry station data', set(B.DATA_MODES) == {'AM', 'SAM_U', 'SAM_L', 'FM'})

    print('Channels')
    for key, f, want in (('FM', 90.45 * M, 90.5 * M), ('FM', 87.4 * M, 87.5 * M), ('FM', 108.5 * M, 107.9 * M),
                         ('MW', 1083 * k, 1080 * k), ('SW41', 7337 * k, 7335 * k), ('CB', 27.187 * M, 27.185 * M),
                         ('AIR', 121.51 * M, 121.5 * M), ('FRS', 462.56 * M, 462.5625 * M),
                         ('PUBLIC', 460.004 * M, 460.0 * M), ('PUBLIC', 860.009 * M, 860.0125 * M)):
        got = B.snap(B.band(key), f)
        check(f'{key}: {f / M:g} MHz snaps to {want / M:g}', abs(got - want) < 1, f'{got / M:g}')
    check("SSB's 100 Hz raster: 14200.07 kHz snaps to 14200.1", abs(B.snap(B.band('HAM20'), 14200.07 * k, 100) - 14200.1 * k) < 1)
    check('60 m keeps its half-kHz origin on it', abs(B.snap(B.band('HAM60'), 5357.04 * k, 100) - 5357.0 * k) < 1)
    bad = [m for m in B.FILTERS if B.DEFAULT_FILTER[m] not in B.FILTERS[m] or B.MODE_RX[m] != 'HF']
    check("each HF mode's filter starts on one of its widths", not bad, ', '.join(bad))
    check('SSB and CW tune in 100 Hz steps', all(B.MODE_STEP[m] == 100 for m in ('USB', 'LSB', 'CW')))
    pub = B.band('PUBLIC')
    check('Police/Fire steps through VHF, UHF and 800 MHz', [r[3] for r in pub.ranges] ==
          ['PUBLIC SAFETY VHF', 'PUBLIC SAFETY UHF', 'PUBLIC SAFETY 800'])
    check('its display name follows the range', B.display_name(pub, 460 * M) == 'PUBLIC SAFETY UHF')

    print('Repeater offsets')
    for key, f, want in (('HAM2', 146.94 * M, -600 * k), ('HAM2', 147.06 * M, 600 * k), ('HAM2', 145.31 * M, -600 * k),
                         ('HAM2', 146.52 * M, 0), ('HAM70', 442.1 * M, 5 * M), ('HAM70', 448.0 * M, -5 * M),
                         ('HAM125', 224.5 * M, -1.6 * M), ('HAM6', 53.03 * M, -1 * M), ('HAM10', 29.62 * M, -100 * k),
                         ('FRS', 462.6 * M, 5 * M)):
        got = B.repeater_offset(B.band(key), f)
        check(f'{key} output {f / M:g} MHz', got == want, f'{got / k:+g} kHz')

    print('Channelized bands')
    cb, wx, frs, mar, murs = (B.band(x) for x in ('CB', 'WX', 'FRS', 'MARINE', 'MURS'))
    bad = [f'{b.key} {l}' for b in B.BANDS for l, f, _ in b.channels if not b.lo - 1 <= f <= b.hi + 1]
    bad += [b.key for b in B.BANDS if b.channels and len({l for l, _, _ in b.channels}) != len(b.channels)]
    check('channels inside their band, numbers unique', not bad, ', '.join(bad))
    check('channel counts: CB 40, FRS/GMRS 22, WX 7, MURS 5',
          [len(b.channels) for b in (cb, frs, wx, murs)] == [40, 22, 7, 5])
    for b, n, f in ((cb, 19, 27.185), (cb, 23, 27.255), (cb, 24, 27.235), (cb, 40, 27.405), (wx, 1, 162.550),
                    (wx, 6, 162.500), (frs, 1, 462.5625), (frs, 15, 462.550), (frs, 22, 462.725),
                    (mar, 16, 156.800), (mar, 22, 157.100), (murs, 5, 154.600)):
        got = B.channel_freq(b, n)
        check(f'{b.key} channel {n} is {f:g} MHz', got is not None and abs(got - f * M) < 1, f'{(got or 0) / M:g}')
    steps = [B.channel_at(cb, B.next_channel(cb, B.channel_freq(cb, n), +1)) for n in (22, 23, 24, 40)]
    check('CB steps in channel order: 22>23>24>25, 40>1', steps == ['23', '24', '25', '1'], ' '.join(steps))
    check('CB down from 1 goes to 40', B.channel_at(cb, B.next_channel(cb, B.channel_freq(cb, 1), -1)) == '40')
    check('off a channel, a step goes to the nearest first', B.next_channel(cb, 27.183 * M, +1) == 27.185 * M)
    check('27.183 MHz snaps to channel 19', B.channel_at(cb, B.nearest_channel(cb, 27.183 * M)) == '19')
    check('GMRS repeaters on 15-22 (+5 MHz), none on FRS 1-7',
          B.repeater_offset(frs, B.channel_freq(frs, 18)) == 5 * M and B.repeater_offset(frs, B.channel_freq(frs, 3)) == 0)
    check('top channel numbers: CB 40, marine 88', (B.top_channel(cb), B.top_channel(mar)) == (40, 88))

    print('Which band a frequency is in')
    for f, prefer, want in ((7200 * k, None, 'SW41'), (7100 * k, None, 'HAM40'), (162.55 * M, None, 'WX'),
                            (156.8 * M, None, 'MARINE'), (153.0 * M, None, 'MURS'), (90.5 * M, None, 'FM'),
                            (7200 * k, 'HAM40', 'HAM40'), (5 * M, None, 'SW60'), (300 * M, None, None)):
        got = B.band_for(f, prefer)
        got = got.key if got else None
        check(f'{f / M:g} MHz' + (f' (on {prefer})' if prefer else ''), got == want, f'{got} (want {want})')

    print('\nAll passed.' if not failed else f'\n{failed} failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
