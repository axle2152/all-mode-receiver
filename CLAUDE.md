# all-mode-receiver project notes

A software radio for an RTL-SDR dongle on GNU Radio, with a hi-fi tuner face (Tk + Pillow)
and a monitor unit of broadcast-grade meters. Today AM and FM broadcast; headed for shortwave,
more modes and amateur digital modes (see `docs/ROADMAP.md`). Owner: Michael Foley, KK4EKN
(GitHub axle2152).

## Where things are

- `quartz_tuner_ui.py`: the tuner and monitor window, the program people run.
- `am_receiver.py`, `fm_stereo.py`: the receivers (GNU Radio top blocks), same interface.
- `rx_meters.py`: read-only metering taps (BS.1770 loudness blocks, true peak, modulation).
- `rx_check.py`: runs a receiver without the window and reports per-stage sample counts and a
  verdict. First thing to ask for when a receiver misbehaves on a machine you can't see.
- `tests/`: tests that need neither GNU Radio nor a dongle. `tests/standin/` is a stand-in
  for GNU Radio and osmosdr that the tests put first on the path; it pretends a dongle is
  sending samples.
- `docs/ROADMAP.md`: the phased plan. Tick items off and add notes as work lands.

## Must run on

- Windows (radioconda) and Ubuntu 22.04 or newer. On 22.04 that means Python 3.10, Pillow
  9.0 (no `getlength` on the bitmap font, no `Image.Resampling`), Tk 8.6, numpy 1.21 and
  GNU Radio 3.10.1. Fonts: Windows fonts first, then Nimbus Sans, Liberation or DejaVu.
- The runnable scripts stay executable (mode 100755) with Unix line endings
  (`.gitattributes`), so `./quartz_tuner_ui.py` works on Linux.

## Tests

Run before every commit:

    python3 tests/test_watchdog.py
    python3 tests/test_loudness.py
    python3 tests/test_am_width.py
    python3 tests/test_window.py          # needs a screen; headless Linux: xvfb-run -a ...

`test_window.py --fonts <folder>` limits the window to one folder of fonts (Ubuntu's basic
DejaVu set, or an empty folder). Recorded-signal tests from the owner's IQ files are next
(Phase 1); the recordings stay out of git.

## PMDS stays out of this repository

- This repository is public. PMDS is the owner's own work, kept in a separate private
  repository until the owner says otherwise. No PMDS code here, and nothing about how PMDS
  works: not in code, comments, docs, tests, commit messages or pull requests. Refer to it
  only as an optional add-on for the AM receiver.
- The plan (roadmap Phase 3): the AM receiver uses the PMDS add-on if it's installed and stays
  a plain AM radio, silently, if it isn't. The add-on interface lives here; the add-on lives
  in the private repository.
- Until then the private repository carries its own copies of this repository's receivers,
  meters and tuner, so a fix to that shared code may be needed there as well. It already uses
  this repository's automatic AM filter table, but not its AM width settings or 12 kHz Wide.
- Sample rates: AM takes 960 ksps from the dongle (the RTL-SDR silently dropped samples at
  2.4 Msps on the owner's bench); FM still takes 2.4 Msps.

## Careful with

- HD Radio (NRSC-5) and AMBE/IMBE voice (D-STAR, DMR, System Fusion, NXDN, P25): the owner has
  patent and licensing concerns. Don't add decoders for them without the owner's go-ahead;
  identifying the system and reading header data is fine.

## How the owner works

- New to git and GitHub. Explain git steps plainly and give exact commands.
- Machines: Windows (radioconda) and a Linux laptop (Ubuntu). Both update with `git pull`.
- Hardware is on the owner's bench, not in the cloud session: RTL-SDR receivers (a Nooelec
  NESDR among them), a HackRF transmitter. Bench and listening results come from there, and
  the owner has IQ recordings to share.
- README is written in plain language for a general reader; keep that tone.
