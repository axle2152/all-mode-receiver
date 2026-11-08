"""
radio/ui/tips.py - Words the window shows: tooltips and RDS program type names.
"""



_PTY_NAMES = ['None', 'News', 'Information', 'Sports', 'Talk', 'Rock', 'Classic Rock',
              'Adult Hits', 'Soft Rock', 'Top 40', 'Country', 'Oldies', 'Soft',
              'Nostalgia', 'Jazz', 'Classical', 'R&B', 'Soft R&B', 'Language',
              'Religious Music', 'Religious Talk', 'Personality', 'Public', 'College']


def _pty_name(pty):
    if pty is None:
        return '--'
    if pty < len(_PTY_NAMES):
        return _PTY_NAMES[pty]
    return {29: 'Weather', 30: 'Emergency Test', 31: 'Emergency'}.get(pty, str(pty))


TIPS = dict(
    quality="How clean the station sounds after all the processing, measured from the FM signal "
            "itself. The white tick shows where it was before the echo canceller. Above 20 dB is clean; "
            "below 10 dB you will hear noise.",
    strength="How far the station stands above the background noise of the band. Strong stations "
             "read 30 dB or more.",
    echo="Distortion from the signal arriving by more than one path, bouncing off buildings and hills. "
         "It makes FM sound harsh and splattery, and a plain SDR can only play it back as-is.",
    eqfix="The echo canceller learns the reflections and subtracts them, the way a car radio's DSP "
          "tuner does. This is how much cleaner it makes the signal. It switches itself in only "
          "when there are echoes to fix.",
    nr="Removes hiss between words and notes on weak stations, using the empty parts of the FM "
       "signal to measure the noise. It stays out of the way on strong stations.",
    static="When the signal briefly drops out, trims the treble and stereo for a few milliseconds so "
           "the crackle doesn't come through. Quick bursts of multipath (picket fencing) pull just the "
           "stereo, where that noise is loudest.",
    stereo="How much stereo separation is let through. Weak signals blend toward mono to stay quiet, "
           "like a car radio. 100% is full stereo.",
    treble="On noisy signals the highs roll off gently, where most of the hiss lives. 100% is full treble.",
    bw="Width of the receiver's filter. On a weak station it narrows to just the station to cut noise; "
       "it never narrows on loud music. The white tick is how wide the station really is.",
    mod="How hard the station is modulating. 75 kHz (the tick) is 100%, the legal limit in the US.",
    am_quality="Carrier-to-noise ratio of the AM station in a 10 kHz channel. Above 25 dB is clean.",
    am_adj="How strong the neighbouring stations are next to this one. High readings mean splatter "
           "and whistles, so the receiver narrows its filter.",
    am_bw="Width of the AM filter: the width chosen in Settings, narrower on weak stations or with "
          "loud neighbours when AUTO is on, 12 kHz with the Wide key.",
    am_pilot="Strength of the stereo pilot tone that tells the receiver a station is in AM stereo: "
             "25 Hz for C-QUAM, 15 Hz for Kahn ISB.",
    am_ib="Impulse blanker: spikes per minute it patched (envelope spikes beyond +300%, which no legal "
          "program reaches) plus C-QUAM phase glitches it muted in the stereo difference.",
    am_nb="Noise blanker: share of the time it's patching pulses. It raises its own threshold if this "
          "passes 1%, so it never eats the program.",
    lamp_blank="A blanker is catching noise pulses or glitches.",
    am_stereo="How much stereo is let through. Weak signals blend toward mono.",
    am_level="Carrier level at the receiver, in dB below full scale.",
    scope_fm="Each dot is a sample of the radio wave. A clean FM signal draws a thin ring; echoes and noise "
             "smear it into a fuzzy donut. Left: as received. Right: after the echo canceller.",
    scope_am="Left: the carrier as received, spinning around the circle. Right: after the receiver "
             "locks onto it, mono AM collapses to a line and C-QUAM stereo opens into an arc.",
    hist_fm="Signal quality over the last 30 seconds: amber as received, cyan after cleanup. Shading "
            "marks the time the echo canceller was working.",
    hist_am="AM signal quality over the last 30 seconds. Shading marks AM stereo.",
    spec="MPX shows the FM baseband: mono audio to 15 kHz, the 19 kHz stereo pilot, stereo audio at "
         "23 to 53 kHz, RDS data at 57 kHz and subcarriers (SCA) at 67 and 92 kHz. RF shows the "
         "radio channel itself.",
    ifeq="The channel spectrum as received (amber) and after the echo canceller (cyan). Multipath "
         "carves notches and ripple into a station; the equalizer flattens them. Grey is the "
         "equalizer's own response, on the scale at the right: the correction it is applying.",
    echoprof="The reflections the equalizer has learned, worked out from its taps: each spike is an "
             "echo, plotted by how late it arrives. Every microsecond of delay is about 1,000 feet "
             "(0.19 mile) of extra path, so this shows how far away the reflecting hills or "
             "buildings are.",
    monline="From the monitor taps: peak deviation from its own measuring path (main channel, pilot and "
            "RDS; SCAs not included), how often it went past "
            "100%, the pilot and RDS injection, subcarriers, the carrier's frequency error (on a "
            "station that's on frequency, this is the dongle's own error), and loudness per BS.1770.",
    moddist="How many times a second the deviation reaches each level, measured from the carrier's "
            "centre over the last 10 seconds. A processed station shows a cliff at its ceiling. A thin "
            "tail beyond the cliff is rare overshoots or stray spikes; a cliff past 75 kHz is a station "
            "running hot. Amber: peaks of frequent recurrence (reached at least once a second). Red: the "
            "highest peak seen.",
    override="Tune anywhere the dongle can reach instead of stopping at the band edges, 100 kHz to "
             "1766 MHz. With direct sampling (AM source direct_q), both bands cross over at 28.8 MHz: "
             "direct sampling below, the quadrature tuner above, and crossing restarts the receiver for "
             "a moment. AM above 28.8 MHz covers the aircraft band; FM is still wideband FM anywhere. "
             "Up/Down steps past the band edge, FREQ entry accepts any of it, and the dot matrix spells "
             "out the exact frequency whenever you're off the band.",
    amstereo="Which AM stereo decoder. AUTO picks by the pilot, like the multi-system radios did: 25 Hz is "
             "Motorola C-QUAM, 15 Hz is Kahn/Hazeltine ISB (left on the lower sideband, right on the upper). "
             "Forcing one decodes with it whatever the station sends.",
    ib="Catches the huge peaks: static crashes and glitches far beyond anything a legal AM station "
       "sends (over +300%), patched before the carrier lock sees them. With C-QUAM stereo it also "
       "briefly mutes the stereo difference around a phase glitch, the kind a carrier hiccup causes. "
       "It never touches normal program, so it's on by default.",
    nb="For frequent smaller pulses: ignition, electric fences, power-line and switching-supply buzz. "
       "It finds them at the dongle's full bandwidth, where they're tallest, and patches them before "
       "the channel filter can stretch them into ticks. LOW, MED and HIGH set how small a pulse it "
       "goes after. If it ever starts patching too much, it backs itself off. Off by default; turn "
       "it up when there's noise to fight.",
    amwidth="How wide the AM filter opens when the Wide key is off. 2.5 kHz digs a weak station out "
            "of a crowded band; 5 kHz is what most AM radios do; 8 kHz is AMAX-class wideband; "
            "10.2 kHz is everything a US station may send (NRSC-1), with the 10 kHz whistle from "
            "neighbouring stations notched out. The Wide key opens it to 12 kHz.",
    amauto="AUTO: the AM filter narrows by itself on noisy stations or when the stations next door "
           "are loud, and never opens wider than the width chosen here. Off: it stays at that width.",
    nrsc="Undo the treble boost that AM stations following NRSC-1 transmit (a modified 75 \u00b5s "
         "curve, +10 dB at 10 kHz), so the full-width AM filter sounds natural instead of bright. "
         "Not every station uses it: if a station sounds dull with this on, it probably doesn't. "
         "AM only; the meters follow it, so they measure what you hear.",
    sync="Meters and the tuner's received indicators (STEREO, TUNED, RDS, the signal ladder and the "
         "RDS text) are held back by however much audio GNU Radio has queued on its way to the sound "
         "card, measured as it plays, so what you see matches what you hear. The sound card's own "
         "buffer, or Bluetooth, isn't counted.",
    glow="How much the tube's light scatters in the glass: a halo around everything lit, and a wider "
         "haze. 0 turns it off.",
    pkmode="What PEAK MOD on the monitor shows. FREQUENT: peaks of frequent recurrence, the FCC's "
           "wording for the 100% limit (the highest level reached at least once a second over 10 s). "
           "HIGHEST: the single highest peak in the last 2 s.",
    swatch="Click to choose this element's colour; right-click for its default. Unlit segments take a "
           "faint tint of the same colour, like a coloured filter over a real VFD.",
    rdsscale="How RDS injection is scaled. IEC 62106: the peak of the standard RDS waveform. "
             "STEREO TOOL: about 1.24 times that, which matches Stereo Tool's RDS level setting.",
    sw_blend="Blend toward mono when the signal gets noisy, like a car radio.",
    sw_hicut="Roll off the treble when the signal gets noisy.",
    sw_nr="Spectral noise reduction on weak stations.",
    sw_static="Trim treble and stereo during brief dropouts, and stereo during multipath bursts.",
    sw_eq="Echo canceller (adaptive equalizer). AUTO uses it only when it measurably helps.",
    sw_bw="Broadcast FM's filter. AUTO narrows it only on weak stations, and never on loud music; "
          "WIDE leaves the whole channel open; NARROW holds it at \u00b135 kHz, for a weak station "
          "beside a strong one. This isn't narrowband FM (ham, NOAA weather), which will be a mode of "
          "its own. AM has its own width setting below.",
    bars="Overall reception, strength and quality together, like the bars on a phone. "
         "Five bars is a clean, strong station; one bar is barely there.",
    rds_ps="The station's name as it sends it over RDS (some stations scroll it), with the call "
           "letters, frequency and program type.",
    rds_rt="RadioText: what's playing, or whatever message the station is sending.",
    rds_detail="PI: the station's RDS code. TP / TA: traffic program, and a traffic announcement in "
               "progress. Music or speech. Clock: the station's own clock. AF: other frequencies "
               "carrying the same station. Groups per second (about 11 when all get through) and the "
               "share of blocks lost to noise or echoes.",
    lamp_stereo="The station's stereo pilot is received and stereo is playing.",
    lamp_rds="RDS data (station name, song text) is being decoded.",
    lamp_echo="The echo canceller is in use.",
    lamp_nr="Noise reduction is removing hiss.",
    lamp_static="Static cut is trimming a dropout or a multipath burst.",
    lamp_mp="Echoes (multipath) detected on this station.",
    lamp_agc="Automatic gain control is setting the tuner gain.",
    lamp_clip="The tuner is overloading. AGC will back the gain off.",
    lamp_lock="The receiver has locked onto the AM carrier.",
    lamp_amst="C-QUAM AM stereo is playing.",
    lamp_pilot="The C-QUAM stereo pilot is present.",
)
