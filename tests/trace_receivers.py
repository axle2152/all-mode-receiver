#!/usr/bin/env python3
"""
trace_receivers.py - prove a change leaves the receivers' signal chains alone

    python3 tests/trace_receivers.py before.json                  # this checkout
    python3 tests/trace_receivers.py after.json --code /other/checkout
    python3 tests/trace_receivers.py --compare before.json after.json

Builds the AM receiver (all three sources), the FM receiver (with and without IQ
recording), the narrow FM receiver (tuner and direct sampling) and the HF receiver (SSB, CW,
sync AM) against the GNU Radio stand-in with tracing on, takes each through a scripted
session (start, tune, filters, tone, stereo, mono, gain, mute, readouts) on a pretend clock,
and writes every event to a JSON file: each block made and its arguments, each connection,
each call on a block, what each step returned, and the receiver's plain attributes at the end.

Record before and after a refactor and compare: identical means every block, setting and
connection is the same. --compare lists any differences; when one trace has a receiver the
other hasn't (one added since), it compares the sessions both have and names the others. For "before", check the old
commit out into another folder (git worktree add ../before HEAD~1) and pass --code.

When a change inserts blocks into a signal path, --lift names their kinds: they're taken out of
the second trace (the wires through each joined from its first input to its output, its other
inputs dropped) and the rest compared, block references renumbered within each session. That
proves the insertion is all that changed; the step results and end attributes that differ are
listed by name:

    python3 tests/trace_receivers.py --compare before.json after.json --lift BlankerBlock
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
    try:
        import radio.nfm as nfm
        nfm.time = FakeTime()
    except ImportError:                   # older code, from before narrow FM
        nfm = None
    try:
        import radio.hf as hf
        import radio.agc
        hf.time = FakeTime()
    except ImportError:                   # older code, from before the HF receiver
        hf = None
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
        if tb.has('nfm_width'):
            step('width', lambda: tb.set_width('NFM'))
        if tb.has('squelch'):
            step('sql', lambda: tb.set_squelch(3))
        if tb.has('hf_mode'):
            step('usb', lambda: tb.set_mode('USB'))
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
        if tb.has('nfm_width'):
            step('width2', lambda: tb.set_width('NFM2'))
        if tb.has('ctcss'):
            step('tsq', lambda: tb.set_tone_squelch(100.0))
        if tb.has('squelch'):
            step('sql0', lambda: tb.set_squelch(0))
        if tb.has('hf_width'):
            step('hfw', lambda: tb.set_width(1800))
        if tb.has('hf_mode'):
            step('cw', lambda: tb.set_mode('CW'))
            step('sam', lambda: tb.set_mode('SAM_L', 6000))
            run(tb, 1.0, 'blend_sam')
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
    for args in (() if nfm is None else ('numchan=1 rtl=0', 'numchan=1 rtl=0,direct_samp=2')):
        SIM.reset(); SIM.fake_clock = True; SIM.vary = True; np.random.seed(1)
        log(('session', f'NFM {args}'))
        tb = nfm.NFMReceiver(freq=146.94e6, rf_gain=29.7, agc=True, device_args=args, audio_device='')
        common(tb, 146.52e6)
        step('readout', lambda: tb.readout())
    for src in (() if hf is None else ('direct_q', 'native', 'upconverter')):
        SIM.reset(); SIM.fake_clock = True; SIM.vary = True; np.random.seed(1)
        log(('session', f'HF {src}'))
        tb = hf.HFReceiver(freq=14.2e6, rf_gain=29.7, agc=True, am_source=src, upconverter_hz=125e6,
                           audio_device='', mode='USB')
        common(tb, 7.185e6)
        step('readout', lambda: tb.readout())
    json.dump(_log.EVENTS, open(out, 'w'), indent=0)
    print(f"{len(_log.EVENTS)} events -> {out}")


def sessions(ev):
    """The events of each receiver session, by its name (events before the first under None)."""
    out, name = {}, None
    for e in ev:
        if e[0] == 'session':
            name = e[1]
        out.setdefault(name, []).append(e)
    return out


def compare(a_path, b_path):
    import difflib
    a, b = json.load(open(a_path)), json.load(open(b_path))
    if a == b:
        print(f"Identical: {len(a)} events, every block, setting, connection and call the same.")
        return 0
    sa, sb = sessions(a), sessions(b)
    both = [n for n in sa if n in sb]
    if set(sa) != set(sb) and all(sa[n] == sb[n] for n in both):
        n_ev = sum(len(sa[n]) for n in both)
        print(f"Identical in every session both have: {n_ev} events, every block, setting, connection "
              f"and call the same.")
        for label, only in (('first', [n for n in sa if n not in sb]), ('second', [n for n in sb if n not in sa])):
            if only:
                print(f"Only in the {label}: " + ', '.join(only))
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


def _endpoint(x):
    if isinstance(x, list) and x and x[0] == 'blk':
        return (x[1], 0)
    if isinstance(x, list) and len(x) == 2 and isinstance(x[0], list) and x[0] and x[0][0] == 'blk':
        return (x[0][1], x[1])
    return ('self', 0)


def _lifted(ev, kinds):
    """A session's blocks, connections and calls with the blocks of these kinds lifted out and
    block numbers counted within the session; and its steps' results and end attributes."""
    gone = {e[1] for e in ev if e[0] == 'new' and e[2] in kinds}
    ids = {}
    for e in ev:
        if e[0] == 'new' and e[1] not in gone:
            ids[e[1]] = len(ids)

    def remap(x):
        if isinstance(x, list):
            if len(x) == 2 and x[0] == 'blk' and isinstance(x[1], int):
                return ['blk', ids.get(x[1], -1 - x[1])]
            return [remap(y) for y in x]
        if isinstance(x, dict):
            return {k: remap(v) for k, v in x.items()}
        return x
    news, calls, edges, rets, state, other = [], [], [], [], {}, []
    for e in ev:
        if e[0] == 'new' and e[1] not in gone:
            news.append(json.dumps([e[2], remap(e[3]), remap(e[4])]))
        elif e[0] == 'call' and e[1] not in gone:
            calls.append(json.dumps([ids.get(e[1], e[1]), e[2], remap(e[3]), remap(e[4])]))
        elif e[0] in ('connect', 'disconnect'):
            chain = [_endpoint(x) for x in e[1]]
            edges += [(e[0], a, b) for a, b in zip(chain, chain[1:])]
        elif e[0] == 'ret':
            rets.append((e[1], json.dumps(e[2:])))
        elif e[0] == 'state':
            state = e[1] if isinstance(e[1], dict) else {'': e[1]}
        elif e[0] not in ('new', 'call'):
            other.append(json.dumps(e))
    for g in gone:                                   # join the wires through each lifted block
        ins = [a for _, a, b in edges if b == (g, 0)]
        outs = [b for _, a, b in edges if a[0] == g]
        edges = [x for x in edges if x[1][0] != g and x[2][0] != g]
        edges += [('connect', a, b) for a in ins for b in outs]
    m = lambda p: (ids.get(p[0], p[0]), p[1])        # noqa: E731
    edges = sorted(json.dumps([k, m(a), m(b)]) for k, a, b in edges)
    return dict(blocks=news, connections=edges, calls=calls, other=other), rets, state, len(gone)


