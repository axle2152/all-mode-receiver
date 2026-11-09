# Add-ons

An add-on is a decoder that isn't part of this program but plugs into one of its receivers
when it's installed. If no add-on is installed, the radio works exactly as it does without
them, and says nothing about it.

Both receivers take add-ons. On AM an add-on can decode data carried on the station and hand
the tuner station data to show: a station name, text, a program type, the same fields RDS
fills on FM. On FM an add-on listens to the composite, and can bring a stereo system of its
own, which the radio plays on stations without a 19 kHz pilot. `addons/crosby` (Crosby's 1958
FM-FM stereo) is one, and a worked example of everything below.

## How the radio finds an add-on

An add-on is an ordinary Python package, installed with pip, that declares itself in its
`pyproject.toml`:

```toml
[project.entry-points."all_mode_receiver.addons"]
mydecoder = "my_package.radio_addon:Addon"
```

The entry point names the add-on's class. Once a receiver of a band the add-on serves has
built its own blocks, it creates the add-on.

## What an add-on provides

```python
class Addon:
    BANDS = ('AM',)                  # the receivers it plugs into: 'AM', 'FM' or both
    NAME = 'MYDATA'                  # short: it labels the lamp and the messages

    def __init__(self, rx):
        """Build GNU Radio blocks and connect them to what the receiver offers:
        rx.connect(rx.addon_tap(), my_block). Raise if it can't start. Read the receiver's
        measurements in update(), not here: they don't exist yet."""

    def stereo_decoder(self):
        """FM only: this add-on brings a stereo system of its own (see 'A stereo system of
        its own', below). Called once, after __init__."""

    def reset(self):
        """The receiver was retuned: start over."""

    def update(self, now):
        """Called about every 20 ms from the receiver's own update, holding its lock.
        Return a dict of station data (see below), or None for nothing new."""

    def readout(self):
        """A line for the Advanced window, under the station details, or ''."""

    def meter(self):
        """(label, 0..1, text) for a meter in the Advanced window, or None."""

    @classmethod
    def make_window(cls, parent, current):
        """The add-on's own Tk window, opened with key I or a click on the station detail
        line. Return an object with toggle() (show / hide). current() gives the add-on
        running on the receiver now, or None between receivers."""
```

Everything but `BANDS` and `__init__` is optional. `readout`, `meter` and `current()` are
called from the window's thread, so they should only read.

## What the window shows

When an add-on is running on AM:
- its lamp (labelled with `NAME`) in the Advanced window, lit while `synced` is true, and an
  ALERT lamp
- its meter, if it has one, and a Blocks lost meter from `bler`
- once a `pi` arrives, the station on the dot matrix (call letters, name, scrolling text) and
  in the Advanced window, the way RDS shows on FM, with its `readout` line under the details;
  the RDS legend lights while `synced`
- an `alert` of 2 blinks ALERT over the station name (1 blinks TEST)

When an add-on is running on FM:
- its lamp in the Advanced window, lit while it reports `synced`, its meter if it has one,
  and its `readout` line under the station details
