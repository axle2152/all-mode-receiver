# Crosby FM stereo (1958), an add-on

Before FM stereo as we know it (the 19 kHz pilot system from GE and Zenith, which the FCC
chose in 1961), Murray Crosby's "compatible FM-FM multiplex" was one of the stereo systems on
trial. This add-on lets the radio play it.

Nobody broadcasts Crosby stereo today, so in practice this is for a station of your own: a
HackRF running `crosby_tx.grc`, for example (see [The transmitter](#the-transmitter)).

## How Crosby stereo works

- The main channel carries L+R, so a mono radio hears both channels. While the station is in
  stereo it runs at half of the full deviation, which is the system's well-known 6 dB penalty
  on mono radios.
- L-R rides on a 50 kHz subcarrier that is itself FM, swinging ±25 kHz, at half of the full
  deviation.
- There is no pilot tone. The subcarrier is always there while the station is in stereo, even in
  silence, so the subcarrier itself says "stereo".

## What the add-on does

**It plays Crosby stereo only when both of these are true:**

1. there is **no 19 kHz pilot** (a pilot means ordinary FM stereo, and that always wins), and
2. it finds **a 50 kHz FM subcarrier**, which takes three things at once, for 0.4 seconds:
   - enough of it: at least 18% injection (Crosby's own is 50%)
   - a steady, constant amplitude, the mark of an FM subcarrier (noise, or ordinary stereo's L-R
     with its pilot missing, doesn't have one)
   - centred on 50 kHz, within 3 kHz

The last test matters. A mono station carrying a 67 kHz background-music subcarrier (SCA) at
30% passes the first two. It looks strong and steady, but it sits 17 kHz off centre, so it is
not taken for Crosby.

**Once it's playing:**

- L+R is raised 6 dB, so a Crosby station plays as loud as any other station, whether it's in
  stereo or mono.
- Like ordinary stereo, it blends toward mono as the signal gets noisy: full stereo down to a
  signal quality of 15 dB, mono by 10 dB. Crosby is quieter than ordinary stereo on a fair
  signal, but its subcarrier falls apart quickly below about 10 dB, so the blend comes in later
  and faster.
- It keeps playing Crosby until you retune, a pilot appears, or the subcarrier has been gone for
  a second on a signal good enough to tell. On a weak signal it can't tell, so the level doesn't
  jump about.

**What you see:**

- the STEREO light, and **CROSBY STEREO** on the dot matrix. A Crosby station can't carry RDS,
  because RDS's 57 kHz falls inside the subcarrier.
- in the Advanced window (ADV on the tuner's face): a CROSBY lamp, a "Crosby subcarrier"
  meter (50% is the 1958 standard), and a line with the details, for example
  `Crosby  STEREO   subcarrier  50% at 50.0 kHz, C/N 24.1 dB`. When it isn't playing
  Crosby, that line says why: no subcarrier, too weak, a 19 kHz pilot, not centred on 50 kHz,
  or not steady. No CROSBY lamp at all means the add-on isn't installed (or didn't start: the
  terminal says why).

**What the radio does differently while Crosby stereo plays:**

- A Crosby signal is about 300 kHz wide and the radio's channel is 200 kHz, so its outer
  sidebands are trimmed. That costs very little sound. But it means the radio's signal quality
  reading tops out around 23 to 24 dB on a Crosby station, a little lower with a real dongle.
- The echo canceller stays out. It judges by that same reading, and would take the trimmed
  sidebands for echoes and "fix" the subcarrier away.
- Static cut listens for noise bursts above 88 kHz instead of 72 kHz, since the subcarrier
  reaches up to 83 kHz.

Without the add-on, or on any other station, the radio does exactly what it always did.

## Installing it

From the `all-mode-receiver` folder.

Windows, in the radioconda prompt:

```
pip install -e addons/crosby
```

Linux (if pip is missing: `sudo apt install python3-pip`):

```
python3 -m pip install --user -e addons/crosby
```

On Ubuntu 23.04 and newer, pip refuses to install into the system Python unless you add
`--break-system-packages` to that line. The add-on is only an entry in Python's list of
packages that points at this folder, so it's safe.

Then start the tuner as usual. The `-e` means the installed add-on is this folder itself, so a
`git pull` updates it with nothing to reinstall.

To remove it: `pip uninstall crosby-stereo`.

## The transmitter

`crosby_tx.grc` as drawn sends L-R half a sample early: 1.3 microseconds at its 384 kHz
composite rate. The subcarrier's Frequency Modulator (`sub_fm`) makes each L-R sample's
frequency take effect over the half sample before it. Your `crosby_rx.grc` and the loopback
have the same half sample in their discriminators, the other way round, so at 384 kHz the two
cancel. The radio's add-on measures the subcarrier's frequency exactly, at the radio's own 240
kHz rate, so the transmitter's half sample shows up:

| Separation, measured through the radio (simulation) | 1 kHz | 10 kHz |
|---|---|---|
| `crosby_tx.grc` as drawn | 48 dB | 28 dB |
| with the fix below | 96 dB | 55 dB |
| with the fix, both channels at 90% at once | 78 dB | 40 dB |

The fix is one block. In `crosby_tx.grc`, put a **Decimating FIR Filter** between `diff_gain`
and `sub_fm`: type Float->Float (Real Taps), Decimation 1, Taps `[0.5, 0.5]`. Averaging each
pair of samples moves L-R back by exactly half a sample.

If you still use `crosby_rx.grc` or the loopback with the fixed transmitter, give their
receivers the matching change: the same `[0.5, 0.5]` filter on the L+R path, after
`sum_delay`.

The 40 dB in the last row comes from the subcarrier's 19 to 81 kHz band, which both ends use.
Carson's rule would want 10 to 90 kHz for full deviation at 15 kHz. It only shows when both
channels are near full level at the same time.

## If it doesn't play Crosby

Close the tuner, start the transmitter, and from the `all-mode-receiver` folder run (on
Windows, `python` instead of `python3`):

```
python3 addons/crosby/crosby_check.py --freq 90.5e6
```

It runs the FM receiver on the dongle for 10 seconds with no window and no sound. It says
whether the add-on is installed in that Python, prints every second what the receiver and the
add-on measure (signal quality, any 19 kHz pilot, peak deviation, the subcarrier's injection,
centre and steadiness, and whether it's playing Crosby), and then says what it found and what
to check. Add `--record 3` to also save 3 seconds of the radio signal exactly as the dongle
sent it (`crosby_check.cs8`, about 14 MB), which `--replay crosby_check.cs8` plays back
through the receiver without a dongle, here or on another machine.

## Checking it

With GNU Radio installed (no dongle, and no need to install the add-on first):

```
python3 addons/crosby/test_crosby.py
```

It runs the real FM receiver with this add-on in real time on pretend stations: Crosby stereo,
clean and weak; the same station switched to mono; ordinary pilot stereo; and a mono station
with a 67 kHz SCA. It checks that only the Crosby stereo station plays Crosby, how much
separation there is, and that stereo and mono play equally loud. It takes about 25 seconds.

## Where the numbers come from

The signal is the 1958 Crosby system as described in Audio magazine (November 1958) and
Radio-Electronics (July 1961). The subcarrier band of 19 to 81 kHz is a choice in
`crosby_tx.grc`, not part of the 1958 description. Every figure above was measured in
simulation, with a test transmitter built like `crosby_tx.grc`, through this radio's real FM
receiver. Bench results with a dongle and the HackRF are still to come.

## Licence

GNU GPL version 3 or later, like the radio.
