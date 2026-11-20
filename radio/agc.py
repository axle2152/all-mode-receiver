"""
radio/agc.py - the HF receiver's AGC (SSB, CW, synchronous AM)
===============================================================
What a good communications receiver's AGC does, on the complex signal after the sideband filter
(whose magnitude is the signal's envelope), giving the audio (the real part) at a steady level:

  attack   instant, and early: the gain is worked out one step (1 ms) ahead of the audio, so it
           is already down when a syllable or a key-down arrives (no thump, no clipping)
  hang     after the gain comes down it holds there (between words on SSB, between characters
           on CW), so the noise doesn't swell up in every pause
  decay    then it recovers at so many dB a second
  ceiling  the most gain it may use, set by the receiver from the band's measured noise, so an
           empty frequency plays its noise well below a station rather than at full level

AGCCore is the arithmetic (numpy only, tested without GNU Radio in tests/test_agc.py); AGCBlock
puts it in a flowgraph. The audio comes out STEP * 2 samples (2 ms at 48 kHz) late.
"""

import threading

import numpy as np

try:
    from gnuradio import gr
except ImportError:                      # the arithmetic still works for testing
    gr = None


class AGCCore:
    """The AGC's arithmetic. process(z) takes complex samples and returns as many audio samples
    (the real part, at the AGC's gain), STEP * 2 samples late."""

    def __init__(self, rate, ref=0.5, hang_s=0.3, decay_db_s=15.0, max_gain=1e4, step_s=0.001):
        self.rate = float(rate)
        self.step = max(1, int(round(rate * step_s)))
        self.dt = self.step / self.rate
        self.ref = float(ref)
        self.max_gain = float(max_gain)
        self.set_speed(hang_s, decay_db_s)
        self.gain = float(max_gain)
        self._hang_left = 0.0
        self._fifo = np.zeros(0, np.complex64)
        self._out = np.zeros(2 * self.step, np.float32)        # the look-ahead's latency
        self._lock = threading.Lock()

    def set_speed(self, hang_s, decay_db_s):
        """How long the gain holds after coming down, and how fast it recovers then."""
        self.hang_s = float(hang_s)
        self._up = 10 ** (float(decay_db_s) * self.dt / 20)     # gain factor per step

    def set_max_gain(self, g):
        with self._lock:
            self.max_gain = max(float(g), 1e-6)

    def reset(self):
        """A retune: start from the ceiling (the next signal brings it down at once)."""
        with self._lock:
            self.gain = self.max_gain
            self._hang_left = 0.0

    def process(self, z):
        z = np.asarray(z, np.complex64)
        n = len(z)
        if n == 0:
            return np.zeros(0, np.float32)
        st = self.step
        with self._lock:
            fifo = np.concatenate((self._fifo, z))
            k = (len(fifo) - st) // st                  # steps that have their look-ahead step
            out = [self._out]
            if k > 0:
                env = np.abs(fifo[:(k + 1) * st]).reshape(k + 1, st).max(axis=1)
                g, hang, gmax, up = self.gain, self._hang_left, self.max_gain, self._up
                gains = np.empty(k + 1)
                gains[0] = g
                for i in range(k):
                    peak = max(env[i], env[i + 1])       # this step and the one after it
                    target = min(gmax, self.ref / peak) if peak > 0 else gmax
                    if target <= g * 1.0001:             # attack (down at once), or the signal
                        g, hang = min(g, target), self.hang_s    # still holding it down: hold
                    elif hang > 0:
                        hang -= self.dt
                    else:
                        g = min(target, g * up)
                    gains[i + 1] = g
                # each step's gain slides from the last step's to its own: no clicks
                ramp = np.linspace(0.0, 1.0, st, endpoint=False)
                gs = (gains[:-1, None] + (gains[1:] - gains[:-1])[:, None] * ramp[None, :]).ravel()
                out.append((fifo[:k * st].real * gs).astype(np.float32))
                self.gain, self._hang_left = g, hang
                fifo = fifo[k * st:]
            self._fifo = fifo
            buf = np.concatenate(out)
            self._out = buf[n:]
            return buf[:n]


if gr is not None:
    class AGCBlock(gr.sync_block):
        """complex in -> audio out, through AGCCore."""

        def __init__(self, rate, **kw):
            gr.sync_block.__init__(self, name='hf_agc', in_sig=[np.complex64], out_sig=[np.float32])
            self.core = AGCCore(rate, **kw)

        def work(self, input_items, output_items):
            y = self.core.process(input_items[0])
            output_items[0][:len(y)] = y
            return len(y)
else:
    AGCBlock = None


def max_gain_for(noise_rms, ref, below_db):
    """The ceiling that puts noise of this RMS (complex, in the passband) below_db under ref."""
    return ref * 10 ** (-below_db / 20) / max(noise_rms, 1e-9)


__all__ = ['AGCCore', 'AGCBlock', 'max_gain_for']
