# Roadmap

Where this radio is going, in phases. Each phase leaves the radio working; the order can change.
Tick items off and add notes here as the work lands.

The goal: a software radio that does everything you'd want or wish a radio would do. It should
sound as good as the signal allows, clean up what it can, play at a correct, consistent level,
and have meters and visuals for casual listeners, broadcast people, and hams and shortwave
listeners alike.

## Phase 0: Linux and the README (done)

- [x] Runs on Ubuntu 22.04 and newer as well as Windows: executable scripts, Linux fonts,
  Pillow 9.0, no false "Dongle lost"
- [x] `rx_check.py` for diagnosing a receiver without the window
- [x] README describes the radio, its features and what it needs
- [x] AM filter: widths of 2.5, 5, 8 and 10.2 kHz, an AUTO switch (narrow by itself, or stay
  put), the Wide key at 12 kHz, and the 10 kHz whistle notch whenever the filter is open past
  9.5 kHz

## Phase 1: Safety net

Tests that show a change hasn't altered what the radio does, before anything gets moved around.

- [x] Tests that need neither GNU Radio nor a dongle (`tests/`): the dongle watchdog, the
  loudness meter against ITU BS.1770 and EBU Tech 3341/3342, the AM filter settings, and the
  whole window on AM and FM
- [ ] Play an IQ recording through a receiver in place of the dongle
- [ ] Reference results from the owner's recordings: C/N, filter width, stereo, RDS text,
  loudness, peak modulation over time. A test plays each recording and compares.
  The recordings themselves stay out of git (too large); the test takes their folder.

Needs from the owner: the recordings, and for each one its file format, sample rate, centre
frequency and what's on it.

## Phase 2: Split into parts (no change in behaviour)

The code grew around exactly two bands. Before adding more, split it so new bands and modes
plug in instead of adding another branch everywhere.

- [ ] A package folder beside the program with:
  - a shared receiver base: dongle or recording input, the watchdog, and the audio output
    (tone, volume, mute, headroom, meter taps)
  - the FM parts: RDS decoder, echo canceller, noise reduction, blend and high-cut control
  - the AM parts: C-QUAM and Kahn decoders, the adaptive filter
  - the meters
- [ ] The window split into the display drawing (VFD, dot matrix, scope screen), the controls,
  the monitor and loudness logic, the Settings and Advanced windows, and the app itself
- [ ] Each receiver states what it can do, and the window shows the controls that fit. This
  replaces the window probing receivers and the receivers' do-nothing stand-ins for the
  other band's controls, which is how the AM blanker controls ended up wired to nothing.
- [ ] `quartz_tuner_ui.py` stays the program you run, and existing settings files keep working

Done when the Phase 1 tests pass unchanged and the window looks and behaves the same.

## Phase 3: Frequency display and S-meter

- [ ] A frequency display with enough digits for any frequency the dongle reaches, down to
  1 Hz (up to 1766.000.000 MHz). How many digits it shows follows the tuning step: broadcast
  bands keep their familiar look (90.5 MHz, 1080 kHz), AM and narrowband FM on the shortwave
  and amateur bands show kHz, SSB and CW show down to 10 Hz or 1 Hz. Off-band frequencies no
  longer spill into the dot matrix, which stays free for RDS and PMDS text.
- [ ] An S-meter in place of the generic signal ladder: a longer bar with an S scale under it,
  S1 to S9 and then decibels over S9, to the IARU convention (S9 = -73 dBm below 30 MHz,
  -93 dBm above, 6 dB per S unit).
  The dongle reports levels relative to its own full scale, not in dBm, so this needs a
  calibration per dongle (from a known signal) that allows for the tuner gain and for direct
  sampling. It reads "uncalibrated" until then.
- [ ] Signal quality (C/N) stays alongside it, since a strong signal isn't always a clean one.

Mock-ups first, for the owner to pick from.

## Phase 4: Bands, modes and presets as data

- [ ] A band plan: long and medium wave, the shortwave broadcast bands (120 to 11 m), the
  amateur bands, VHF and UHF. Each band has its default mode, tuning step and filter widths.
- [ ] Tuning steps by mode, with a setting to change them: 5 kHz for AM and narrowband FM on
  the shortwave and amateur bands, 10 Hz or 1 Hz for SSB and CW.
- [ ] Presets store band, mode, frequency and filter, in pages of 24. Existing presets carry
  over.

## Phase 5: Shortwave and more analog modes

- [ ] One HF front end (direct sampling, a V4, or an upconverter) with switchable
  demodulators, so changing mode doesn't reopen the dongle. It should take fewer samples per
  second from the dongle than today's 2.4 Msps: pmds found the RTL-SDR silently dropping
  samples at that rate on the bench, and 960 ksps fixed it there.
  - AM
  - synchronous AM, with either sideband selectable to dodge interference
  - SSB (upper and lower), DSB, CW
- [ ] Filter width and passband shift, a notch, AGC with selectable speed for SSB and CW
- [ ] Impulse and noise blankers. The window already has their controls, but the blankers
  themselves were never written, so the controls do nothing yet. Written here, once, for AM
  broadcast and shortwave alike.
- [ ] Noise reduction for AM and SSB

## Phase 6: PMDS on the shared AM receiver

- [ ] The PMDS decoder becomes an add-on to the shared AM receiver, and the pmds repository
  uses this code instead of its own copy (by install or submodule, to be decided). That ends
  the drift between the copies; the different automatic AM filter tables, which made the
  loudness read about 1 dB apart on the same signal, came from that drift.
- [x] Until then, the pmds receiver uses this receiver's AM filter table (the owner's choice;
  only the table, not the new width settings).

## Phase 7: Narrowband FM and amateur digital modes

- [ ] Narrowband FM for repeaters and utility stations, with CTCSS and DCS tone decoding
- [ ] Automatic identification of digital signals. Each system has its own modulation, symbol
  rate and fixed frame-sync pattern (D-STAR, DMR, System Fusion, P25, NXDN, M17 and others),
  so the radio can say which one it's hearing. It can also show the callsign or IDs from the
  frame headers even where the voice can't be decoded.
- [ ] Open modes first: M17 and FreeDV (both use the open Codec 2 voice codec), APRS / AX.25
  packet, POCSAG paging; FT8, FT4, WSPR, RTTY and PSK31, using existing open decoders where
  they fit
- [ ] Voice on D-STAR, DMR, System Fusion and NXDN uses DVSI's proprietary AMBE codecs (P25
  uses IMBE). Decoding it raises the same kind of patent and licensing question as HD Radio,
  so identification and header data only until that's settled.

## Back burner: DRM and HD Radio

- DRM: decode with an existing open decoder (Dream) fed from the HF front end. Its audio
  codecs (AAC, xHE-AAC) have their own licensing questions to check.
- HD Radio (NRSC-5): an open decoder exists (nrsc5), but there are IP and licensing concerns to
  settle first. The hard part is blending: lining up the analog and digital audio, which no
  software radio does well.
- So these fit later, the work from Phase 2 on should keep to these rules:
  - the audio output can take audio from an outside decoder
  - the analog path gets an adjustable, measurable delay
  - wide enough IQ stays available (HD Radio needs about 400 kHz around the FM carrier; the
    FM front end already runs at 2.4 MHz)

## Open questions for the owner

None at the moment.
