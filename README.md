# all-mode-receiver

A software radio for an RTL-SDR dongle, built on GNU Radio, with the face of a hi-fi component
tuner: a glowing display, a row of preset keys, and a matching monitor unit underneath full of
meters and scopes.

The aim is a radio that does everything you'd want or wish a radio would do. It should sound as
good as the signal allows, clean up what it can, play at a correct, consistent level, and show
you what's going on. That goes for someone who just wants to listen, for someone who works in
broadcasting, and for a ham or shortwave listener.

Today it receives AM and FM broadcast. Shortwave and more modes are on the way (see
[Where it's going](#where-its-going)).

## What it does

### FM

**Listening on weak or difficult stations.** Most SDR programs play FM back exactly as it
arrives. This one handles a weak or difficult signal the way a good car radio does:

- **Blend and high-cut.** As a station gets noisy it blends toward mono and rolls off the
  treble, smoothly and only as much as needed, then lets go as soon as the signal recovers. It
  judges the signal by how clean the FM wave itself is, not by how loud the station is.
- **Static cut.** When the signal drops out for a moment, it trims the treble and stereo for a
  few milliseconds so the crackle doesn't come through. Quick bursts of multipath (picket
  fencing) pull just the stereo, where that noise is loudest.
- **Noise reduction.** Takes the hiss out between words and notes on weak stations. It measures
  the noise in an empty part of the FM signal, so it can't mistake music for hiss, and it stays
  out of the way on strong stations.
- **Echo canceller.** Signals bouncing off hills and buildings make FM sound harsh and
  splattery. An adaptive equalizer learns the reflections and removes them, the way a car
  radio's DSP tuner does, and switches itself in only when it measurably helps.
- **Adaptive filter width.** On a weak station the filter narrows to just the station to cut
  noise. It never narrows on loud music, and strong stations always stay wide.
- **Smart gain control.** Sets the dongle's gain by whether more gain actually makes the station
  cleaner, not just louder, and backs off at once if the dongle overloads.
- Channel 87.7 (the audio of TV channel 6, on 87.75 MHz) tunes like any other station.

**RDS and RBDS.** Station name, RadioText, program type and program type name, call letters
worked out from the station's code (even when a station's encoder is set to the international
mode by mistake), traffic flags, music or speech, alternate frequencies and the station's clock.
It also shows how many data groups arrive each second and how many are lost.

### AM

- **AM stereo, both systems:** Motorola C-QUAM and Kahn/Hazeltine ISB. It picks the right
  decoder from the station's pilot tone, as the multi-system radios did, and blends toward mono
  as the signal gets noisy. Mono stations play exactly as they do on any AM radio.
- **Adaptive filter width.** Wide on clean stations for full audio, narrower on weak ones or
  when the stations next door get loud. You choose how wide it normally opens: 5 kHz (what most
  AM radios do), 6 kHz, or 8 kHz (AMAX-class wideband). The **Wide** key opens it all the way
  for everything the station sends, with the 10 kHz whistle from neighbouring stations notched
  out.
- **NRSC-1 de-emphasis**, to undo the treble boost many US AM stations transmit.
- Carrier lock, carrier-to-noise, adjacent-channel strength and carrier offset readings.

### The tuner

- 24 presets, seek, step tuning, direct frequency entry from the keyboard, and mouse-wheel
  tuning.
- With the band-limit override it tunes anywhere the dongle reaches, 100 kHz to 1766 MHz. It
  switches between direct sampling and the normal tuner by itself.
- A signal ladder, phone-style signal bars, STEREO / TUNED / RDS indicators, and a dot-matrix
  line for the station name and RadioText.
- The look is yours to change: the colour of every lit element, the glow of the glass, the
  nameplate, even hand-drawn digits for the frequency display.
- It remembers your station, presets and settings.

### The monitor

- **Audio meters:** left and right, PPM or VU ballistics, with peak hold.
- **Loudness** to ITU BS.1770 / EBU R128: momentary, short-term, integrated, loudness range and
  true peak, the same numbers broadcasters work to.
- **Modulation:** FM peak deviation, measured on its own wideband path so the receiver's
  filters can't hide or inflate it, and AM peak modulation. Both include the FCC's "peaks of
  frequent recurrence", with a chart of how often each level is reached.
- **FM broadcast checks:** stereo pilot and RDS injection, subcarriers (SCA), and the carrier's
  frequency error.
- **Scopes:**
  - a goniometer with phase correlation
  - the radio wave itself (a clean FM signal draws a thin ring; echoes and noise smear it)
  - the channel spectrum before and after the echo canceller
  - the FM baseband (MPX) spectrum
  - an echo profile that shows how far away the reflecting hills or buildings are
  - a 30-second history of signal quality
