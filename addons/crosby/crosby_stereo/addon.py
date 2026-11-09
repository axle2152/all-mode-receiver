"""
crosby_stereo/addon.py - Crosby FM-FM compatible stereo (1958) for the FM receiver
=================================================================================
Crosby's system put L+R on the main channel and L-R on a 50 kHz subcarrier that is itself
frequency modulated (+/-25 kHz). There is no pilot: the subcarrier, a constant-amplitude
carrier present whenever the station is in stereo, is its own stereo indicator. The main
channel runs at 50% of full deviation while the station stereocasts (the system's 6 dB mono
penalty), so the receiver raises L+R by 6 dB while this add-on's stereo is on.

    composite = 0.5 (L+R)/2  +  0.5 cos(2 pi 50 kHz t + phi(t)),   dphi/dt = 2 pi 25 kHz (L-R)/2

Signal path (at the receiver's composite rate, 240 kHz):
  composite -> to 50 kHz = DC -> subcarrier channel filter, 19-81 kHz (even length)
            -> discriminator (+/-25 kHz = (L-R)/2) -> the receiver's own 15 kHz low-pass
            -> delay -> the receiver's matrix, lined up with L+R
The channel filter is an even length on purpose: its half-sample delay cancels the
discriminator's (which measures the frequency between two samples), so the delay that
lines L-R up with L+R is a whole number of samples.

Is it a Crosby station? Only when, for 0.4 s running:
  - the receiver hears no 19 kHz pilot (a pilot means FM stereo as everyone knows it)
  - there is a carrier in the 19-81 kHz band at a real injection (level)
  - it has a constant envelope, so it's FM and not noise or a DSB L-R (circle)
  - it is centred on 50 kHz (a 67 kHz SCA has the first two; this tells it apart)
Once found, it holds until a retune, a pilot, or a second of absence while the signal is
good enough to have seen it (on a weak signal it can't tell, so the level stays put).

How much L-R to blend in follows the receiver's signal quality: full stereo from
FULL_DB, mono by MONO_DB. Crosby is quieter than pilot stereo above its threshold, but
the subcarrier's discriminator falls apart below it, around 10 dB.
"""
import math

import numpy as np
from gnuradio import gr, blocks, analog, filter


