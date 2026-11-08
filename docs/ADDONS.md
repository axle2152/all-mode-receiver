# Add-ons

An add-on is a decoder that isn't part of this program but plugs into one of its receivers
when it's installed. If no add-on is installed, the radio works exactly as it does without
them, and says nothing about it.

Today the AM receiver takes add-ons. An add-on there can decode data carried on an AM station
and hand the tuner station data to show: a station name, text, a program type, the same
fields RDS fills on FM.

## How the radio finds an add-on

An add-on is an ordinary Python package, installed with pip, that declares itself in its
`pyproject.toml`:

```toml
[project.entry-points."all_mode_receiver.addons"]
mydecoder = "my_package.radio_addon:Addon"
```

The entry point names the add-on's class. Once a receiver of a band the add-on serves has
built its own signal chain, it creates the add-on.

## What an add-on provides

```python
class Addon:
    BANDS = ('AM',)                  # the receivers it plugs into

    def __init__(self, rx):
        """Build GNU Radio blocks and connect them to what the receiver offers:
        rx.connect(rx.addon_tap(), my_block). Raise if it can't start."""

    def reset(self):
        """The receiver was retuned: start over."""

    def update(self, now):
        """Called about every 20 ms from the receiver's own update, holding its lock.
        Return a dict of station data (see below), or None for nothing new."""

    def readout(self):
        """Lines for the Advanced window, or ''."""
```

`reset`, `update` and `readout` are optional.

## What the receiver offers

| | |
|---|---|
| `rx.addon_tap()` | `(block, port)` to connect to. On AM: the 48 kHz complex channel after the carrier lock. |
| `rx.connect(...)` | GNU Radio's connect, for wiring the add-on's blocks |
| `rx.BAND`, `rx.freq`, `rx.ch_rate` | the band, the frequency it's tuned to, the channel sample rate |
| `rx.station` | everything the add-ons have reported since the last retune |

## Station data

These keys in what `update()` returns fill the receiver's station fields, which the tuner
shows the way it shows RDS:

| Key | Field | What it is |
|---|---|---|
| `ps` | `rds_ps` | station name (8 characters) |
| `rt` | `rds_rt` | text: what's playing, or a message |
| `pi` | `rds_pi` | station code |
| `call` | `rds_call` | call letters |
| `pty` | `rds_pty` | program type (RBDS number) |
| `synced` | `rds_synced` | data is being decoded |
| `tp`, `ta` | `rds_tp`, `rds_ta` | traffic program, traffic announcement |
| `af` | `rds_af` | other frequencies with the same station |
| `ct` | `rds_ct` | the station's clock |
| `rate`, `bler` | `rds_rate`, `rds_bler` | data groups per second, share lost |

Any other key is kept in `rx.station`. A retune clears all of it.

## When something goes wrong

An add-on can't stop the radio:

- If it can't be loaded, or raises while starting, the receiver prints one line in the
  terminal (`Add-on 'name' not used: ...`) and carries on without it.
- If it raises later, from `update`, `reset` or `readout`, the receiver prints one line
  (`Add-on 'name' stopped: ...`) and drops it until the next start.

`tests/test_addons.py` shows each of these with pretend add-ons.

## Licence

The add-on runs inside this program, which is GPL 3.0. If you distribute an add-on, use a
licence compatible with GPL 3.0 (Apache 2.0, for example, is).
