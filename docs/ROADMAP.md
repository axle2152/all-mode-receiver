# Roadmap

Where this radio is going, in phases. Each phase leaves the radio working; the order can change.
Tick items off and add notes here as the work lands.

The goal: a software radio that does everything you'd want or wish a radio would do. It should
sound as good as the signal allows, clean up what it can, play at a correct, consistent level,
and have meters and visuals for casual listeners, broadcast people, and hams and shortwave
listeners alike.

**Next up:** the rest of Phase 6: noise reduction for AM, SSB and CW; then a passband shift, a
notch and an AGC speed setting for SSB and CW, DSB, and DCS for narrowband FM. FM at a lower
sample rate can go in any time. Phases 0 to 5 are done, and Phase 6's HF and narrowband FM
receivers and blankers.

## Phase 0: Linux and the README (done)

- [x] Runs on Ubuntu 22.04 and newer as well as Windows: executable scripts, Linux fonts,
  Pillow 9.0, no false "Dongle lost"
- [x] `rx_check.py` for diagnosing a receiver without the window
- [x] README describes the radio, its features and what it needs
- [x] AM filter: widths of 2.5, 5, 8 and 10.2 kHz, an AUTO switch (narrow by itself, or stay
  put), the Wide key at 12 kHz, and the 10 kHz whistle notch whenever the filter is open past
  9.5 kHz
- [x] AM receiver takes 960 ksps from the dongle instead of 2.4 Msps: at 2.4 Msps the RTL-SDR
  silently dropped samples on the bench, and about 1 Msps cured it
- [x] AM C/N reads true (it read 4.5 dB high); every threshold tuned against it moved by the
  same amount, so the radio behaves as before

## Phase 1: Safety net

Tests that show a change hasn't altered what the radio does. What's there works well; these
are so the split in Phase 2 can be shown to leave it that way.

- [x] Tests that need neither GNU Radio nor a dongle (`tests/`): the dongle watchdog, the
  loudness meter against ITU BS.1770 and EBU Tech 3341/3342, the AM filter settings, and the
  whole window on AM and FM
- [x] `tests/test_gnuradio.py` runs the real receivers in GNU Radio with a pretend dongle (a test
  station on the tuned frequency) and checks rates, updates and measurements
- [x] In place of recordings (the owner has none usable): `tests/trace_receivers.py` records
  every block, setting, connection and call each receiver makes through a scripted session,
  so old and new code can be compared exactly; screenshots of the window, compared pixel for
  pixel, cover the window
- [ ] Later, if recordings turn up: play an IQ recording through a receiver in place of the
  dongle, with reference results (C/N, filter width, stereo, RDS text, loudness, peaks)

## Phase 2: Split into parts (no change in behaviour)

The code grew around exactly two bands. Before adding more, split it so new bands and modes
plug in instead of adding another branch everywhere.

- [x] The `radio/` package beside the program:
  - `receiver.py`, the base every receiver shares: dongle and sound-card openers, the
    lost-dongle watchdog, tone controls and headroom
  - `am.py`; `fm/` with the receiver, its RDS decoder and its own blocks
  - `meters.py`
- [x] The window split into 14 modules under `radio/ui/`: the app, the Settings and Advanced
  windows, monitor and loudness logic, the displays, widgets, artwork, fonts, theme,
  constants
- [x] Each receiver states its band and what it can do (`FEATURES`), and the window calls only
  what a receiver has. The probing and the do-nothing stand-ins are gone; the AM blanker
  controls are hidden until the blankers exist (Phase 6).
- [x] The add-on interface: `radio/addons.py`, `docs/ADDONS.md`, `tests/test_addons.py`
- [x] `quartz_tuner_ui.py` stays the program you run, and existing settings files keep working

Checked: the receivers' traces were identical before and after the move (10,268 events), and
the window's screenshots pixel-identical, with and without Nimbus Sans. Found on the way and
fixed: Settings' Static cut checkbox couldn't turn static cut off (its variable was reused by
the blanker row), and clicking Noise reduction switched static cut on.

## Smaller items, any time