- Meters and indicators are delayed to match the sound, so what you see is what you hear.
- An Advanced window with the full diagnostics.

Hover the mouse over almost anything for a plain-words explanation of what it shows.

## What you need

- **An RTL-SDR dongle** with an R820T, R820T2 or R828D tuner (RTL-SDR Blog V3 or V4, Nooelec
  NESDR and similar), and an antenna. For AM, the dongle needs a way down to medium wave: a
  V3-style dongle's direct-sampling mode (the default), a V4's built-in upconverter
  (`--am-source native`), or an external upconverter (`--am-source upconverter`).
- **GNU Radio 3.10** with **gr-osmosdr**.
- **Python 3.10 or newer**, with numpy, Pillow (including ImageTk) and Tk.
- **Windows** (radioconda) **or Linux** (Ubuntu 22.04 or newer).

## Running it

Windows, from the radioconda prompt (or whichever Python runs your GNU Radio):

    python quartz_tuner_ui.py

Linux: install GNU Radio and the rest once,

    sudo apt install gnuradio gr-osmosdr python3-tk python3-pil.imagetk python3-numpy

then, from this folder,

    ./quartz_tuner_ui.py

(`python3 quartz_tuner_ui.py` does the same thing.)

Some useful options; `--help` lists them all:

| Option | What it does |
|---|---|
| `--freq 1080e3`, `--freq 90.5e6` | Start on this station (Hz) instead of the last one played |
| `--scale 0.8` | Smaller (or larger) window |
| `--audio-device pulse` | Which sound output to use. On Linux, `pulse` or an ALSA name such as `plughw:0,0`. Leave it off for the system default. |
| `--am-source native` | How the dongle reaches AM: `direct_q` (default), `native` (V4) or `upconverter` (with `--upconverter-hz`) |
| `--log drive.csv` | FM: write the receiver's measurements to a spreadsheet file |
| `--record-iq drive.cs8` | FM: record the raw radio signal (about 4.8 MB a second) |

Keyboard: Left and Right tune, Shift+Left and Shift+Right seek, 0 to 9 recall presets, M stores
one, F and A pick the band, O is mono, W is the AM Wide key, P is power, and D or Enter starts
direct frequency entry.

The window looks best with the Nimbus Sans font (`fonts-urw-base35` on Ubuntu, usually already
installed). Without it, it uses DejaVu or Liberation.

Settings are saved in `quartz_tuner_settings.json` beside the program. Git ignores that file,
so `git pull` won't touch your presets.

## What's in this folder

| File | What it is |
|---|---|
| `quartz_tuner_ui.py` | The tuner and monitor window. This is the one you run. |
| `am_receiver.py` | The AM receiver the tuner uses on AM |
| `fm_stereo.py` | The FM stereo receiver the tuner uses on FM; also runs on its own |
| `rx_meters.py` | Audio and signal metering taps for the monitor |
| `rx_check.py` | Checks a receiver's signal chain without the window, and says in plain words which part stops |
| `tests/` | Checks that run without GNU Radio or a dongle (see below) |
| `docs/ROADMAP.md` | Where the project is going, phase by phase |

## Checks

These run without GNU Radio or a dongle, using a stand-in that pretends a dongle is sending
samples:

    python3 tests/test_watchdog.py       # tells a lost dongle from a quiet one, AM and FM
    python3 tests/test_loudness.py       # loudness meter against ITU BS.1770 and EBU Tech 3341/3342
    python3 tests/test_window.py         # the whole window runs on AM and FM

On Linux without a screen, run the window check as `xvfb-run -a python3 tests/test_window.py`.

## If the dongle keeps dropping

The tuner shows "Dongle lost, retrying in 3 s..." when the dongle stops sending samples for
2 seconds (8 seconds right after it connects, since opening it can be slow), and prints the
reason in the terminal. To see what's going on without the window, close the tuner and run

    python3 rx_check.py                  # AM
    python3 rx_check.py --band fm        # FM

It runs the receiver for 10 seconds with sound and 10 seconds without, prints how many samples
each stage moved every second, and says which part stopped. Paste what it prints into the chat.

On Linux, if the dongle won't open at all (`usb_claim_interface error -6`) even with nothing
else running, the kernel's TV-tuner driver has it. Unplug the dongle, run
`sudo modprobe -r dvb_usb_rtl28xxu`, and plug it back in.

## Where it's going

- A frequency display with enough digits for any frequency, and an S-meter
- Shortwave and amateur bands, with presets in pages of 24
- More modes: synchronous AM, SSB, DSB and CW, then narrowband FM and amateur digital modes,
  with automatic identification of which digital system a signal is using
- Later, possibly DRM and HD Radio

The details, phase by phase, are in [docs/ROADMAP.md](docs/ROADMAP.md).

## License

GNU GPL version 3; see `LICENSE`.
