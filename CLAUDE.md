# all-mode-receiver project notes

A software radio for an RTL-SDR dongle on GNU Radio, with a hi-fi tuner face (Tk + Pillow)
and a monitor unit of broadcast-grade meters. Today AM and FM broadcast, shortwave, SSB, CW,
synchronous AM and narrowband FM; headed for amateur digital modes (see `docs/ROADMAP.md`).
Owner: Michael Foley, KK4EKN (GitHub axle2152).

## Where things are

- `quartz_tuner_ui.py`: the launcher people run (its docstring is the user's guide to the
  keys). It only calls `radio.ui.app.main()`.
- `radio/`: the package. `receiver.py` is the base every receiver derives from (dongle and
  sound-card openers, lost-dongle watchdog, tone controls, add-on hook, the tuner AGC narrow FM
  and HF share); `am.py`, `fm/`, `nfm.py` (narrowband FM: squelch, CTCSS, tone squelch) and
  `hf.py` (SSB, CW, synchronous AM, switched without a restart; its AGC in `agc.py`) are the
  receivers (GNU Radio top blocks); `ui/bandplan.py` the bands, modes and filters behind the
  Bands panel; `meters.py` the read-only metering taps; `addons.py` finds installed add-ons;
  `ui/` is the window (`app.py` with the Settings and Advanced windows as mixins in
  `settings.py` and `advanced.py`; displays, widgets, artwork, fonts, theme, constants in
  their own modules).
- `rx_check.py`: runs a receiver without the window and reports per-stage sample counts and a
  verdict. First thing to ask for when a receiver misbehaves on a machine you can't see.
- `tests/`: tests that need neither GNU Radio nor a dongle. `tests/standin/` is a stand-in
  for GNU Radio and osmosdr that the tests put first on the path; it pretends a dongle is
  sending samples, and with `STANDIN_TRACE=1` it logs every block, connection and call.
- `docs/ROADMAP.md`: the phased plan. Tick items off and add notes as work lands.
- `docs/ADDONS.md`: the add-on interface. Add-on packages declare themselves in `setup.py`:
  Ubuntu 22.04's pip builds with setuptools 59.6, which can't read `pyproject.toml`'s
  `[project]`. That pip needs `--user --no-build-isolation -e`; a newer pip `--user -e` (plus
  `--break-system-packages` on Ubuntu 23.04+). `crosby_check.py` prints the right one.
- `addons/crosby/`: the Crosby FM-FM stereo add-on (1958), a package of its own (installed with
  `pip install -e addons/crosby`), public, and the worked example of an FM stereo add-on. Its
  `test_crosby.py` runs it through the real FM receiver in GNU Radio; `crosby_check.py` is
  the bench check to ask for when it doesn't play Crosby (verdict in plain words; `--record`
  saves raw IQ that `--replay` plays back here through the real receiver).
- `addons/btsc/`: the BTSC (analog TV sound) stereo add-on, built the same way, with
  `btsc_check.py` and `test_btsc.py`. Its dbx-TV expander (`btsc_stereo/dbx.py`, numpy and
  scipy only) is the inverse of the reference transmitter in gr-flowgraphs/ntsc-btsc-tx.
  Needs scipy (GNU Radio recommends it on Ubuntu; radioconda has it).

## How the pieces talk

