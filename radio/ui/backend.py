"""
radio/ui/backend.py - The receivers the window drives (the AM one is optional).
"""



from ..fm import FMStereoReceiver

__all__ = ['FMStereoReceiver', 'pi_call_candidates', 'AMReceiver', '_AM_ERR', 'band_has']
try:
    from ..fm import pi_call_candidates
except ImportError:
    pi_call_candidates = lambda pi: []
try:                                     # AM band (and C-QUAM AM stereo)
    from ..am import AMReceiver
    _AM_ERR = None
except Exception as _e:
    AMReceiver, _AM_ERR = None, str(_e)

RECEIVERS = {'FM': FMStereoReceiver, 'AM': AMReceiver}


def band_has(band, feature):
    """Whether that band's receiver can do this (its FEATURES); for building the windows
    before any receiver is running."""
    cls = RECEIVERS.get(band)
    return cls is not None and feature in cls.FEATURES