- while a station uses a stereo add-on's system: STEREO as usual, the system's name (the
  add-on's `NAME` and STEREO, for example CROSBY STEREO) on the dot matrix when the station
  has no RDS, and in the Advanced window

Without an add-on, AM and FM look exactly as they always have.

## What the receiver offers

| | |
|---|---|
| `rx.addon_tap()` | `(block, port)` to connect to. On AM: the 48 kHz complex channel, frequency-corrected and filtered, before the carrier lock. An add-on that needs a locked carrier runs its own, as fast or slow as it needs. On FM: the composite (MPX), real, straight from the FM demodulator at `rx.mpx_rate`, ahead of every filter. |
| `rx.connect(...)` | GNU Radio's connect, for wiring the add-on's blocks |
| `rx.BAND`, `rx.freq` | the band, the frequency it's tuned to |
| `rx.ch_rate` | AM: the channel sample rate |
| `rx.mpx_rate`, `rx.MPX_FULL` | FM: the composite's sample rate (240 kHz), and the composite value that is 75 kHz deviation (1.8) |
| `rx.stereo_lowpass()` | FM: the taps of the 15 kHz low-pass the stereo decoder puts on L+R and L-R (see below) |
| `rx.pilot_present`, `rx.cnr_db` | FM: whether there's a 19 kHz stereo pilot; the signal quality in dB, the reading the stereo blend follows. Measured just before `update()` is called. |
| `rx.station` | everything the add-ons have reported since the last retune |

## Station data

On AM, these keys in what `update()` returns fill the receiver's station fields, which the
tuner shows the way it shows RDS. On FM the receiver's own RDS keeps those fields, and
whatever an add-on reports stays in `rx.station`.

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
| `rate`, `bler` | `rds_rate`, `rds_bler` | data groups per second, share lost (%) |
| `ms`, `ptyn` | `rds_ms`, `rds_ptyn` | music or speech, program type name |
| `alert` | (kept in `rx.station`) | 0 none, 1 test, 2 emergency |
| `mono` | (kept in `rx.station`) | True: the station is mono, so the AM receiver ignores anything that looks like a stereo pilot and stays mono. Report it only when you're sure (a real stereo station must never lose its stereo to it). |

On FM:

| Key | What it is |
|---|---|
| `synced` | it has found what it listens for (lights its lamp) |
| `stereo` | a stereo add-on: its stereo system is on the air on this station |
| `stereo_quality` | a stereo add-on: how much of its L-R to blend in now, 0 (mono) to 1 (full stereo) |

Any other key is kept in `rx.station`. A retune clears all of it.

## A stereo system of its own (FM)

An FM add-on whose `stereo_decoder()` returns a dict brings a stereo decoder the radio can
play instead of its own (the first such add-on, if more than one is installed):

| Key | What it is |
|---|---|
| `out` | `(block, port)`: its L-R, real, at `rx.mpx_rate`, at the scale of the main channel: (L-R)/2 x `MPX_FULL`, where L and R are 1 at full deviation |
| `main_gain` | how much to raise the main channel (L+R) while its stereo is on. Crosby: 2, since its L+R rides at 50%. 1 if left out. |
| `mpx_top` | the highest composite frequency its signal uses, in Hz |

Its L-R must reach the matrix lined up with L+R, which arrives `len(rx.stereo_lowpass()) - 1`
samples after the tap. Put L-R through those same taps and the two channels match exactly;
then delay it so the whole path, the decoder's own filters and the low-pass together, comes to
that many samples. A discriminator (GNU Radio's quadrature demod) measures the frequency
between two samples, half a sample late, so an even-length filter ahead of it, with its
half-sample delay, keeps the count whole. `addons/crosby` does exactly this.

What the radio does with it:

- Its L-R plays only while the receiver hears **no pilot** and the add-on reports `stereo`. A
  pilot always wins: the station is in ordinary stereo.
- The blend follows `stereo_quality` (full stereo when the listener turns blend off); Mono and
  static cut work as usual.
- The main channel moves to `main_gain` while the add-on reports `stereo`, eased over a
  fraction of a second, and back when it stops. Mono keeps it: the station's level doesn't
  depend on the listener's stereo setting.
- The L-R source changes only at full mono, so switching between decoders can't click.
- If `mpx_top` reaches past 67 kHz, static cut listens for noise bursts above it instead of
  above 72 kHz, so the add-on's program can't pass for bursts.
- The echo canceller stays out (in auto): it judges by how round the FM circle is, which a
  signal wider than the receiver's 200 kHz channel never is.
- A retune puts the radio's own decoder back and the level back to normal at once, and the
  add-on starts over. If the add-on stops (raises), the same happens for good.

## When something goes wrong

An add-on can't stop the radio:

- If it can't be loaded, or raises while starting, the receiver prints one line in the
  terminal (`Add-on 'name' not used: ...`) and carries on without it.
- If it raises later, from `update`, `reset` or `readout`, the receiver prints one line
  (`Add-on 'name' stopped: ...`) and drops it until the next start.

If a stereo decoder can't be used (`out` isn't a `(block, port)`, or `main_gain` is out of
range), the receiver prints one line (`Add-on 'name' stereo not used: ...`) and runs the
add-on without it.

`tests/test_addons.py` shows each of these with pretend add-ons.

## Licence

The add-on runs inside this program, which is GPL 3.0. If you distribute an add-on, use a
licence compatible with GPL 3.0 (Apache 2.0, for example, is).
