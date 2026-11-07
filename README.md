# all-mode-receiver
An All Mode GNU Radio Based Receiver

Currently only AM/FM broadcast are available. Will add the rest of the functionality soon.

AM Stereo C-QUAM / Kahn 
NRSC-1 De-emphasis
Impulse & Noise Blanker
Automatic IF width
Wide IF Filter

FM Stereo with RBDS/RDS Decode
Stereo Blend / Hi-cut istant on CNR
CMA Equalizer
Noise Reduction

24-preset / Seek 

## What's in this folder

| File | What it is |
|---|---|
| `quartz_tuner_ui.py` | The tuner and monitor window. This is the one you run. |
| `am_receiver.py` | The AM receiver the tuner uses on AM (C-QUAM and Kahn stereo, blankers) |
| `fm_stereo.py` | The FM stereo receiver the tuner uses on FM (RDS, equalizer, noise reduction); also runs on its own |
| `rx_meters.py` | Audio and signal metering taps for the monitor |
| `rx_check.py` | Checks a receiver's signal chain without the window, and says in plain words which part stops |

## Running it

It runs on Windows and on Linux (Ubuntu 22.04 or newer).

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

The window looks best with the Nimbus Sans font (`fonts-urw-base35` on Ubuntu, usually already
installed). Without it, it uses DejaVu or Liberation.

Settings are saved in `quartz_tuner_settings.json` beside the program. Git ignores that file,
so `git pull` won't touch your presets.

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