def compare_lifted(a_path, b_path, kinds):
    a, b = json.load(open(a_path)), json.load(open(b_path))
    sa, sb = sessions(a), sessions(b)
    bad = 0
    for name in sa:
        if name not in sb:
            print(f"{name}: only in the first")
            continue
        (ga, ra, sta, _), (gb, rb, stb, n) = _lifted(sa[name], ()), _lifted(sb[name], set(kinds))
        diff = [k for k in ga if ga[k] != gb[k]]
        bad += bool(diff)
        steps = sorted({x[0] for x, y in zip(ra, rb) if x != y} | ({'(count)'} if len(ra) != len(rb) else set()))
        new = sorted(set(stb) - set(sta))
        gone = sorted(set(sta) - set(stb))
        changed = sorted(k for k in sta if k in stb and sta[k] != stb[k])
        print(f"{name}: {n} block{'s' if n != 1 else ''} lifted out; "
              + ('blocks, connections and calls identical' if not diff else 'DIFFERENT: ' + ', '.join(diff))
              + f" ({len(ga['blocks'])} blocks, {len(ga['connections'])} connections, {len(ga['calls'])} calls)")
        for label, names in (('step results differ', steps), ('new attributes', new),
                             ('attributes gone', gone), ('attributes changed', changed)):
            if names:
                print(f"    {label}: {', '.join(names)}")
    for name in sb:
        if name not in sa:
            print(f"{name}: only in the second")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('out', nargs='?', help='JSON file to write')
    ap.add_argument('--code', default=os.path.dirname(HERE), help='checkout to trace (default: this one)')
    ap.add_argument('--compare', nargs=2, metavar=('A', 'B'), help='compare two traces')
    ap.add_argument('--lift', nargs='+', metavar='KIND', help='with --compare: block kinds inserted in the '
                    'second, lifted out before comparing')
    ap.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.compare:
        return compare_lifted(*a.compare, a.lift) if a.lift else compare(*a.compare)
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
