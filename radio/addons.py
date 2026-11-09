"""
radio/addons.py - optional decoders that aren't part of this repository
=======================================================================
An add-on is a separately installed Python package that plugs a decoder into a receiver.
If none is installed, nothing here does anything and nothing is printed. If one is
installed but fails to load or to start, the receiver prints one line in the terminal and
carries on without it. See docs/ADDONS.md for how to write one.

Finding add-ons: an installed package declares itself in its pyproject.toml,

    [project.entry-points."all_mode_receiver.addons"]
    mydecoder = "my_package.radio_addon:Addon"

and the entry point names the add-on class. The class says which bands it serves
(BANDS = ('AM',)); a receiver of that band creates it once its own blocks are built.
On FM an add-on can also bring a stereo system of its own (stereo_decoder()), which the
receiver plays when the station has no 19 kHz pilot and the add-on says its stereo is on.
"""
from importlib import metadata

GROUP = 'all_mode_receiver.addons'

# Station data an add-on can report from update(), and the receiver attribute each one
# fills on AM (the same fields RDS fills on FM, so the window shows them the same way).
# On FM the receiver's own RDS keeps those fields; an add-on's data stays in rx.station.
STATION_FIELDS = {
    'ps': 'rds_ps', 'rt': 'rds_rt', 'pi': 'rds_pi', 'call': 'rds_call', 'pty': 'rds_pty',
    'synced': 'rds_synced', 'tp': 'rds_tp', 'ta': 'rds_ta', 'af': 'rds_af', 'ct': 'rds_ct',
    'rate': 'rds_rate', 'bler': 'rds_bler', 'ms': 'rds_ms', 'ptyn': 'rds_ptyn',
}
# ...and what each field holds when nothing has been decoded
STATION_DEFAULTS = {
    'rds_ps': '', 'rds_rt': '', 'rds_pi': None, 'rds_call': None, 'rds_pty': None,
    'rds_synced': False, 'rds_tp': None, 'rds_ta': None, 'rds_af': [], 'rds_ct': None,
    'rds_rate': 0.0, 'rds_bler': 0.0, 'rds_ms': None, 'rds_ptyn': None,
}


def _entry_points():
    try:
        return list(metadata.entry_points(group=GROUP))
    except Exception:
        return []


def classes_for(band):
    """(name, class) of every installed add-on that serves this band. Ones that can't be
    loaded are reported in one line each and skipped."""
    out = []
    for ep in _entry_points():
        try:
            cls = ep.load()
        except Exception as e:
            print(f"Add-on '{ep.name}' not used: {type(e).__name__}: {e}")
            continue
        if band in tuple(getattr(cls, 'BANDS', ())):
            out.append((ep.name, cls))
    return out


class Attached:
    """One add-on running on one receiver. Errors from the add-on stop the add-on, never
    the receiver: the first is reported in one line and the add-on is dropped."""

    def __init__(self, name, obj):
        self.name, self.obj, self.alive = name, obj, True
        self.label = str(getattr(type(obj), 'NAME', name))     # for lamps and messages

    def _call(self, method, *args):
        if not self.alive:
            return None
        fn = getattr(self.obj, method, None)
        if fn is None:
            return None
        try:
            return fn(*args)
        except Exception as e:
            self.alive = False
            print(f"Add-on '{self.name}' stopped: {type(e).__name__}: {e}")
            return None

    def reset(self):
        self._call('reset')

    def update(self, now):
        return self._call('update', now)

    def readout(self):
        return self._call('readout') or ''

    def meter(self):
        """(label, 0..1, text) for a meter in the Advanced window, or None."""
        return self._call('meter')

    def stereo_decoder(self):
        """FM: the add-on's stereo decoder (a dict, see docs/ADDONS.md), or None."""
        return self._call('stereo_decoder')

    def window_maker(self):
        """The add-on's make_window(parent, current), or None if it has no window."""
        return getattr(type(self.obj), 'make_window', None)


def attach(rx):
    """Create every installed add-on for this receiver's band. Returns the ones that started."""
    out = []
    for name, cls in classes_for(rx.BAND):
        try:
            out.append(Attached(name, cls(rx)))
        except Exception as e:
            print(f"Add-on '{name}' not used: {type(e).__name__}: {e}")
    return out
