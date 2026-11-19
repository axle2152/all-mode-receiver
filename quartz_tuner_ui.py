#!/usr/bin/env python3
"""
Stereo Tuner and Monitor
========================
A face for the SDR receiver in two stacked units, on the same back end as
the Delco head unit (fm_stereo.FMStereoReceiver / am_receiver.AMReceiver).
Run this file, not fm_stereo.py.

    python quartz_tuner_ui.py
    python quartz_tuner_ui.py --freq 90.5e6 --scale 1.2

The panels are drawn to fit the screen (Settings > Panel > Size: FIT, or a fixed size;
--scale overrides it for one run; 1.0 = 1480 px wide).
    python quartz_tuner_ui.py --no-agc --gain 7.7 --log drive.csv

Tuner (top unit)
    Power           latching; off mutes at once and frees the dongle after 60 s
    Monitor         latching: shows or hides the monitor unit
    Advanced        the Advanced window (diagnostics); down while it's open
    Settings        reception, panel (nameplate, size, monitor) and display colours;
                    down while it's open
    Tuning down/up  tap: one channel. Hold: search to the next station.
                    Either key stops a search that is running.
    0 to 9          preset channels 1 to 24 (3-9 recall at once; 1 or 2 wait a
                    moment for a second digit; 0 then a digit = 01 to 09)
    Memory          then a channel number: stores the station you're on
    S units         latching: the signal bar in S units, or (up) signal quality;
                    see Display below
    Wide            latching AM filter key: 12 kHz, with a 10 kHz notch for
                    adjacent-channel whistles. Off: the AM width chosen in Settings
                    (2.5, 5, 8 or 10.2 kHz). With AUTO on there, it starts at 2.5 kHz
                    when AM comes on, opens up as the signal allows, and narrows on
                    noise and loud neighbours. Lights WIDE on AM. AM fades in over a
                    second when it starts.
    Mono            latching Stereo key, both bands: FM mono, or AM with the
                    C-QUAM stereo ignored
    Display         TUNED = the retune has settled and there is a carrier the
                    receiver can hold; STEREO = the back end's stereo indicator.
                    The bar shows signal quality (POOR / FAIR / GOOD, from the
                    carrier-to-noise) or signal strength in S units (S1 to S9, then
                    +20 to +60; S9 = -73 dBm below 30 MHz, -93 dBm above). S units,
                    S or a click on the bar switches; each band remembers. UNCAL
                    until calibrated in Settings > Reception > S-meter.

Bands (middle unit)
    Shortwave       the broadcast bands 120 m to 11 m (metres), in AM
    Amateur         160 m to 23 cm, in the mode each part of a band is usually heard
                    in (CW at the bottom of the HF bands, then SSB; narrow FM on 2 m)
    LW / MW / FM    long wave, medium wave, FM broadcast
    Public service  Air (AM), Marine, Weather, Police/Fire (VHF, UHF, 800 MHz: press
                    again for the next)
    Personal        CB (AM), FRS/GMRS, MURS
    Repeater        Rev (listen on the input) and Tone (CTCSS)
                    Each band remembers where you left it. Bands that need narrow FM,
                    SSB or CW say NOT YET until those receivers are built.
    Direct          direct frequency entry, down while you type:
                    then digits (preset keys, keyboard or keypad),
                    Enter to tune, Esc to cancel, Backspace to correct. FM: 885 =
                    88.5 MHz, or type a decimal (162.55); AM: kHz (1720), or a
                    decimal for MHz (5.905). It goes to the band the frequency is in
                    (9580 = 31 m shortwave, in AM); outside every band it needs
                    Band limits: Override.

Monitor (bottom unit; needs rx_meters.py next to fm_stereo.py)
    L / R           station audio level after de-emphasis, ahead of volume:
                    PPM (IEC type I fall-back) or VU ballistics, with peak hold.
                    0 dBFS = 75 kHz of L+R deviation (AM: 100% modulation).
    MOD             peak modulation from its own +/-110 kHz path, composite
                    cut at 60 kHz (main channel, pilot and RDS; SCAs are not
                    included), so neither the channel filter nor neighbouring
                    stations can inflate it (FM: kHz and percent of 75 kHz;
                    AM: the larger of positive and negative). NOISY
                    lights instead when noise or multipath ahead of the
                    equalizer would make the peaks read high.
    LUFS            BS.1770 momentary loudness; INTEGRATED, SHORT TERM, RANGE
                    (LRA) and the highest TRUE PEAK since the last retune or Reset
    PEAK MOD        FM: peaks of frequent recurrence (the FCC's wording), the highest
                    deviation reached at least once a second over 10 s; the true
                    maximum and the full distribution are in Advanced. Measured
                    from the carrier's centre, so a frequency offset can't add to it.
                    AM: negative peaks of frequent recurrence, which is how the FCC
                    words the AM limit (100%); clicks and static throw the envelope
                    up, almost never down, so this side stays honest on a noisy band.
                    Positive peaks (125% limit, at any time) are in Advanced.
    PILOT           19 kHz pilot injection (AM: C-QUAM pilot)
    Screen          goniometer and phase correlation on the left; on the right
                    the channel before/after the equalizer (IF), the composite
                    spectrum (MPX) or the multipath echo profile (Echo)

Keyboard: Left / Right tune, Shift+Left / Right search, 0 to 9 presets, M memory,
F / A band, O mono, W wide (AM), P power, D or Enter direct frequency entry,
S signal meter (quality or S units).
Mouse wheel over the tuner display tunes.

Hand-drawn digits: vfd_0.png ... vfd_9.png in a 'vfd' folder next to this file
replace the tuner's drawn frequency segments (vfd_ghost.png: unlit pattern).

Settings live in quartz_tuner_settings.json. On the first run the DSP options
and gain are copied from the Delco's fm_radio_settings.json; after that this
face keeps its own copy, changed from the Settings window. Colours chosen there
are saved per element ("colors"); delete an entry to go back to its default.
"""

from radio.ui.app import main

if __name__ == '__main__':
    main()
