#!/usr/bin/env python3
"""
rx_check.py - is the receiver's signal chain moving? No window, no tuner.

    python3 rx_check.py                      # AM, 1080 kHz
    python3 rx_check.py --freq 750e3
    python3 rx_check.py --band fm --freq 90.5e6
    python3 rx_check.py --audio-device pulse

Runs the AM receiver (radio/am.py) or the FM receiver (radio/fm) twice,
10 s each:
  1. as the tuner runs it, with sound
  2. with the sound output swapped for a silent one
and prints every second how many samples each stage has moved, whether the
receiver's "dongle lost" watchdog went off, then a verdict in plain words.
Each run is its own process with a time limit, so a stuck run can't hang this.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SECS = 10
STAGES = {'am': ('from dongle', 'carrier PLL', 'to sound'),
          'fm': ('from dongle', 'FM demod', 'to sound')}
GIVE_UP = 5                     # the tuner reconnects after this many errors in a row


# ---------------------------------------------------------------------------- child
def child(band, mode, freq, secs, am_source, audio_device):
    import numpy as np
    from gnuradio import gr, blocks, audio

    print('INFO GNU Radio', gr.version(), '| numpy', np.__version__,
          '| Python', sys.version.split()[0], '|', sys.platform, flush=True)
    if mode == 'silent':                 # both receivers look up audio.sink when they're built
        audio.sink = lambda *a, **k: blocks.null_sink(gr.sizeof_float)
    if band == 'am':
        from radio.am import AMReceiver
        tb = AMReceiver(freq=freq, am_source=am_source, audio_device=audio_device)
        probes = (lambda: tb.xlate.nitems_read(0), lambda: tb.pll.nitems_written(0),
                  lambda: tb.audio_sink.nitems_read(0))
        rates = (tb.samp_rate, tb.ch_rate, tb.audio_rate)
    else:
        from radio.fm import FMStereoReceiver
        tb = FMStereoReceiver(freq=freq, audio_device=audio_device)
        probes = (lambda: tb.dc_sub.nitems_read(0), lambda: tb.fm_demod.nitems_written(0),
                  lambda: tb.audio_sink.nitems_read(0))
        rates = (tb.samp_rate, tb.mpx_rate, tb.audio_rate)
    print('RATES', *[int(r) for r in rates], flush=True)
    tb.start()
    print('STARTED', flush=True)
    t0 = time.monotonic()
    errors, tripped = 0, False
    for i in range(1, secs + 1):
        while time.monotonic() < t0 + i:             # drive the watchdog as the tuner does
            try:
                tb.update_blend()
                errors = 0
            except Exception as e:
                errors += 1
                if errors >= GIVE_UP and not tripped:
                    tripped = True
                    print(f'WATCHDOG {time.monotonic() - t0:.1f} s: {type(e).__name__}: {e}', flush=True)
            time.sleep(0.02)
        vals = []
        for p in probes:
            try:
                vals.append(str(int(p())))
            except Exception:
                vals.append('-1')
        print('DATA', i, *vals, flush=True)
    try:
        x = np.asarray(tb.snap_wb.level())
        if len(x):
            print('SIGNAL', len(np.unique(np.round(x, 6))), f'{float(np.mean(np.abs(x))):.4f}',
                  flush=True)
    except Exception as e:
        print('INFO signal snapshot failed:', e, flush=True)
    tb.stop()
    tb.wait()
    print('STOPPED', flush=True)


# ---------------------------------------------------------------------------- parent
def run(a, mode):
    cmd = [sys.executable, os.path.abspath(__file__), '--child', mode, '--band', a.band,
           '--freq', str(a.freq), '--am-source', a.am_source, '--audio-device', a.audio_device]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         cwd=HERE)
    hung = False
    try:
        out, _ = p.communicate(timeout=SECS + 25)
    except subprocess.TimeoutExpired:
        hung = True
        p.kill()
        out, _ = p.communicate()
    stages = STAGES[a.band]
    n = len(stages)
    rows, rates, signal, watchdog, msgs = [], None, None, None, []
    started = stopped = False
    for line in out.splitlines():
        w = line.split()
        if w[:1] == ['DATA'] and len(w) == n + 2:
            rows.append([int(v) for v in w[1:]])
        elif w[:1] == ['RATES'] and len(w) == n + 1:
            rates = [int(v) for v in w[1:]]
        elif w[:1] == ['SIGNAL'] and len(w) == 3:
            signal = (int(w[1]), float(w[2]))
        elif w[:1] == ['WATCHDOG']:
            watchdog = line[9:]
        elif w[:1] == ['STARTED']:
            started = True
        elif w[:1] == ['STOPPED']:
            stopped = True
        elif w[:1] == ['INFO']:
            print('  ' + line[5:])
        elif line.strip():
            msgs.append(line)
    if msgs:
        print('  Messages from GNU Radio and the dongle:')
        for m in msgs[-25:]:
            print('    | ' + m)
    if rates:
        print('  Expected per second: ' + ', '.join(f'{s} about {r:,}' for s, r in zip(stages, rates)))
    if rows:
        print('  %6s' % 'second' + ''.join('%14s' % s for s in stages))
        for r in rows:
            print('  %6d' % r[0] + ''.join('%14s' % (f'{v:,}' if v >= 0 else 'n/a') for v in r[1:]))
    if watchdog:
        print(f'  The "dongle lost" watchdog went off after {watchdog}')
    elif started and rows:
        print('  The "dongle lost" watchdog stayed quiet.')
    return dict(rows=rows, signal=signal, watchdog=watchdog, started=started, stopped=stopped,
                hung=hung, code=p.returncode, stages=stages)


def moving(res):
    """Per stage: still moving over the last 3 s?"""
    rows = res['rows']
    if len(rows) < 4:
        return None
    a, b = rows[-4], rows[-1]
    return [b[k] > a[k] for k in range(1, len(b))]


def first_stopped(res):
    """The stage that stopped first, by the second its count last changed."""
    rows, last = res['rows'], {}
    for k in range(1, len(rows[0])):
        last[k] = 0
        for i in range(1, len(rows)):
            if rows[i][k] > rows[i - 1][k]:
                last[k] = rows[i][0]
    k = min(last, key=lambda j: (last[j], j))
    return res['stages'][k - 1], last[k]


def verdict(snd, sil):
    print('\n=== Verdict ===')
    ms, mq = moving(snd), moving(sil)
    if not snd['started'] and not sil['started']:
        print('The receiver did not start at all; the messages above say why.')
        return
    if snd['signal'] and snd['signal'][0] <= 3:
        print('Note: the dongle\'s samples are all (nearly) the same value, so no signal is reaching\n'
              'it. Check the antenna is connected to the dongle.')
    if ms and all(ms) and not snd['hung']:
        if snd['watchdog']:
            print('Samples kept flowing, yet the "dongle lost" watchdog went off. That is a false\n'
                  'alarm in the receiver code, not a dongle problem.')
            return
        print('The receiver runs fine on its own, with sound, for %d s: the dongle, GNU Radio\n'
              'and the sound card all work. If the tuner window still drops the dongle, the\n'
              'problem is in how the window drives the receiver.' % SECS)
        return
    if mq and all(mq) and not (ms and all(ms)):
        print('Without sound the receiver runs fine; with sound it stops.\n'
              'So the sound output is what stalls it. Try --audio-device pulse (or plughw:0,0).')
        return
    if mq is None and ms is None:
        print('Neither run got far enough to measure; the messages above say why.')
        return
    res = sil if mq is not None else snd
    st, t = first_stopped(res)
    print('The receiver stops even without sound. The first stage to stop was "%s", after %d s.'
          % (st, t))
    if snd['hung'] or sil['hung']:
        print('A run also failed to shut down, so a block was stuck rather than just waiting.')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('--band', choices=('am', 'fm'), default='am')
    ap.add_argument('--freq', type=float, default=None, help='Hz (default 1080e3 AM, 90.5e6 FM)')
    ap.add_argument('--am-source', default='direct_q', choices=('direct_q', 'native', 'upconverter'))
    ap.add_argument('--audio-device', default='',
                    help='sound output for GNU Radio, e.g. pulse or plughw:0,0 (default: system default)')
    ap.add_argument('--child', choices=('sound', 'silent'), help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.freq is None:
        a.freq = 1080e3 if a.band == 'am' else 90.5e6
    if a.child:
        child(a.band, a.child, a.freq, SECS, a.am_source, a.audio_device)
        return
    print('Checking the %s receiver on %s' % (a.band.upper(), f'{a.freq / 1e3:.0f} kHz' if a.band == 'am'
                                              else f'{a.freq / 1e6:.1f} MHz'))
    print('\n=== Run 1 of 2: with sound, as the tuner runs it (%d s) ===' % SECS)
    snd = run(a, 'sound')
    time.sleep(2)                                    # let the dongle go before the next run
    print('\n=== Run 2 of 2: sound output swapped for a silent one (%d s) ===' % SECS)
    sil = run(a, 'silent')
    verdict(snd, sil)
    print('\nPlease paste everything above into the chat.')


if __name__ == '__main__':
    main()