- Add-ons: AM's hear the channel, FM's hear the composite. An FM add-on with
  `stereo_decoder()` joins the stereo matrix; its L-R plays only with no 19 kHz pilot and while
  it reports `stereo`. Without add-ons the FM receiver builds and calls exactly what it did
  before (prove it with the trace tool).
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
  GNU Radio 3.10.1 lacks some Python methods 3.10.9 has (a PLL's `get_frequency()`), and its
  `hier_block2` reaches its own methods through `__getattr__`: a stand-in block with a
  catch-all `__getattr__` must ask `self._impl` first.
- The runnable scripts stay executable (mode 100755) with Unix line endings
  (`.gitattributes`), so `./quartz_tuner_ui.py` works on Linux.

## Tests

Run before every commit:

    python3 tests/test_watchdog.py
    python3 tests/test_loudness.py
    python3 tests/test_am_width.py
    python3 tests/test_addons.py
    python3 tests/test_display.py
    python3 tests/test_bandplan.py
    python3 tests/test_agc.py
    python3 tests/test_window.py          # needs a screen; headless Linux: xvfb-run -a ...
    python3 tests/test_gnuradio.py        # needs GNU Radio (apt install gnuradio gr-osmosdr)
    python3 tests/test_nfm.py             # needs GNU Radio; narrow FM on a pretend repeater
    python3 tests/test_hf.py              # needs GNU Radio; SSB, CW, sync AM on pretend signals
    python3 addons/crosby/test_crosby.py  # needs GNU Radio; after changing radio/fm/ or add-ons
    python3 addons/btsc/test_btsc.py      # needs GNU Radio and scipy; the same

`test_window.py --fonts <folder>` limits the window to one folder of fonts (Ubuntu's basic
DejaVu set, or an empty folder). The stand-in tests and the trace tool ignore installed add-ons
(PMDS, Crosby): they test the radio alone and bring pretend add-ons of their own.

The owner has no usable IQ recordings, so a refactor is proven with
`tests/trace_receivers.py`: record a trace of the old code (check it out with
`git worktree add`) and of the new, and `--compare` them. Identical means every block,
setting and connection is unchanged (against code from before a receiver was added, it
compares the sessions both have). For the window, compare screenshots of the old and new
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
- Sample rates: AM, narrow FM and HF take 960 ksps from the dongle (the RTL-SDR silently
  dropped samples at 2.4 Msps on the owner's bench); FM still takes 2.4 Msps.

## Careful with

- HD Radio (NRSC-5) and AMBE/IMBE voice (D-STAR, DMR, System Fusion, NXDN, P25): the owner has
  patent and licensing concerns. Don't add decoders for them without the owner's go-ahead;
  identifying the system and reading header data is fine.

## How the owner works

- New to git and GitHub. Explain git steps plainly and give exact commands.
- Machines: Windows (radioconda) and a Linux laptop (Ubuntu). Both update with `git pull`.
- Hardware is on the owner's bench, not in the cloud session: RTL-SDR receivers (a Nooelec
  NESDR among them), a HackRF transmitter. Bench and listening results come from there. No
  usable IQ recordings so far. The Soapy HackRF sink runs only whole-MHz rates (2 to 20 Msps);
  a transmit flowgraph's rate variables must match the sink's (a stale 3.84 Msps once halved
  Crosby's deviation). The RTL-SDR clips often on medium wave (Clip % in the Advanced window).
- Merges pull requests on GitHub. After each merge, give the update commands for both
  machines (`git checkout main`, `git pull`; and in the pmds folder, the `pip install -e`
  again when its add-on changed).
- Wants a traditional-looking radio (no waterfall), with meters and visuals for casual
  listeners, broadcast people, and hams and shortwave listeners.
- README is written in plain language for a general reader; keep that tone.

## Testing in a cloud session

The cloud container is Ubuntu 24.04. What worked:
- Real GNU Radio for `tests/test_gnuradio.py` and the private repository's
  `tests/test_addon.py`: `apt-get install -y --no-install-recommends gnuradio gr-osmosdr`
  (GNU Radio 3.10.9, system Python 3.12). `python3` may be another Python: run GNU Radio
  things with `/usr/bin/python3.12`. Python blocks in a hier block must stay referenced
  from Python, or the flowgraph segfaults on start.
- Test signals for FM: the receiver tunes 2 kHz off the station, and its DC canceller eats FM
  spectral lines that land within a few tens of Hz of 2 kHz: a whole-kHz tone (exactly), 997 Hz
  (its 2nd line 6 Hz away) or 1033 Hz on a loud enough tone. It looked like broken decoders
  twice. Use a tone whose multiples land at least 100 Hz from 2 kHz (1100 Hz: 2200), and tones
  that aren't harmonics of each other.
- The window: `apt-get install -y python3-tk python3-pil python3-pil.imagetk xvfb` and run under
  `xvfb-run -a`; `fonts-urw-base35` adds Nimbus Sans.
- Ubuntu 22.04's versions: `uv venv -p 3.10.12` with `pillow==9.0.1` and `numpy==1.21.5` for
  the tests that need no window (that Python's built-in Tk crashes under Xvfb). For the window
  with Pillow 9.0.1: a venv on the system Python 3.12 with Pillow 9.0.1 built from source
  (`uv pip install --no-build-isolation --no-binary pillow pillow==9.0.1`, after
  `apt-get install libjpeg-dev zlib1g-dev libfreetype-dev`). Limit fonts to Ubuntu's basic set
  with `tests/test_window.py --fonts /usr/share/fonts/truetype/dejavu`.
- Screenshots for before/after comparisons: Pillow's `ImageGrab.grab(bbox=..., xdisplay=...)`
  on the system Python under Xvfb.
- GNU Radio 3.10.1 itself (Ubuntu 22.04's): in the 22.04 chroot described next, with
  `https://archive.ubuntu.com` in its sources.list (the proxy refuses plain HTTP),
  `apt-get install --no-install-recommends gnuradio gr-osmosdr python3-scipy` with
  https_proxy set, `/dev/shm` bind-mounted as well as `/dev` (or GNU Radio runs out of
  buffers), and the repository bind-mounted inside.
- Installs on Ubuntu 22.04 (pip quirks a venv can't show): a real 22.04 in a chroot.
  `debootstrap --variant=minbase jammy <dir>` (about 15 minutes), jammy-updates and
  jammy-security in its sources.list, `apt-get install python3-pip ca-certificates sudo`,
  bind-mount /proc and /dev, copy in /root/.ccr/ca-bundle.crt and run update-ca-certificates,
  then test as a normal user (`useradd`; root can write the system folders and hides the
  problems), passing HTTPS_PROXY and PIP_CERT=/etc/ssl/certs/ca-certificates.crt.
