"""
radio/ui - the tuner and monitor window
=======================================
    app        TunerApp, the window itself, and main()
    settings   the Settings window (a part of TunerApp)
    advanced   the Advanced window (a part of TunerApp)
    monitor    what the monitor unit's meters show; the audio-delay line
    loudness   ITU BS.1770 / EBU R128 loudness from the meter blocks
    vfd, crt   the tuner and monitor displays, and the scope screen
    widgets    keys, buttons, tooltips, meter bars, lamps
    art        drawn artwork: segments, brushed plates, keys, glass
    fonts      finding fonts on Windows and Linux; drawing text
    theme      geometry, colours and layout
    constants  tuning, timing and monitor constants; where settings are kept
    tips       the words shown in tooltips; RDS program type names
    backend    the receivers the window drives
"""
