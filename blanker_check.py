#!/usr/bin/env python3
"""
blanker_check.py - what noise is on this frequency, and what do the blankers do with it?

    python3 blanker_check.py --freq 1230e3                # AM receiver, 5 s from the dongle
    python3 blanker_check.py --freq 7.185e6 --band hf     # the SSB / CW receiver
    python3 blanker_check.py --freq 1230e3 --secs 10
    python3 blanker_check.py --replay blanker_check.cs8   # a saved recording, no dongle

Close the tuner first (only one program can have the dongle), then give the frequency where
you hear the noise. It records the dongle's samples, exactly as the receiver gets them (before
any filter or blanker), to blanker_check.cs8 beside this file (about 2 MB a second), and then,
without the dongle:

  - the strongest stations in the 960 kHz the dongle sends, against the one tuned
  - the pulses at that full bandwidth: how many a second stand 12, 15, 18 and 24 dB out of the
    band (over what the noise blanker can't predict), how long they are and how they're
    spaced (power-line noise comes 8.3 ms apart where the mains is 60 Hz, 10 ms apart at 50 Hz)
  - each blanker setting run over the same samples: the pulses it patched, and the crackle
    left in the channel you'd hear

Paste what it prints into the chat. If you can, attach blanker_check.cs8 too, so the noise
itself can be played through the receiver and looked at.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RATE = 960_000
SKIP_S = 0.5                     # the dongle's first moments aren't the band


# ---------------------------------------------------------------------------- recording
def record(a, path, make_source=None):
    """secs of the dongle's samples as the receiver gets them, as interleaved int8 (lossless for
    an RTL), and the receiver's own readings meanwhile."""
    from gnuradio import gr, blocks
    import radio.addons
    radio.addons._entry_points = lambda: []          # add-ons don't change the samples
    if a.band == 'am':
        from radio.am import AMReceiver as Base
    else:
        from radio.hf import HFReceiver as Base

    class Rx(Base):
        def _make_audio_sink(self, rate, device):
            return blocks.null_sink(gr.sizeof_float)
        if make_source is not None:
            def _make_source(self, args):
                return make_source(self)

    kw = dict(freq=a.freq, am_source=a.am_source)
    if a.gain is not None:
        kw.update(rf_gain=a.gain, agc=False)
    tb = Rx(**kw)
    n = int((a.secs + SKIP_S) * tb.samp_rate)
    tb.rec_head = blocks.head(gr.sizeof_gr_complex, n)
    tb.rec_conv = blocks.complex_to_interleaved_char(False, 128.0)
    tb.rec_sink = blocks.file_sink(gr.sizeof_char, path, False)
    tb.connect(tb.src, tb.rec_head, tb.rec_conv, tb.rec_sink)
    tb.start()
    t0, seen = time.monotonic(), []
    while time.monotonic() - t0 < a.secs + SKIP_S + 10:
        try:
            tb.update_blend()
        except Exception as e:
            seen.append(f'{type(e).__name__}: {e}')
        if tb.rec_head.nitems_written(0) >= n:
            break
        time.sleep(0.02)
    readings = dict(level_dbfs=round(tb.channel_dbfs, 1), quality_db=round(tb.cnr_db, 1),
                    clip_pct=round(tb.clip_pct, 3))
    tb.stop()
    tb.wait()
    tb.rec_sink.close()
    got = os.path.getsize(path) // 2 if os.path.exists(path) else 0
    meta = dict(band=a.band, freq=a.freq, rate=int(tb.samp_rate), lo_offset=float(tb.LO_OFFSET),
                am_source=a.am_source, readings=readings, errors=seen[:3])
    with open(path + '.json', 'w') as f:
        json.dump(meta, f, indent=1)
    if got < n:
        print(f'Only {got / tb.samp_rate:.1f} s of the {a.secs + SKIP_S:.1f} s came from the dongle.')
    return meta


# ---------------------------------------------------------------------------- analysis
def load(path):
    raw = np.fromfile(path, np.int8)
    raw = raw[:len(raw) // 2 * 2]
    clip = float(np.mean((np.abs(raw[0::2].astype(np.int16)) >= 127)
                         | (np.abs(raw[1::2].astype(np.int16)) >= 127)))
    x = (raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)) / 128.0
    return x.astype(np.complex64), clip


def lowpass(fc, fs, n, beta=8.0):
    m = (n - 1) / 2
    h = 2 * fc / fs * np.sinc(2 * fc / fs * (np.arange(n) - m)) * np.kaiser(n, beta)
    return (h / np.sum(h)).astype(np.float32)