def smoothstep(x, lo, hi):
    t = min(max((x - lo) / (hi - lo), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class Addon:
    BANDS = ('FM',)
    NAME = 'CROSBY'

    # ---- the 1958 signal ----
    SUB_HZ         = 50e3       # subcarrier centre
    SUB_DEV_HZ     = 25e3       # its deviation for (L-R)/2 = 1
    SUB_INJ        = 0.5        # its share of full deviation (0 dB on the level reading)
    MAIN_GAIN      = 2.0        # L+R rides at 50% while the station is in stereo
    # ---- the subcarrier channel: 19-81 kHz, nothing below 17 or above 83 kHz ----
    SUB_HALF_BW    = 31e3       # -6 dB half-width
    SUB_WIDTH      = 4e3        # transition
    MPX_TOP_HZ     = 83e3       # the highest composite frequency the subcarrier uses
    # ---- is it there? ----
    WINDOW_S       = 0.02       # measurement windows
    AVG_S          = 0.25       # readings averaged over about this long
    LEVEL_MIN_DB   = -9.0       # injection at least 18% (50% reads 0 dB)
    CIRCLE_MIN_DB  = 6.0        # a clean subcarrier reads 15-26 dB, noise -10, DSB L-R 3-4
    OFFSET_MAX_HZ  = 3000.0     # within this of 50 kHz (noise leans +7 kHz, an SCA at 67 +17)
    FIND_S         = 0.4        # all of that this long to call it Crosby
    LOSE_S         = 1.0        # none of it this long (and the signal good enough) to let go
    SEEN_CNR_DB    = 12.0       # below this signal quality an absence proves nothing
    SETTLE_S       = 0.15       # after a retune, samples from the old station still arrive
    # ---- how much stereo (the receiver's signal quality, dB) ----
    FULL_DB        = 15.0
    MONO_DB        = 10.0

    def __init__(self, rx):
        if rx.BAND != 'FM':
            raise ValueError('Crosby stereo is an FM system')
        self.rx = rx
        fs = rx.mpx_rate
        full = rx.MPX_FULL
        self._win = int(round(fs * self.WINDOW_S))

        # Subcarrier channel, even length (see the module notes). Its gain makes a full 50%
        # subcarrier read |z| = 1, allowing for the main discriminator's roll-off at 50 kHz.
        n = int(round(3.3 * fs / self.SUB_WIDTH))
        n += n % 2
        k = np.arange(n) - (n - 1) / 2
        fc = self.SUB_HALF_BW / fs
        h = 2 * fc * np.sinc(2 * fc * k) * np.hamming(n)
        droop = float(np.sinc(self.SUB_HZ / fs))
        h *= 1.0 / (np.sum(h) * self.SUB_INJ * full / 2 * droop)
        lp = list(rx.stereo_lowpass())               # the receiver's own: the channels match
        align = (len(lp) - 1) - n // 2 - (len(lp) - 1) // 2
        if align < 0:
            raise ValueError(f'the receiver lines L+R up {len(lp) - 1} samples after the tap; '
                             f'the subcarrier filter alone takes {n // 2}')

        self.to_c   = blocks.float_to_complex(1)
        self.mix    = blocks.rotator_cc(-2 * math.pi * self.SUB_HZ / fs)
        self.chan   = filter.fft_filter_ccc(1, [complex(v) for v in h])
        self.disc   = analog.quadrature_demod_cf(fs / (2 * math.pi * self.SUB_DEV_HZ))
        self.lpf    = filter.fft_filter_fff(1, [full * v for v in lp])   # (L-R)/2 x MPX_FULL
        self.align  = blocks.delay(gr.sizeof_float, align)
        # what's in the subcarrier channel, 20 ms at a time: |z|^2, |z|^4, frequency
        self.mag2   = blocks.complex_to_mag_squared(1)
        self.mag4   = blocks.multiply_ff(1)
        self.int2   = blocks.integrate_ff(self._win)
        self.int4   = blocks.integrate_ff(self._win)
        self.pack   = blocks.float_to_complex(1)
        self.probe  = blocks.probe_signal_c()
        self.intf   = blocks.integrate_ff(self._win)
        self.fprobe = blocks.probe_signal_f()

        c = rx.connect
        c(rx.addon_tap(), self.to_c, self.mix, self.chan, self.disc, self.lpf, self.align)
        c(self.chan, self.mag2)
        c(self.mag2, (self.mag4, 0))
        c(self.mag2, (self.mag4, 1))
        c(self.mag2, self.int2, (self.pack, 0))
        c(self.mag4, self.int4, (self.pack, 1))
        c(self.pack, self.probe)
        c(self.disc, self.intf, self.fprobe)
        self.reset()

    def stereo_decoder(self):
        return {'out': (self.align, 0), 'main_gain': self.MAIN_GAIN, 'mpx_top': self.MPX_TOP_HZ}

    def reset(self):
        self._m2 = self._m4 = self._f = None
        self._last = None
        self._t = None
        self._settle = None
        self._ok_s = self._bad_s = 0.0
        self.found = False
        self.quality = 0.0
        self.level_db = self.circle_db = None
        self.offset_hz = None

    # ------------------------------------------------------------------ update
    def update(self, now):
        rx = self.rx
        dt = 0.0 if self._t is None else min(max(now - self._t, 0.0), 0.2)
        self._t = now
        if self._settle is None:
            self._settle = now + self.SETTLE_S
        p, f = self.probe.level(), self.fprobe.level()
        if p != self._last and now >= self._settle and p.real > 0:
            self._last = p
            m2, m4, fm = p.real / self._win, p.imag / self._win, f / self._win
            if self._m2 is None:
                self._m2, self._m4, self._f = m2, m4, fm
            else:
                a = 1 - math.exp(-self.WINDOW_S / self.AVG_S)
                self._m2 += a * (m2 - self._m2)
                self._m4 += a * (m4 - self._m4)
                self._f += a * (fm - self._f)
            m2, m4 = self._m2, self._m4
            s = math.sqrt(max(2 * m2 * m2 - m4, 0.0))
            self.level_db = 10 * math.log10(max(m2, 1e-15))
            self.circle_db = min(max(10 * math.log10(max(s, 1e-15) / max(m2 - s, 1e-15)), -10.0), 40.0)
            self.offset_hz = self._f * self.SUB_DEV_HZ
        pilot = bool(rx.pilot_present)
        ok = (not pilot and self.level_db is not None
              and self.level_db >= self.LEVEL_MIN_DB
              and self.circle_db >= self.CIRCLE_MIN_DB
              and abs(self.offset_hz) <= self.OFFSET_MAX_HZ)
        if ok:
            self._ok_s, self._bad_s = self._ok_s + dt, 0.0
        else:
            self._ok_s, self._bad_s = 0.0, self._bad_s + dt
        if not self.found and self._ok_s >= self.FIND_S:
            self.found = True
        elif self.found and (pilot or (self._bad_s >= self.LOSE_S and rx.cnr_db >= self.SEEN_CNR_DB)):
            self.found = False
        self.quality = smoothstep(rx.cnr_db, self.MONO_DB, self.FULL_DB) if self.found else 0.0
        return {'stereo': self.found, 'stereo_quality': self.quality, 'synced': self.found}

    # --------------------------------------------------------- for the window
    def injection_pct(self):
        """The subcarrier's share of full deviation, percent (50 for the 1958 system)."""
        if self.level_db is None:
            return 0.0
        return 100 * self.SUB_INJ * 10 ** (self.level_db / 20)

    def why_not(self):
        """Why the station isn't taken for Crosby stereo right now, in a few words ('' when
        it is): the first of the tests that fails."""
        if self.found:
            return ''
        if self.level_db is None:
            return 'listening for a 50 kHz subcarrier'
        if self.rx.pilot_present:
            return 'a 19 kHz pilot: ordinary stereo, not Crosby'
        inj = self.injection_pct()
        if self.level_db < self.LEVEL_MIN_DB:
            return 'no subcarrier' if inj < 5 else f'subcarrier too weak ({inj:.0f}%, needs 18%)'
        if abs(self.offset_hz) > self.OFFSET_MAX_HZ:
            return f'not centred on 50 kHz ({(self.SUB_HZ + self.offset_hz) / 1e3:.1f} kHz)'
        if self.circle_db < self.CIRCLE_MIN_DB:
            return f'not a steady FM subcarrier (C/N {self.circle_db:.1f} dB, needs 6)'
        return 'found, checking'

    def readout(self):
        if self.level_db is None:
            return 'Crosby  listening for a 50 kHz subcarrier'
        if self.found:
            state = 'STEREO' if self.quality > 0 else 'stereo station, blended to mono'
        else:
            state = self.why_not()
        return (f"Crosby  {state}   subcarrier {self.injection_pct():3.0f}% at "
                f"{(self.SUB_HZ + self.offset_hz) / 1e3:4.1f} kHz, C/N {self.circle_db:4.1f} dB")

    def meter(self):
        if not self.found:
            return ('Crosby subcarrier', 0.0, 'none')
        inj = self.injection_pct()
        return ('Crosby subcarrier', min(1.0, inj / (100 * self.SUB_INJ)), f"{inj:3.0f}%")
