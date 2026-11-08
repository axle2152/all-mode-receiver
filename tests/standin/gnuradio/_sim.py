"""The stand-in's pretend dongle and clock, shared by all its blocks.

SIM.started_at   when the flowgraph started (None = stopped)
SIM.first_after  samples begin this long after start (a slow-opening dongle)
SIM.stop_after   samples stop this long after start (a lost dongle); None = never
SIM.vary         True: the probes' readings change while samples flow;
                 False: they stay put (a steady signal, the case that once fooled the watchdog)
SIM.fake_clock   True: time only moves when a test moves SIM.t (fast, repeatable)
"""
import os
import time as _time


class Sim:
    def __init__(self):
        self.fake_clock = False
        self.t = 1000.0
        self.reset()

    def reset(self):
        self.started_at = None
        self.first_after = 0.0
        stop = os.environ.get('STANDIN_STOP_AFTER')
        self.stop_after = float(stop) if stop else None
        self.rate = 2_400_000
        self.wb_value = 0.01 + 0.0j
        self.vary = False

    def now(self):
        return self.t if self.fake_clock else _time.monotonic()

    def count(self):
        """Samples the pretend dongle has delivered so far."""
        if self.started_at is None:
            return 0
        el = self.now() - self.started_at - self.first_after
        if self.stop_after is not None:
            el = min(el, self.stop_after - self.first_after)
        return int(max(0.0, el) * self.rate)


SIM = Sim()


class FakeTime:
    """Drop-in for the time module inside a receiver, following SIM's clock."""

    def monotonic(self):
        return SIM.now()

    def sleep(self, s):
        if SIM.fake_clock:
            SIM.t += s
        else:
            _time.sleep(s)

    def __getattr__(self, name):
        return getattr(_time, name)