- [ ] The Monitor's PEAK MOD on AM comes and goes on a strong, clean station. Seen on the
  bench (November 2026): a GNU Radio and HackRF test transmitter at 52 dB C/N, with dense
  Stereo Tool program peaking at -95% and +125%, where a steady reading near 95% was expected.
  The owner has a minute of that program audio (mono, 192 kHz), so it can be played through
  the AM receiver here to see why the reading drops out.
- [x] Crosby FM-FM compatible stereo (1958) as an optional add-on (`addons/crosby`). FM now
  takes add-ons too: they hear the composite, and one can bring a stereo system of its own,
  which plays only on a station with no 19 kHz pilot, and only when the add-on finds its
  subcarrier. Crosby's is a 50 kHz FM subcarrier: enough injection, a constant envelope, and
  centred on 50 kHz (so a 67 kHz SCA on a mono station isn't taken for it). L+R comes up 6 dB to
  make up for its 50% injection; the echo canceller stays out while it plays.
  Checked in GNU Radio with simulated stations through the real FM receiver: separation 64 dB
  at 1 kHz and 54 dB at 10 kHz (52 and 39 dB with both channels at 90%), the same loudness in
  stereo and mono, ordinary stereo and SCA stations untouched. Without the add-on, the FM receiver's
  trace and the window's screenshots are identical to before. Found on the way:
  `crosby_tx.grc` sends L-R half a sample early (fix in the add-on's README).
- [x] BTSC stereo (analog TV sound, channel 6 at 87.75 MHz and elsewhere) as an optional add-on
  (`addons/btsc`). Pilot at 15.734 kHz, L-R on 2fH with dbx-TV expansion written from FCC
  OET-60, as the reference transmitter in gr-flowgraphs/ntsc-btsc-tx compresses it; L+R raised
  9.5 dB; the pilot is the level reference. The FM receiver now takes several stereo add-ons at
  once (Crosby and BTSC), each with its own reports; BTSC's pilot vetoes the 19 kHz detector,
  which its L-R can fool; noise reduction stands down while an add-on's stereo fills the
  stretches it measures the noise in (BTSC's does, Crosby's doesn't). Checked in GNU Radio
  through the real FM receiver: separation 57 dB (loud) and 62 dB (quiet, -30 dB) at 60 dB C/N,
  41 and 38 dB at 30 dB C/N, 44 dB at 10 kHz; as loud as FM broadcast; a station at 80%
  deviation still balanced; TV mono and FM stereo not taken for BTSC. Found on the way: the
  expander's detectors must not hear the pilot (band-limited like the compressor's), its noise
  must be allowed for (measured on the carrier's other phase), and the pilot PLL must not see
  the pilot's mirror (a complex band-pass): each cost 10 to 30 dB of separation.
- [x] BTSC on the bench: the NTSC transmitter, a dongle receiving it (channel 24, 535.75 MHz, since
  channel 6 is in use locally).
  First try (channel 24, 535.75 MHz with the band limits overridden, on the Ubuntu 22.04
  laptop): no BTSC, and now and then a segfault on start. Both found and fixed. GNU Radio
  3.10.1 offers no `get_frequency()` on its PLL block in Python, so the add-on never saw its
  PLL as locked (it now measures the PLL's frequency from its output, on every version). The
  segfault: the window's timer asked the new receiver for its audio lag while the backend
  thread was still inside `start()`, and reading GNU Radio's sample counters then crashes it
  (15 times in 15 on 3.10.1 and on 3.10.9 when forced); they're read only after `start()`
  now, and `test_gnuradio.py` forces the race. The reference transmitter's own output (15
  Msps, 8-bit, as sent to the HackRF) plays BTSC through the receiver at 535.75 MHz, up to
  25 kHz off-centre. Second try, after those fixes: BTSC STEREO, and it plays well. The
  off-centre clipping (an off-centre carrier puts a steady offset on L+R that BTSC's 9.5 dB
  raises into the final clipper: 2 dB lost at 25 kHz off, silence at 50) is on the back burner:
  nothing audible on the bench.
- [x] Crosby on the bench: the HackRF sending the fixed `crosby_tx.grc`, a dongle receiving.
  First try: no Crosby. `addons/crosby/crosby_check.py` found the add-on working (CROSBY STEREO
  from the first second, subcarrier steady at 50.0 kHz) but not installed: Ubuntu 22.04's pip had
  installed it as "UNKNOWN" (now described in `setup.py`, which every pip reads). It also showed
  the station at about half its deviation (subcarrier 26%, peaks 40 kHz); L+R is now balanced to
  the measured subcarrier, which still helps on any station off its deviation. The half deviation
  was the transmitter: the HackRF sink had been set to 2 Msps (the Soapy HackRF sink runs only
  whole-MHz rates, 2 to 20 Msps; 2.4 and 3.84 don't work) while the flowgraph's `rf_rate` was
  still 3.84 Msps, so its FM was worked out for 3.84 and sent at 2: 52% of the deviation, which
  fits the 40 kHz and 26% measured. With `rf_rate` matching, everything came out where it should.
  The bench separation, and the signal quality a dongle shows on a Crosby station (the
  simulation tops out at 23 to 24 dB), are still to be written down.
- [x] A gentle start on AM. It started loud and at its widest IF, a jump scare: the audio level
  is the envelope divided by a carrier estimate that started from zero (the first 100 ms played
  56 dB over the station's level in GNU Radio), and the IF opened to the chosen width at once.
  Now the estimate runs fast (10 ms) until it has the station's level, then at its usual pace
  (250 ms); the sound fades in over a second at start; and AUTO starts at 2.5 kHz and opens as
  the C/N allows (to 8 kHz in about two seconds on a clean station). The fast estimate also
  runs after each retune: tuning to a station 20 dB stronger played +16.5 dB for 0.7 s, now one
  50 ms tick at +8 dB. `tests/test_gnuradio.py` checks the start. Still to hear on the bench:
  retunes on a real dongle, whose buffers delay the new station.
- [x] AM stereo pilots: an add-on can't lock them out, and data can't pass for one. An add-on
  that reported a station as mono held stereo off, and on the bench a false report on a real
  C-QUAM station cost it its stereo. Now a pilot the receiver hears always wins, and the pilot
  test is stricter: the strongest line within 2.5 Hz of 25 Hz (15 Hz for Kahn) must sit within
  0.5 Hz of it and stand 10 dB over the spectrum 3 to 4.5 Hz either side. Before, the
  strongest energy anywhere from 23 to 27.5 Hz counted. `tests/test_gnuradio.py` checks real
  pilots (found as before, down to 12 dB C/N), one with an add-on saying mono, a strong tone
  1.5 Hz off, and broad energy around 25 Hz; the old test failed the last three.
- [ ] FM at a lower sample rate too, like AM (the RTL-SDR can drop samples at 2.4 Msps; the owner
  suspects it costs RDS more than is noticed: the RDS error rate in the Advanced window is the
  measure, before and after). Its gain
  control and quality thresholds were tuned at 2.4 Msps, so check them on the bench, or with
  `tests/test_gnuradio.py` and the trace tool, before and after.

## Phase 3: PMDS as an optional add-on

PMDS is the owner's own work, in its own repository (public since November 2026:
github.com/axle2152/pmds). None of its code goes in this repository.

- [x] When the AM receiver starts, it looks for installed add-ons. If there are none, this is
  a regular AM radio: no message, no empty controls.
- [x] An add-on gets the AM receiver's channel signal and reports station data (station name,
  text, program type and the other RDS fields), plus its own lines for the Advanced window.
- [x] The window shows that station data on AM the way it shows RDS on FM: the dot matrix and
  RDS legend, the Advanced window's station panel, a lamp and meters for the add-on, an
  alert blinking over the station name, and the add-on's own window on key I
- [x] An add-on can say the station is mono, and the AM receiver then ignores anything that
  looks like a stereo pilot
- [x] The add-on itself, in the PMDS repository, registered through the
  `all_mode_receiver.addons` entry point; installing that folder is all it takes
- [x] The PMDS repository keeps only PMDS and its add-on. Its copies of this repository's
  tuner and receivers are retired, which ends the drift between the copies.

Checked end to end in GNU Radio: a simulated station through this repository's real AM
receiver, with the add-on attached the way the tuner attaches it, decodes call letters, name
and text at several data rates, with and without an alert.

## Phase 4: Frequency display and S-meter

- [x] A frequency display with enough digits for any frequency up to 9.99 GHz, down to 1 Hz
  (9999.999.999 MHz), so SDRs that reach further than the RTL-SDR fit too. Ten digits don't
  fit beside today's signal ladder, so the ladder moves right, where the S-meter goes. How
  many digits it shows follows the tuning step (and the exact frequency, when it's off the
  step): broadcast bands keep their familiar look (90.5 MHz, 1080 kHz), AM and narrowband FM
  on the shortwave and amateur bands show kHz, SSB and CW show down to 10 Hz or 1 Hz.
  Off-band frequencies no longer spill into the dot matrix, which stays free for station
  data text.
  Done as layout A (below), with kHz up to 30 MHz: seven big digits down to 1 kHz, three
  small ones below that, the number against its unit (1080 kHz, 90.5 MHz, 9580 kHz,
  14230.50 kHz, 146.520 MHz, 9999.999.999 MHz). The rules are in `radio/ui/frequency.py`,
  checked by `tests/test_display.py`. The signal bar moved right into the S-meter's place,
  15 bars long; it still shows signal quality, labelled SIGNAL, until the S-meter lands.
- [x] An S-meter in place of the generic signal ladder: a longer bar with an S scale under it,
  S1 to S9 and then decibels over S9, to the IARU convention (S9 = -73 dBm below 30 MHz,
  -93 dBm above, 6 dB per S unit).
  The dongle reports levels relative to its own full scale, not in dBm, so this needs a
  calibration per dongle (from a known signal) that allows for the tuner gain and for direct
  sampling. It reads "uncalibrated" until then.
  Done: the receivers report the station's level (dBFS) and the tuner gain ahead of the
  converter (`signal_level()`), and the window turns that into dBm: dBFS - gain + a
  calibration, one per input path (direct sampling, the tuner below 30 MHz, the tuner from
  30 MHz up), set in Settings > Reception > S-meter from a signal of known strength. UNCAL
  lights until then, and while the tuner's own AGC hides the gain (AM through the tuner). The
  arithmetic is in `radio/ui/smeter.py`; real GNU Radio reads the test stations' levels to
  0.1 dB. Still to check on the bench: how close a calibration made at one frequency and gain
  holds at others (the RTL-SDR's gain steps and frequency response aren't exact).
- [x] Signal quality (C/N) stays alongside it, since a strong signal isn't always a clean one.
  Done as the owner's option 2: one bar, S units or signal quality (POOR / FAIR / GOOD),
  switched with S, the METER button or a click on the bar, remembered per band. MEMORY moved
  over the channel number. Phase 5's band plan can give each band its starting view
  (broadcast on quality, shortwave and amateur on S units).

Mock-ups first, for the owner to pick from. Two layouts were drawn, both with the S-meter as a
longer bar to the right of the frequency and an S scale under it. The owner picked A, with
kHz up to 30 MHz, and the S-meter where the mock-ups put it:
- A, hi-fi: big digits down to 1 kHz and small digits below that, the number against its
  unit (drawn with kHz below 2 MHz; built with kHz below 30 MHz).
- B, communications receiver: ten equal digits in fixed groups, always in MHz (1.080 for
  AM 1080).

## Phase 5: Bands, modes and presets

The face the owner chose (version 2 of the mock-ups): a third panel, Bands, between the tuner and
the monitor, and modes on the tuner.

- [x] Bigger text: nothing on the tuner or the monitor under 7-8 pt. At full size (pixels):
  display legends 13 (were 9.5), the signal scale 11 (8), the monitor's meter labels and scales
  11-12 (7-8), the scope's lettering 9.5-10.5 (6-7), printed labels 11.5-13 (9.5-11), key
  legends 13 (11-12). The tuner's and the monitor's displays grow taller (about 154 px, from
  122) to fit it, the monitor's rows spread out. Done: both units are 200 px tall (were 168),
  the scope too, its graticules laid out from its boxes; +20 +40 +60 on the S scale at 10 px,
  three wide labels only 21 px apart. The tuner display keeps one dot-matrix row, centred,
  until Band info brings the second.
- [x] The window grows to fit the screen. Today it never goes past full size (1480 px wide),
  which leaves room on a 1080p screen and draws it at about half the size you'd expect on a 4K
  screen with the desktop scaled 200%. A size choice in Settings too. Must look right on the
  owner's 4K laptop and on 1080p machines. Done: FIT draws the panels 90% of the screen's
  width (1080p: 1728 px, scale 1.17; 4K: 3456 px, scale 2.34; 720p: 0.78), within its height;
  fixed sizes 75 to 200%; a change redraws the panels in place. --scale still wins for a run.
- [ ] Later: a split layout for 720p and narrow screens, each panel cut in half and the halves
  stacked, so nothing shrinks below readable.
- [x] The hint buttons become real keys: Power and Monitor (show the monitor), Advanced and
  Settings on the tuner's left; METER becomes "S units" (the signal bar in S units, or signal
  quality) beside Band info under the presets; FREQ moves to the Bands panel as Direct.
  Done, until the Bands panel exists: Direct sits on the tuner's third row (with the FM/AM
  band keys and the AM filter's Wide key on the second), and Band info comes with the second
  dot-matrix line. Monitor, Advanced, Settings, Direct and S units stay down while what they
  turn on is on.
- [x] The Bands panel, a key for every band, grouped under printed headings:
  - Shortwave (metres): 120, 90, 75, 60, 49, 41, 31, 25, 22, 19, 16, 15, 13, 11; with
    Broadcast (LW, MW, FM) at the right of that row
  - Amateur (metres): 160, 80, 60, 40, 30, 20, 17, 15, 12, 10, 6, 2, 1.25, 70 cm, 33 cm,
    23 cm, its keys the same width as the shortwave keys so the columns line up; with
    Frequency (Direct: type it on the preset keys) at the right
  - Public service: Air, Marine, Weather, Police/Fire; Personal: CB, FRS/GMRS, MURS;
    Narrow FM: Squelch, Rev and Tone
  Done: a third unit between the tuner and the monitor. Each band key tunes to its band in its
  usual receiver and remembers where it was left; Police/Fire steps through VHF, UHF and
  800 MHz; Direct finds the band a typed frequency is in and tunes it exactly as typed (not
  onto the nearest channel); presets find theirs. FM/AM left the
  tuner; Direct sits under the Tuning keys (the owner's choice), beside Wide. A Bands key (and
  Settings > Panel) shows or hides the panel. A band change across 28.8 MHz reopens the dongle
  (direct sampling below, the tuner above), and says so. Bands that need SSB or CW say NOT YET
  until that receiver exists. Narrow FM's bands play (2 m, 70 cm, marine, weather, FRS/GMRS,
  MURS, public safety), and Squelch joined Rev and Tone on the panel (no room on the tuner).
  Since replaced by the Bands & Modes unit (next).
- [x] Bands & Modes (the owner's redesign: the Bands panel was busy, and the FM mode key was
  easily pressed for the FM band). Version 2 of two mock-ups: the middle unit is a dot-matrix
  display, 4 lines of 60 characters (radio/ui/vfd.py MenuVfd: the tuner's dots, each character
  drawn and cached the first time it's shown, so a page takes a third of a second at 4K), with
  ten soft keys under it that do what its bottom line shows. Group keys (Broadcast, Shortwave,
  Amateur, Public, Personal, Mode) put a group's bands, or every mode, on the soft keys; a long
  group pages (MORE>, or its key again), and the group follows the band in use. The display:
  the band, mode and filter (narrow FM's offset and tone, SSB and CW's step); the band's range
  and the modes of its parts, or a repeater's input and the tone heard; the filters on offer
  with the one in use, and narrow FM's squelch; the soft keys' labels. Filter narrower / wider
  and Squelch - / + at the right (off, then 1 to 9), Rev and Tone at the left. The tuner: band
  keys FM, AM, LW and SW (the last shortwave band, again the next) where the mode keys were;
  Width and Band info gone; the tuner's text is the station's own, or the band, and with the
  unit hidden its second row carries the mode and filter.
- [x] A band plan behind those keys: each band's edges, default mode, tuning step and filter
  widths, and the parts of a band with a mode of their own. Done (radio/ui/bandplan.py,
  tests/test_bandplan.py), for the Americas; filter widths come with the receivers.
- [x] Mode keys on the tuner: AM, FM, SSB, CW. Pressing one again steps to its next kind: AM,
  synchronous AM on USB or LSB; FM broadcast, narrow (+/-5 kHz), narrow (+/-2.5 kHz); SSB on
  USB or LSB. Width steps the mode's filters (the CW filters too, taking over from the AM width
  and FM bandwidth rows in Settings); Squelch for narrow FM; Fine tune (two small keys) nudges
  by less than the band's step (10 Hz on SSB and CW, 100 Hz or so on AM). Done: the keys, the
  kinds, a mode on any frequency (changing receiver if it must), presets remembering their mode,
  Fine tune (AM 100 Hz, FM 5 kHz, SSB/CW 10 Hz, narrow FM 500 Hz). Every kind plays now.
  Width (where Wide was) steps the mode's filters: AM 2.5, 5, 8, 10.2 kHz then WIDE; FM auto,
  wide, narrow; narrow FM 5 or 2.5 kHz; SSB, CW and sync AM their own (each remembered), shown
  on the second text row. Narrow FM's two kinds, and SSB, CW and sync AM, change without a
  restart; Squelch is on the Bands panel, its level in Settings. Since then the modes are soft
  keys on Bands & Modes, every kind its own key, and Width its Filter keys.
- [x] Each band picks its usual mode, and so does tuning into a part of a band with its own: AM
  on medium wave, shortwave and the airband; narrow FM on 2 m, marine and weather; on the
  amateur HF bands LSB below 10 MHz and USB above (20 m is USB), and CW in their CW parts.
  Where there's no usual mode, the radio stays on the one it's on. Done, SSB and CW included
  (crossing into a part whose receiver isn't built would leave the mode as it is).
- [x] The tuner display: legends AM, FM, USB, LSB, CW; AUTO, STEREO, MONO, RDS, TONE; TUNED,
  MEMORY. A second dot-matrix line. Band info (a latching key) switches the text between the
  band information (band, kind of mode, filter, repeater offset, tone) and the station's own
  data (RDS, RBDS, PMDS: a shortwave station could carry PMDS); on modes that can carry no data
  (SSB, CW) it stays on band information. Done: with no station data the text shows the band
  anyway; in the station view the second row has the RDS programme type, else the mode. WIDE
  left the legends for the text (AM's filter: WIDE, AUTO or its width). Since then Band info is
  gone: the band information is on the Bands & Modes display.
- [x] Repeaters: Rev listens on the input, at the band's usual offset (2 m +/-600 kHz, 1.25 m
  -1.6 MHz, 70 cm +/-5 MHz); CTCSS (PL) and DCS decoded and shown, and tone squelch. Done for
  CTCSS: the tone named (any of the 50 standard ones, to a fraction of a hertz, so 67.0 and
  69.3 stay apart), shown in the second text row (PL 100.0, or TSQ 100.0 with tone squelch),
  and kept out of the speaker; Tone squelches on the tone heard or a preset's; Rev at the band
  plan's offset or a preset's (GMRS +5 MHz on 15-22). DCS still to come.
- [ ] Regions, chosen in Settings, because band edges and channel spacing depend on where you
  are:
  - AM: 530 to 1700 kHz in 10 kHz steps in the Americas; 531 to 1602 kHz in 9 kHz steps in
    Europe, Africa and most of Asia (to 1701 kHz in Australia); long wave, 153 to 279 kHz in
    9 kHz steps, where it's still used.
  - FM: 87.5 to 108 MHz in 100 kHz steps in most of the world; odd tenths in 200 kHz steps in
    North America; 76 to 95 MHz in Japan; the old OIRT band, 65.8 to 74 MHz, in a few places
    in Eastern Europe.
  - Amateur bands differ by ITU region (40 m is 7.0 to 7.3 MHz in Region 2 and 7.0 to
    7.2 MHz in Regions 1 and 3; 80 m is 3.5 to 4.0, 3.5 to 3.8 and 3.5 to 3.9 MHz in Regions
    2, 1 and 3), and a few by country (60 m).
  - Public service and utility bands differ by country too (airband channels 8.33 kHz apart
    in much of Europe and 25 kHz in the US; CB on AM in the US and mostly FM in Europe).
- [ ] Tuning steps by mode, with a setting to change them: 5 kHz for AM and narrowband FM on
  the shortwave and amateur bands, 10 Hz or 1 Hz for SSB and CW. Done so far: SSB and CW step
  100 Hz (Fine tune 10 Hz), and a band key brings back exactly where the band was left. The
  setting is still to come.
- [x] Channelized bands (the owner's idea): CB (40), FRS/GMRS (22; GMRS repeater pairs on
  15-22), weather (WX1-7), MURS (1-5) and marine VHF (US numbering; 18A shows as 18) tune
  channel to channel, in channel order (CB's 23 between 22 and 24), the number keys pick a
  channel, and the CH digits show it (both CH digits are now whole 7-segment digits: CB runs
  to 40, marine to 88). No presets there: Memory pressed twice sets the band's home channel,
  and Settings > Reception chooses whether the band key goes to it or to where it was left,
  and whether these bands step by channel or by their ordinary spacing.
- [x] Presets by band (the owner's choice over pages): 24 for each band, except the
  channelized ones; a preset stores band, mode, frequency and filter, and on narrow FM the
  repeater offset (or Rev) and the PL tone. Existing presets carry over (FM to FM, AM to the
  band each is in). Done so far: 24 per band (the CH digits show the band's own), the first
  run's seeds on FM 1-4 and MW 1-4, an older single list carried over slot for slot; the mode;
  on narrow FM the offset, the tone and tone squelch (in the settings file as
  'NFM 146.94 OFS-600 PL100.0 TSQ'); on SSB, CW and sync AM the filter ('HF 14.0525 CW W500').
  AM's width stays a setting of its own, as before.

## Phase 6: Shortwave, VHF/UHF and more analog modes

- [x] One HF front end (direct sampling, a V4, or an upconverter) at about 1 Msps like the AM
  receiver, with switchable demodulators, so changing mode doesn't reopen the dongle:
  - AM
  - synchronous AM, with either sideband selectable to dodge interference
  - SSB (upper and lower), DSB, CW

  Done (radio/hf.py, tests/test_hf.py in real GNU Radio 3.10.1 and 3.10.9): SSB, CW (dial on
  the carrier, a 600 Hz tone) and sync AM on either sideband, switching without a restart; the
  AM receiver's sources (direct sampling below 28.8 MHz, the tuner above, so 6 m, 2 m and 70 cm
  SSB too); a complex sideband filter about 150 Hz from pass to stop (the other sideband 78 dB
  down in the test); sync AM's PLL hears the carrier alone (+/-150 Hz) and turns the whole
  channel by its phase, so a strong neighbour can't wobble it (an interferer on the other side
  68 dB down). Plain AM stays on the AM receiver (C-QUAM, the add-ons), so AM to sync AM reopens
  the dongle for a moment. DSB is still to come.
- [ ] Filter width and passband shift, a notch, AGC with selectable speed for SSB and CW. Done
  so far: the filter widths (the Width key), and the AGC (radio/agc.py, tests/test_agc.py):
  look-ahead attack (no overshoot), hang and decay by mode, its ceiling set from the band's
  noise so an empty frequency plays 20 dB under a station. Still to come: a speed setting,
  passband shift, a notch.
- [x] Impulse and noise blankers, written once (radio/blanker.py) for the AM receiver (medium
  wave and shortwave broadcasts) and the HF receiver (SSB, CW, sync AM).
  - First version: a pulse was a sample K times over the band's median magnitude, patched with
    a straight line. On the bench (November 2026) it patched 0.25% of the time and made no
    audible difference. Modelled on a medium-wave band with locals 30 dB over the station
    listened to, it couldn't win: pulses loud in a weak station's channel are smaller than the
    locals at full bandwidth, so it couldn't see them, and its straight lines chopped the locals,
    whose splatter made clicks of its own (with locals 10 dB up, HIGH patched a band with no
    pulses 450 times a second and damaged it). Its tests had used one station in plain noise.
  - Now the noise blanker predicts the band (linear prediction, 24 samples back, fitted every
    40 ms by Levinson's recursion to what it gave out). A pulse is where the prediction misses
    by K times its median miss: it starts where the forward miss (clean before a pulse) first
    crosses and ends where the backward miss (clean after it) last does, no longer than 50 us
    and not inside a long stretch over half the line (a signal arriving). The patch carries the
    prediction across the gap from both sides, faded, so every station carries on; and only if
    the pulse outweighs the error such a patch makes. LOW, MED and HIGH: 18, 15, 12 dB over the
    median miss; patching over 1% of the time raises the line (up to 12 dB). 123 us late.
    Modelled bands (noise-like program, 45 stations, locals 0 to 30 dB up, power-line bursts
    120 a second): from 7 to 25 dB of crackle over the program down to 12 or 13.5 dB under
    it, the same at MED and HIGH; small pulses on a crowded band neither helped nor harmed; a
    band without pulses never touched. About 7% of a core while on, nothing while off.
  - Impulse blanker, on by default: AM, on the 48 kHz channel ahead of the carrier lock, the
    envelope over +300% (3.3 ms late), patched by prediction too (16 back, for the stations
    10 kHz either side); HF, the same blanker as the noise blanker, pulses 24 dB over the
    median miss. AM stereo: the difference signal muted for about 3 ms around a carrier glitch
    (the envelope under a tenth of the carrier, or the phase past 75 degrees, beyond anything
    C-QUAM sends), counted while the stereo plays.
  - Advanced: the BLANKER lamp, impulses (and stereo glitches) a minute, the share of the time
    the noise blanker patches; on SSB and CW too now. Settings > Blankers serves both receivers.
  - blanker_check.py, the bench check: records 5 s of a noisy frequency, reports the stations,
    the pulses (height, length, spacing, power-line rhythm) and the crackle each setting
    leaves; `--replay` runs a recording here.
  - Tests: tests/test_blanker.py (no GNU Radio: untouched when off or clean; pulses on one
    station patched 30 dB down and counted; a crowded band with locals 30 dB up, pulses hidden
    under them (11 dB over the band's median), the channel 40 dB cleaner at MED and HIGH and
    the band without pulses untouched; chunking; bursts and keyed signals spared; the back-off;
    the glitch gate) and tests/test_blanker_rx.py (the real receivers in GNU Radio: on a weak AM
    station the noise blanker takes 100 pulses a second down to the station's own noise, the
    impulse blanker 10 dB; the same with a local 30 dB stronger 200 kHz away; on USB the
    impulse blanker takes big pulses to the noise and leaves smaller ones to the noise blanker;
    a clean station, +125% peaks and a strong signal keyed on are untouched; over-modulation
    glitches in AM stereo muted, the loudest click 11 dB down). The trace tool's --lift shows
    the three blocks are all that changed in the AM and HF receivers. On the bench: to be heard
    again.
- [ ] Noise reduction for AM and SSB
- [x] Narrowband FM as a mode of its own (not broadcast FM narrowed). Two channel plans cover
  most of what's on the air:
  - 25 kHz channels, about +/-5 kHz deviation: most amateur FM (2 m, 70 cm, 10 m), NOAA
    Weather Radio, marine VHF. Filter about 15 kHz.
  - 12.5 kHz channels, about +/-2.5 kHz deviation: US land mobile since narrowbanding, some
    amateur areas. Filter about 11 kHz.

  Plus an AUTO that picks from the measured deviation, and a level reference per plan so a
  +/-2.5 kHz station doesn't play 6 dB quieter. Squelch, de-emphasis, and CTCSS and DCS tone
  decoding. Done (radio/nfm.py, tests/test_nfm.py in real GNU Radio, 3.10.1 and 3.10.9): its
  own receiver at 960 ksps, +/-8 and +/-5.5 kHz filters (the FM key picks; both play equally
  loud), 750 us de-emphasis with 300 Hz to 3 kHz audio (the CTCSS tone 70 dB down), a noise
  squelch whose levels are dB of C/N (the discriminator's noise just above the voice band
  against its no-signal level: it tracks C/N in 12.5 kHz dB for dB, at any gain, and doubles as
  the signal quality), no noise burst at the end of a transmission, AFC for the dongle's crystal
  error, its own tuner AGC (the S-meter always knows the gain), meters (deviation against 5 or
  2.5 kHz) and an Advanced dashboard. Still to come: AUTO width, DCS. To try on the bench:
  squelch levels against real stations, the AGC's targets, and 750 us against what local
  repeaters send.

## Phase 7: Amateur digital modes

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
  - wide enough IQ stays available (HD Radio needs about 400 kHz around the FM carrier)

No waterfall is planned: this stays a traditional-looking radio, so the receivers only need as
much bandwidth as the station and the measurements take.

## Open questions for the owner

None at the moment.
