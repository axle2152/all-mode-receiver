# all-mode-receiver project notes

A software radio for an RTL-SDR dongle on GNU Radio, with a hi-fi tuner face (Tk + Pillow)
and a monitor unit of broadcast-grade meters. Today AM and FM broadcast; headed for shortwave,
more modes and amateur digital modes (see `docs/ROADMAP.md`). Owner: Michael Foley, KK4EKN
(GitHub axle2152).

## Where things are

- `quartz_tuner_ui.py`: the launcher people run (its docstring is the user's guide to the
  keys). It only calls `radio.ui.app.main()`.
- `radio/`: the package. `receiver.py` is the base every receiver derives from (dongle and
  sound-card openers, lost-dongle watchdog, tone controls, add-on hook); `am.py` and `fm/`
  are the receivers (GNU Radio top blocks); `meters.py` the read-only metering taps;
  `addons.py` finds installed add-ons; `ui/` is the window (`app.py` with the Settings and
  Advanced windows as mixins in `settings.py` and `advanced.py`; displays, widgets, artwork,
  fonts, theme, constants in their own modules).
- `rx_check.py`: runs a receiver without the window and reports per-stage sample counts and a
  verdict. First thing to ask for when a receiver misbehaves on a machine you can't see.
- `tests/`: tests that need neither GNU Radio nor a dongle. `tests/standin/` is a stand-in
  for GNU Radio and osmosdr that the tests put first on the path; it pretends a dongle is
  sending samples, and with `STANDIN_TRACE=1` it logs every block, connection and call.
- `docs/ROADMAP.md`: the phased plan. Tick items off and add notes as work lands.
- `docs/ADDONS.md`: the add-on interface.

## How the pieces talk

- A receiver declares `BAND` and `FEATURES` (listed in `radio/receiver.py`). The window calls
  the common interface on any receiver, and a feature's methods only when the receiver has
  that feature (`tb.has('am_width')`); building windows before a receiver runs, it asks
  `radio.ui.backend.band_has()`. No `hasattr` probing and no do-nothing stand-ins: a new
  mode is a new `Receiver` subclass that declares what it can do.
- Settings files live beside `quartz_tuner_ui.py` (`radio.TOP_DIR`), never inside the package.
- `fonts.UI_FAMILY` and `fonts.FACE_FONT` are reassigned at start-up (Nimbus Sans): read them
  through the module, never `from .fonts import UI_FAMILY`.

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
    python3 tests/test_addons.py
    python3 tests/test_window.py          # needs a screen; headless Linux: xvfb-run -a ...
    python3 tests/test_gnuradio.py        # needs GNU Radio (apt install gnuradio gr-osmosdr)

`test_window.py --fonts <folder>` limits the window to one folder of fonts (Ubuntu's basic
DejaVu set, or an empty folder).

The owner has no usable IQ recordings, so a refactor is proven with
`tests/trace_receivers.py`: record a trace of the old code (check it out with
`git worktree add`) and of the new, and `--compare` them. Identical means every block,
setting and connection is unchanged. For the window, compare screenshots of the old and new
(both windows, AM and FM) pixel for pixel. Say what was compared in the commit message.

## PMDS stays out of this repository

- This repository is public. PMDS is the owner's own work, kept in a separate private
  repository until the owner says otherwise. No PMDS code here, and nothing about how PMDS
  works: not in code, comments, docs, tests, commit messages or pull requests. Refer to it
  only as an optional add-on for the AM receiver.
- The AM receiver uses the PMDS add-on if it's installed and stays a plain AM radio, silently,
  if it isn't. The add-on interface (`radio/addons.py`, `docs/ADDONS.md`) is generic and names
  no add-on; the add-on lives in the private repository (`pmds_addon.py`, installed with
  `pip install -e` of that folder) and registers itself through the `all_mode_receiver.addons`
  entry point. The private repository keeps no copies of this one's tuner or receivers.
- The private repository's `tests/test_addon.py` runs PMDS through this repository's real AM
  receiver in GNU Radio. Run it after changing `radio/am.py`, `radio/receiver.py`,
  `radio/addons.py` or the window's station display; keep the add-on interface stable.
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
