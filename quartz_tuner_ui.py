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
    Bands           latching: shows or hides the Bands & Modes unit
    Monitor         latching: shows or hides the monitor unit
    Advanced        the Advanced window (diagnostics); down while it's open
    Settings        reception, panel (nameplate, size, monitor) and display colours;
                    down while it's open
    Tuning down/up  tap: one channel (100 Hz on SSB and CW). Hold: search to the next
                    station. Either key stops a search that is running.
    0 to 9          presets 1 to 24, each band its own (3-9 recall at once; 1 or 2
                    wait a moment for a second digit; 0 then a digit = 01 to 09). On
                    a channelized band (CB, FRS/GMRS, Weather...) they pick channels.
    Memory          then a preset number: stores the station you're on, on this band
    S units         latching: the signal bar in S units, or (up) signal quality;
                    see Display below
    Band            FM, AM (medium wave), LW: those broadcast bands. SW: the shortwave
                    band you were last on; pressed again on shortwave, the next one.
                    Each band comes up in its usual mode, where you left it.
    Fine tune       a small nudge off the band's steps (AM 100 Hz, SSB and CW 10 Hz,
                    narrow FM 500 Hz, FM 5 kHz); the next ordinary step comes back onto them
    Mono            latching Stereo key, both bands: FM mono, or AM with the
                    C-QUAM stereo ignored
    Direct          direct frequency entry, down while you type:
                    then digits (preset keys, keyboard or keypad),
                    Enter to tune, Esc to cancel, Backspace to correct. FM: 885 =
                    88.5 MHz, or type a decimal (162.55); AM: kHz (1720), or a
                    decimal for MHz (5.905); narrow FM: kHz (146940), or a decimal
                    for MHz (146.94). It goes to the band the frequency is in
                    (9580 = 31 m shortwave, in AM), exactly as typed (27.150 on CB
                    stays between channels; the next step goes to the nearest);
                    outside every band it needs Band limits: Override.
    Display         TUNED = the retune has settled and there is a carrier the
                    receiver can hold (narrow FM: one that would open the squelch);
                    STEREO = the back end's stereo indicator; TONE = tone squelch on.
                    The text: the station's own data (RDS, RBDS, PMDS) and its
                    programme type, else the band. (The mode and filter are on the
                    Bands & Modes display; with that unit hidden, the second row.)
                    The bar shows signal quality (POOR / FAIR / GOOD, from the
                    carrier-to-noise) or signal strength in S units (S1 to S9, then
                    +20 to +60; S9 = -73 dBm below 30 MHz, -93 dBm above). S units,
                    S or a click on the bar switches; each band remembers. UNCAL
                    until calibrated in Settings > Reception > S-meter.

Bands & Modes (middle unit): a dot-matrix display, and ten keys under it that do what the
display shows above them
    The display     line 1: the band, the mode and filter (on narrow FM the repeater
                    offset and tone, on SSB and CW the step). Line 2: about the band
                    (its range and the modes of its parts, a repeater's input and the
                    tone heard), or the group or modes shown. Line 3: the filters on
                    offer, the one in use in brackets; narrow FM's squelch. Line 4: what
                    the keys under it do; the band or mode in use in brackets.
    Bands keys      Broadcast (LW, MW, FM), Shortwave (120 m to 11 m, in AM), Amateur
                    (160 m to 23 cm, each part in its usual mode: CW at the bottom of
                    the HF bands, then SSB; narrow FM on 2 m), Public (Air, Marine,
                    Weather, Police/Fire: VHF, UHF, 800 MHz, press again for the next),
                    Personal (CB, FRS/GMRS, MURS): that group's bands on the keys under
                    the display. Shortwave and Amateur need two pages: MORE>, or the
                    group key again. The group follows the band you're on.
    Mode            every kind on its own key: AM; SAM U and SAM L (synchronous AM on
                    the upper or lower sideband: only that side is heard, so
                    interference on the other drops away); FM; NFM (5 kHz deviation)
                    and NFM N (2.5 kHz, 12.5 kHz channels); USB, LSB, CW. SSB, CW and
                    sync AM share one receiver, so moving between them is instant. The
                    dial reads the carrier: CW plays a 600 Hz tone when tuned on it.
                    A band, or a part of a band with a mode of its own, picks its mode.
    Filter          narrower / wider, for the mode in use. AM: 2.5, 5, 8, 10.2 kHz, then
                    WIDE (12 kHz, with a 10 kHz notch for adjacent-channel whistles);
                    with AUTO on in Settings the filter narrows below that on noise and
                    loud neighbours (it starts at 2.5 kHz when AM comes on and opens as
                    the signal allows). FM: narrow, auto, wide. Narrow FM: 2.5 or 5 kHz
                    deviation. SSB 1.8, 2.4, 2.7, 3.0 kHz; CW 250, 500, 1000, 2400 Hz;
                    sync AM 3, 4.5, 6, 8 kHz. Each mode remembers its own. AM fades in
                    over a second when it starts.
    Squelch         - / +: narrow FM's squelch, off then levels 1 to 9 (Settings >
                    Reception too; 3 suits most). It keeps the speaker quiet between
                    transmissions.
    Repeater        Rev: latching, listens on a repeater's input, at the band's usual
                    offset (2 m +/-600 kHz, 1.25 m -1.6 MHz, 70 cm +/-5 MHz, GMRS +5 MHz)
                    or a preset's; the tuner shows the input. Tone: tone squelch on the
                    CTCSS (PL) tone heard (or stored with the preset): only that
                    repeater's transmissions play. A preset stored on a repeater keeps
                    its offset, tone and tone squelch.
    Channels        CB, FRS/GMRS, Weather, MURS and Marine go channel to channel: the
                    tuning keys step channels (CB's 23 comes between 22 and 24, as on a
                    CB), the number keys pick one (4 then 0 = channel 40), and CH shows
                    its number. Memory pressed twice makes the channel you're on the
                    band's home channel; its band key then goes there (Settings >
                    Reception: their band key, home channel or where I left it; channel
                    bands, by channel or ordinary steps).
                    Each band remembers where you left it. Crossing 28.8 MHz the dongle
                    reopens: direct sampling below, its tuner above.

Monitor (bottom unit; needs rx_meters.py next to fm_stereo.py)
    L / R           station audio level after de-emphasis, ahead of volume:
                    PPM (IEC type I fall-back) or VU ballistics, with peak hold.
                    0 dBFS = 75 kHz of L+R deviation (AM: 100% modulation).
    MOD             peak modulation from its own +/-110 kHz path, composite
                    cut at 60 kHz (main channel, pilot and RDS; SCAs are not
                    included), so neither the channel filter nor neighbouring
                    stations can inflate it (FM: kHz and percent of 75 kHz;
                    narrow FM: of 5 or 2.5 kHz; AM: the larger of positive and
                    negative). NOISY
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
F / A band (FM, AM), O mono, W the next filter, P power, D or Enter direct frequency entry,
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