def fftconv(x, h):
    """x filtered by h, centred (no delay), by FFT in pieces."""
    n, m = len(x), len(h)
    step = 1 << 18
    nfft = 1 << int(math.ceil(math.log2(step + m - 1)))
    H = np.fft.fft(h, nfft)
    out = np.zeros(n + m - 1, np.complex64)
    for i in range(0, n, step):
        seg = x[i:i + step]
        y = np.fft.ifft(np.fft.fft(seg, nfft) * H)[:len(seg) + m - 1]
        out[i:i + len(y)] += y.astype(np.complex64)
    return out[(m - 1) // 2:(m - 1) // 2 + n]


def to_channel(y, off_hz, fs=RATE):
    """The 48 kHz channel around off_hz, as the receivers make it: 40 kHz then 14 kHz wide."""
    ph = (np.arange(len(y), dtype=np.float64) * (off_hz / fs)) % 1.0
    z = y * np.exp(-2j * np.pi * ph).astype(np.complex64)
    z = fftconv(z, lowpass(40e3, fs, 101))[::10]
    return fftconv(z, lowpass(14e3, fs / 10, 63))[::2]


def crackle(ch, band, fs=48000):
    """How much louder the loudest 1% of milliseconds are than the median millisecond, in the
    channel you'd hear (the carrier and slow fades taken out), and how many clicks a second
    (runs of milliseconds 15 dB over the median)."""
    bw = 5000.0 if band == 'am' else 3000.0
    s = fftconv(ch, lowpass(bw, fs, 97))
    k = fs // 100
    c = np.cumsum(np.concatenate(([0], s)))
    s = s[k:] - (c[k:-1] - c[:-k - 1])[:len(s) - k] / k if len(s) > 2 * k else s
    w = fs // 1000
    p = np.mean(np.abs(s[:len(s) // w * w].reshape(-1, w)) ** 2, axis=1)
    med = float(np.median(p)) or 1e-20
    top = np.sort(p)[-max(1, len(p) // 100):]
    loud = p > 10 ** 1.5 * med
    clicks = int(np.sum(np.diff(np.concatenate(([0], loud.view(np.int8)))) == 1))
    return 10 * math.log10(float(np.mean(top)) / med), clicks / (len(p) / 1000.0)


def stations(x, meta, n=4096):
    """The strongest carriers in the recording: (dial Hz, dB re the strongest)."""
    w = np.hanning(n).astype(np.float32)
    segs = min(400, len(x) // n)
    idx = np.linspace(0, len(x) - n, segs).astype(int)
    P = np.zeros(n)
    for i in idx:
        P += np.abs(np.fft.fft(x[i:i + n] * w)) ** 2
    P = np.fft.fftshift(P / segs)
    f = np.fft.fftshift(np.fft.fftfreq(n, 1 / meta['rate']))
    floor = float(np.median(P))
    peaks = [i for i in range(3, n - 3) if P[i] == P[i - 3:i + 4].max() and P[i] > 100 * floor
             and abs(f[i]) < 0.45 * meta['rate']]
    peaks.sort(key=lambda i: -P[i])
    kept = []
    for i in peaks:
        if all(abs(f[i] - f[j]) > 5e3 for j in kept):
            kept.append(i)
    top = P[kept[0]] if kept else 1.0
    tuned = f.searchsorted(-meta['lo_offset'])
    near = slice(max(0, tuned - 3), tuned + 4)
    tuned_db = 10 * math.log10(float(P[near].max()) / top) if kept else None
    dial = lambda i: meta['freq'] + meta['lo_offset'] + f[i]      # noqa: E731
    return [(dial(i), 10 * math.log10(P[i] / top)) for i in kept[:6]], tuned_db, \
        10 * math.log10(floor / top) if kept else None


def fmt_hz(hz):
    return f'{hz / 1e3:.0f} kHz' if hz < 30e6 else f'{hz / 1e6:.3f} MHz'


def run_setting(x, meta, label, nb_k, ib, consts):
    """One blanker setting over the recording: (pulses a second, % of the time, crackle, clicks,
    the noise blanker's events)."""
    from radio.blanker import BlankerCore
    band, secs = meta['band'], len(x) / RATE
    y, events, patched, per_s = x, [], 0.0, 0.0
    hf_ib = band == 'hf' and ib
    if nb_k is not None or hf_ib:
        core = BlankerCore(RATE, pre=consts['NB_PRE'], post=consts['NB_POST'], max_run=consts['NB_MAX'],
                           order=consts['NB_ORDER'], backoff=True)
        core.set_k(nb_k if nb_k is not None else consts['IB_K'], consts['IB_K'] if hf_ib else None)
        core.events = events
        y = np.concatenate([core.process(x[i:i + RATE // 100]) for i in range(0, len(x), RATE // 100)])
        patched, per_s = 100.0 * core.n_blanked / len(x), core.n_pulses / secs
    ch = to_channel(y, -meta['lo_offset'])
    ib_s = 0.0
    if band == 'am' and ib:
        core = BlankerCore(48000, pre=consts['IB_PRE'], post=consts['IB_POST'], max_run=consts['IB_MAX'],
                           detect='level', order=consts['IB_ORDER'])
        core.set_k(consts['IB_K'])
        ch = np.concatenate([core.process(ch[i:i + 480]) for i in range(0, len(ch), 480)])
        ib_s = core.n_pulses / secs
    cr, clicks = crackle(ch[48000 // 10:], band)
    return dict(label=label, per_s=per_s, patched=patched, ib_s=ib_s, crackle=cr, clicks=clicks,
                events=events)


def census(events, secs):
    """The pulses the most sensitive setting found, by height: a second each; widths; spacing."""
    if not events:
        return None
    peaks = np.array([e[2] for e in events])
    widths = np.array([e[1] for e in events])
    starts = np.array([e[0] for e in events])
    by = {db: float(np.sum(peaks >= 10 ** (db / 20))) / secs for db in (12, 15, 18, 24)}
    spacing = None
    big = starts[peaks >= 10 ** (15 / 20)]
    if len(big) > 10:
        d = np.diff(big) / RATE * 1e3                       # ms
        d = d[(d > 0.5) & (d < 50)]
        if len(d) > 10:
            h, edges = np.histogram(d, bins=np.arange(0.5, 50.25, 0.25))
            i = int(np.argmax(h))
            if h[i] >= max(5, 0.15 * len(d)):
                spacing = (edges[i] + 0.125, h[i] / len(d))
    return dict(by=by, width=float(np.median(widths)), tallest=20 * math.log10(float(peaks.max())),
                spacing=spacing)


def analyse(path, meta):
    if meta['band'] == 'am':
        from radio.am import AMReceiver as R
        consts = dict(NB_K=R.NB_K, NB_PRE=R.NB_PRE, NB_POST=R.NB_POST, NB_MAX=R.NB_MAX,
                      NB_ORDER=R.NB_ORDER, IB_K=R.IB_K, IB_PRE=R.IB_PRE, IB_POST=R.IB_POST,
                      IB_MAX=R.IB_MAX, IB_ORDER=R.IB_ORDER)
    else:
        from radio.hf import HFReceiver as R
        consts = dict(NB_K=R.NB_K, NB_PRE=R.NB_PRE, NB_POST=R.NB_POST, NB_MAX=R.NB_MAX,
                      NB_ORDER=R.NB_ORDER, IB_K=R.IB_K)
    x, clip = load(path)
    x = x[int(SKIP_S * RATE):]
    secs = len(x) / RATE
    if secs < 1:
        print('The recording is too short to say anything (under a second).')
        return
    print(f"\n=== {fmt_hz(meta['freq'])}, {meta['band'].upper()} receiver, {secs:.1f} s ===")
    r = meta.get('readings') or {}
    if r:
        print(f"The receiver read: level {r.get('level_dbfs')} dBFS, "
              f"{'C/N' if meta['band'] == 'am' else 'S/N'} {r.get('quality_db')} dB")
    med = float(np.median(np.abs(x[::4])))
    print(f"The dongle's samples: median {20 * math.log10(max(med, 1e-9)):.1f} dBFS, "
          f"{100 * clip:.3f}% at full scale (clipping)")
    st, tuned_db, floor_db = stations(x, meta)
    if st:
        print('Strongest stations in the 960 kHz the dongle sends (dB against the strongest):')
        for hz, db in st:
            print(f"   {fmt_hz(hz):>12s}  {db:6.1f} dB")
        print(f"   tuned ({fmt_hz(meta['freq'])}): {tuned_db:.1f} dB; the noise floor {floor_db:.1f} dB")

    settings = [('off', None, False), ('impulse', None, True)] + \
        [(f'impulse + noise {lv.upper()}', consts['NB_K'][lv], True) for lv in ('low', 'med', 'high')]
    res = [run_setting(x, meta, lab, k, ib, consts) for lab, k, ib in settings]

    cen = census(res[-1]['events'], secs)
    print('\nPulses at full bandwidth, standing out of the band (over what the noise blanker can\'t predict):')
    if cen is None:
        print('   none found')
    else:
        print('   ' + ', '.join(f"over {db} dB: {n:.0f} a second" for db, n in cen['by'].items()))
        print(f"   patched {cen['width']:.0f} samples ({cen['width'] / RATE * 1e6:.0f} us) each, typically; "
              f"the tallest {cen['tallest']:.0f} dB over")
        if cen['spacing']:
            ms, share = cen['spacing']
            what = ''
            for hz in (60, 50):
                for mult, name in ((2, 'twice'), (1, 'once')):
                    if abs(ms - 1000 / (hz * mult)) < 0.3:
                        what = f": {name} a cycle of {hz} Hz mains, so power-line noise"
            print(f"   most often {ms:.2f} ms apart ({100 * share:.0f}% of them){what}")

    print('\nEach setting over the same samples (crackle: the loudest 1% of milliseconds in the channel')
    print('over the median one; lower is cleaner):')
    print(f"   {'setting':26s}{'patched':>22s}{'crackle':>10s}{'clicks/s':>10s}")
    for d in res:
        p = ''
        if d['per_s']:
            p = f"{d['per_s']:.0f}/s, {d['patched']:.2f}%"
        if d['ib_s']:
            p = (p + ', ' if p else '') + f"{d['ib_s']:.0f}/s in the channel"
        print(f"   {d['label']:26s}{p or '-':>22s}{d['crackle']:8.1f} dB{d['clicks']:9.1f}")

    off, best = res[0], min(res[1:], key=lambda d: d['crackle'])
    gain = off['crackle'] - best['crackle']
    print('\n=== In plain words ===')
    few = cen is None or cen['by'][15] < 1
    if few:
        print('Hardly any pulses stand clear of the band here, so there is little for a blanker to\n'
              'catch: the noise you hear is more likely hiss or a steady buzz, which noise reduction\n'
              '(next on the list) is for.')
    elif gain >= 3:
        print(f"'{best['label']}' takes the crackle down {gain:.0f} dB.")
    else:
        print('There are pulses and the blanker patches them, but the crackle in the channel hardly\n'
              'changes: they may be longer than it patches, or the noise heard is something else.')
    if st and tuned_db is not None and tuned_db < -25:
        print(f'Stations up to {-tuned_db:.0f} dB stronger than this one share the dongle\'s 960 kHz. The noise\n'
              'blanker looks past them (it predicts them), but they may be pushing the dongle hard.')
    if clip > 0.001:
        print(f'{100 * clip:.2f}% of the samples are at full scale: the dongle is overloaded, and its clipping\n'
              'makes noise of its own that no blanker removes.')
    print('\nPlease paste everything above into the chat' +
          (f', and attach {os.path.basename(path)} if you can.' if os.path.exists(path) else '.'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('--freq', type=float, default=None, help='Hz, where you hear the noise')
    ap.add_argument('--band', choices=('am', 'hf'), default='am',
                    help='am: the AM receiver (broadcasts); hf: the SSB / CW receiver')
    ap.add_argument('--secs', type=float, default=5.0)
    ap.add_argument('--am-source', default='direct_q', choices=('direct_q', 'native', 'upconverter'))
    ap.add_argument('--gain', type=float, default=None, help='tuner gain in dB (not for direct sampling)')
    ap.add_argument('--replay', default=None, help='a saved blanker_check.cs8 in place of the dongle')
    a = ap.parse_args()
    if a.replay:
        path = a.replay
        try:
            with open(path + '.json') as f:
                meta = json.load(f)
        except OSError:
            meta = dict(band=a.band, freq=a.freq or 1080e3, rate=RATE, lo_offset=20e3, readings={})
    else:
        if a.freq is None:
            ap.error('give the frequency where you hear the noise: --freq 1230e3')
        path = os.path.join(HERE, 'blanker_check.cs8')
        print(f"Recording {a.secs:g} s of {fmt_hz(a.freq)} ({a.band.upper()} receiver) to {path} ...")
        meta = record(a, path)
    analyse(path, meta)


if __name__ == '__main__':
    main()
