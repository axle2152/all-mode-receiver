# BTSC stereo (TV sound), an add-on

Analog TV sound in North America was FM, and from 1984 it could be stereo, by the BTSC
system (Multichannel Television Sound, MTS). Analog TV is gone from the air, but its sound
still turns up: channel 6's sound sits at 87.75 MHz, just below the FM band (the radio tunes
it as 87.7), and BTSC is used elsewhere too. This add-on lets the radio play it in stereo.

The `ntsc-btsc-tx` transmitter in
[gr-flowgraphs](https://github.com/axle2152/gr-flowgraphs) makes a proper BTSC signal to test
it with.

## How BTSC works

- **The main channel** carries L+R, so a mono TV hears both channels. Full modulation is
  25 kHz of deviation, a third of FM broadcast's 75 kHz.
- **The pilot** is a 15.734 kHz tone at 5 kHz deviation: the TV line rate (fH).
- **L-R** rides on twice that, 31.469 kHz, as double sideband (like FM stereo's 38 kHz), at
  50 kHz deviation for full modulation.
- **L-R is compressed** by dbx-TV noise reduction: its level is squeezed 2:1 and its treble
  adjusted on the fly, so the stereo channel stays quiet. The receiver must expand it back
  exactly, or left and right leak into each other.
- **SAP**, a second program (often another language), rides on its own FM subcarrier at five
  times the pilot. This add-on doesn't play SAP.

## What the add-on does

**It plays BTSC stereo when** the signal is usable and there's a pilot locked within 25 Hz of
15.734 kHz at a real level (2.5 kHz of deviation or more), for 0.3 seconds.

That wins over anything that looks like an FM stereo pilot. BTSC's L-R can put a lot of
energy right on 19 kHz: L-R around 12.5 kHz has its lower sideband there, and dbx-TV boosts the
treble. That's enough to fool the radio's FM stereo detector, which would then decode FM stereo
from a TV signal. FM stereo stations have nothing at 15.734 kHz anywhere near a pilot's level,
so once the add-on has found its pilot, the radio stops looking for 19 kHz.

**Once it's playing:**

- L+R is raised 9.5 dB (TV sound's 25 kHz against FM's 75), so a TV station plays as loud as
  an FM station.
- The pilot is the level reference. dbx-TV expansion doubles any level error in decibels, so a
  station deviating 20% low would otherwise lose most of its separation. The add-on measures
  the pilot (it should be 5 kHz) and corrects both channels by the difference.
- The dbx-TV expander is written from the equations in FCC OET Bulletin 60, as the reference
  transmitter's compressor is, and undoes it to within 0.01 dB and 0.05 degrees.
- It blends toward mono on a weak signal, as FM stereo does.
- On a weak signal the L-R channel is noisy, and noise would steer the expander wrong in quiet
  passages. L-R rides on one phase of its carrier and the other phase carries only noise, so
  the add-on measures the noise there and allows for it.

**What you see:**

- the STEREO light, and **BTSC STEREO** on the dot matrix (TV sound has no RDS)
- in the Advanced window (ADV on the tuner's face): a BTSC lamp, a "BTSC pilot" meter, and a
  line such as `BTSC  STEREO   pilot 5.0 kHz at 15734.3 Hz, L+R raised  9.5 dB`. When it
  isn't playing BTSC, that line says why: no pilot, pilot too weak, not locked, or too noisy.

**What the radio does differently while BTSC stereo plays:** noise reduction stands down. It
measures the noise in two quiet stretches of the FM signal (16 to 22 kHz), and BTSC's L-R fills
them, so it would take the program for noise.

## Installing it

From the `all-mode-receiver` folder. It needs scipy, which GNU Radio normally brings along
(on Ubuntu, `sudo apt install python3-scipy` if it's missing).

Windows, in the radioconda prompt:

```
pip install -e addons/btsc
```

Ubuntu 22.04, with its own pip:

```
python3 -m pip install --user --no-build-isolation -e addons/btsc
```

Ubuntu 23.04 and newer:

```
python3 -m pip install --user --break-system-packages -e addons/btsc
```

A pip you've upgraded yourself, on 22.04:

```
python3 -m pip install --user -e addons/btsc
```

Not sure which? `btsc_check.py`, below, prints the right command for that machine. Then
restart the tuner. It works alongside the Crosby add-on: each station plays through the one
whose system it uses.

## If it doesn't play BTSC

Close the tuner, start the transmitter, and from the `all-mode-receiver` folder run (on
Windows, `python` instead of `python3`):

```
python3 addons/btsc/btsc_check.py
```

It listens to channel 6's sound (87.75 MHz; another frequency with `--freq`) for 10 seconds,
with no window and no sound. It says whether the add-on is installed in that Python and prints
every second what it measures: signal quality, anything that looks like a 19 kHz pilot, peak
deviation, the 15.734 kHz pilot's level and frequency, and whether it's playing BTSC. Then it
says what it found and what to check. `--record 3` also saves 3 seconds of the radio signal
(`btsc_check.cs8`, about 14 MB) for playing back with `--replay`.

With the reference transmitter, channel 6 with colour bars and test tones:

```
python3 -m ntsc_btsc -i bars -c 6
```

## Checking it

With GNU Radio and scipy installed (no dongle, and no need to install the add-on first):

```
python3 addons/btsc/test_btsc.py
```

It encodes BTSC itself (OET-60, as the reference transmitter does), sends it through the
radio's real FM receiver in real time, and checks the decoding, the levels, the 19 kHz case,
and that TV sound in mono and FM stereo stations aren't taken for BTSC. With the Crosby add-on
beside it, it checks the two together. It takes about a minute.

## How well it does (simulation)

A tone in the left channel only, through the radio's real FM receiver:

| Signal quality (C/N) | Loud | Quiet (-30 dB) |
|---|---|---|
| 30 dB | 41 dB | 38 dB |
| 40 dB | 56 dB | 46 dB |
| 60 dB | 57 dB | 62 dB |

Separation at 10 kHz is about 44 dB. The FCC's OET-60 asks a BTSC system for 30 dB. Bench
results with a dongle and the HackRF are still to come.

## Licence

GNU GPL version 3 or later, like the radio.
