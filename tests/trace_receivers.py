#!/usr/bin/env python3
"""
trace_receivers.py - prove a change leaves the receivers' signal chains alone

    python3 tests/trace_receivers.py before.json                  # this checkout
    python3 tests/trace_receivers.py after.json --code /other/checkout
    python3 tests/trace_receivers.py --compare before.json after.json

Builds the AM receiver (all three sources) and the FM receiver (with and without IQ
recording) against the GNU Radio stand-in with tracing on, takes each through a scripted
session (start, tune, filters, tone, stereo, mono, gain, mute, readouts) on a pretend clock,
and writes every event to a JSON file: each block made and its arguments, each connection,
each call on a block, what each step returned, and the receiver's plain attributes at the end.

Record before and after a refactor and compare: identical means every block, setting and
connection is the same. --compare lists any differences. For "before", check the old
commit out into another folder (git worktree add ../before HEAD~1) and pass --code.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def record(out):
    import numpy as np
    from gnuradio import _log
    from gnuradio._sim import SIM, FakeTime
    import radio.am
    import radio.fm.rds
    import radio.fm.receiver
    import radio.receiver
    for m in (radio.am, radio.fm.receiver, radio.fm.rds, radio.receiver):
        m.time = FakeTime()
    try:                                  # the receivers alone: installed add-ons stay out, so
        import radio.addons               # traces compare the same thing on any machine
        radio.addons._entry_points = lambda: []
    except ImportError:                   # older code, from before add-ons
        pass
    log = _log.EVENTS.append

    def state(tb):
        st = {}
        for k, v in sorted(vars(tb).items()):
            if isinstance(v, (bool, int, float, str)) or v is None:
                st[k] = _log.canon(v)
            elif isinstance(v, (list, tuple)) and len(v) < 50 and \
                    all(isinstance(x, (bool, int, float, str)) for x in v):
                st[k] = _log.canon(list(v))
        return st

    def step(label, fn):
        log(('step', label))
        try:
            r = fn()
            log(('ret', label, _log.canon(dict(r) if hasattr(r, 'keys') else r)))
        except Exception as e:
            log(('exc', label, type(e).__name__, str(e)[:120]))

    def run(tb, secs, label):
        log(('step', label))
        errs = []
        t_end = SIM.t + secs
        while SIM.t < t_end:
            try:
                tb.update_blend()
            except Exception as e:
                errs.append(f"{type(e).__name__}: {str(e)[:80]}")
            SIM.t += 0.02
        log(('blend', label, errs[:3], len(errs)))

    # older code, before receivers declared FEATURES: the same sets, by receiver
    OLD_FEATURES = {'AMReceiver': {'mono', 'am_width', 'am_stereo', 'nrsc'},
                    'FMStereoReceiver': {'mono', 'fm_helpers', 'echo', 'fm_bandwidth', 'rds'}}

    def common(tb, f2):
        if not hasattr(tb, 'has'):
            feats = OLD_FEATURES[type(tb).__name__]
            tb.has = lambda f: f in feats
        step('gain', lambda: tb.set_output_gain(0.5, 0.5))
        step('tone0', lambda: tb.set_tone(0, 0))
        if tb.has('fm_helpers'):
            step('enh', lambda: tb.set_enhancements(blend=True, hicut=True))
            step('dip', lambda: tb.set_dip_hicut(True))
            step('nr', lambda: tb.set_noise_reduction(True))
        if tb.has('echo'):
            step('eq', lambda: tb.set_eq_mode('auto'))
        if tb.has('fm_bandwidth'):
            step('ifm', lambda: tb.set_if_mode('auto'))
        if tb.has('am_width'):
            step('ceil', lambda: tb.set_auto_ceiling(8000))
            step('ifm', lambda: tb.set_if_mode('auto'))
            step('wide0', lambda: tb.set_wide(False))
        if tb.has('nrsc'):
            step('nrsc0', lambda: tb.set_nrsc_deemphasis(False))
        if tb.has('am_stereo'):
            step('st', lambda: tb.set_stereo_system('auto'))
        step('mute', lambda: tb.set_mute(False))
        step('start', lambda: tb.start())
        run(tb, 3.0, 'blend1')
        step('freq', lambda: tb.set_freq(f2))
        run(tb, 2.0, 'blend2')
        if tb.has('am_width'):
            step('wide1', lambda: tb.set_wide(True))
            step('ceil10', lambda: tb.set_auto_ceiling(10200))
            step('wide0b', lambda: tb.set_wide(False))
            step('fixed', lambda: tb.set_if_mode('fixed'))
        if tb.has('echo'):
            step('eqon', lambda: tb.set_eq_mode('on'))
        if tb.has('fm_bandwidth'):
            step('narrow', lambda: tb.set_if_mode('narrow'))
        if tb.has('fm_helpers'):
            step('enh0', lambda: tb.set_enhancements(blend=False, hicut=False))
        step('tone', lambda: tb.set_tone(6, -3))
        if tb.has('nrsc'):
            step('nrsc1', lambda: tb.set_nrsc_deemphasis(True))
        if tb.has('am_stereo'):
            step('kahn', lambda: tb.set_stereo_system('kahn'))
        if tb.has('mono'):
            step('mono1', lambda: tb.set_force_mono(True))
            step('mono0', lambda: tb.set_force_mono(False))
        step('agc0', lambda: tb.set_agc(False))
        step('man', lambda: tb.set_manual_gain(12.5))
        step('mute1', lambda: tb.set_mute(True))
        run(tb, 1.0, 'blend3')
        step('lag', lambda: tb.audio_lag())
        step('mon', lambda: tb.monitor())
        for k in ('iq', 'pre', 'post', 'mpx', 'rf', 'x'):
            step('snap ' + k, lambda k=k: len(tb.snapshot(k)))
        step('cnr', lambda: tb.cnr_since_tune()[-3:])
        log(('state', state(tb)))
        step('stop', lambda: tb.stop())

    for src in ('direct_q', 'native', 'upconverter'):
        SIM.reset(); SIM.fake_clock = True; SIM.vary = True; np.random.seed(1)
        log(('session', 'AM ' + src))
        tb = radio.am.AMReceiver(freq=1080e3, rf_gain=8.7, agc=True, am_source=src,
                                 upconverter_hz=125e6, audio_device='')
        common(tb, 750e3)
        step('readout', lambda: tb.readout())
    for rec in (False, True):
        SIM.reset(); SIM.fake_clock = True; SIM.vary = True; np.random.seed(1)
        log(('session', f'FM rec={rec}'))
        tb = radio.fm.receiver.FMStereoReceiver(
            freq=90.5e6, rf_gain=8.7, agc=True, device_args='numchan=1 rtl=0', audio_device='',
            lmr_gain=1.0, log_path=None, record_iq=(os.devnull if rec else None))
        common(tb, 98.1e6)
        step('freq877', lambda: tb.set_freq(87.7e6))
        for a in ('rds_ps', 'rds_rt', 'rds_pi', 'rds_tp', 'rds_ta', 'rds_af', 'rds_ct', 'rds_ptyn'):
            step('attr ' + a, lambda a=a: getattr(tb, a))
    json.dump(_log.EVENTS, open(out, 'w'), indent=0)
    print(f"{len(_log.EVENTS)} events -> {out}")


def compare(a_path, b_path):
    import difflib
    a, b = json.load(open(a_path)), json.load(open(b_path))
    if a == b:
        print(f"Identical: {len(a)} events, every block, setting, connection and call the same.")
        return 0
    graph = lambda ev: [e for e in ev if e[0] in ('new', 'connect', 'disconnect', 'call', 'firdes')]
    print('Blocks, connections and calls:', 'identical' if graph(a) == graph(b) else 'DIFFERENT')
    A, B = [json.dumps(e) for e in a], [json.dumps(e) for e in b]
    shown = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=A, b=B, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        print(f"--- {op} at event {i1}")
        for x in A[i1:i2][:3]:
            print('  before:', x[:160])
        for x in B[j1:j2][:3]:
            print('  after: ', x[:160])
        shown += 1
        if shown >= 25:
            print('  (more differences not shown)')
            break
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('out', nargs='?', help='JSON file to write')
    ap.add_argument('--code', default=os.path.dirname(HERE), help='checkout to trace (default: this one)')
    ap.add_argument('--compare', nargs=2, metavar=('A', 'B'), help='compare two traces')
    ap.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.compare:
        return compare(*a.compare)
    if not a.out:
        ap.error('give a file to write, or --compare A B')
    if a.child:
        sys.path[:0] = [os.path.join(HERE, 'standin'), os.path.abspath(a.code)]
        record(a.out)
        return 0
    env = dict(os.environ, STANDIN_TRACE='1')        # tracing is switched on before the stand-in loads
    return subprocess.call([sys.executable, os.path.abspath(__file__), os.path.abspath(a.out),
                            '--code', os.path.abspath(a.code), '--child'], env=env)


if __name__ == '__main__':
    sys.exit(main())
