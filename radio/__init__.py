"""
radio - the receivers and the tuner window
==========================================
    radio.receiver   what every receiver shares: dongle input, the lost-dongle watchdog,
                     tone controls and headroom, the add-on hook
    radio.am         the AM receiver (C-QUAM and Kahn stereo)
    radio.fm         the FM stereo receiver (RDS, echo canceller, noise reduction);
                     python3 -m radio.fm runs it without the window
    radio.meters     read-only metering taps for the monitor
    radio.addons     optional decoders that aren't part of this repository
    radio.ui         the tuner and monitor window

quartz_tuner_ui.py, beside this folder, is the program you run.
"""
import os

# The folder that holds quartz_tuner_ui.py: settings and the 'vfd' digit images live there.
TOP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
