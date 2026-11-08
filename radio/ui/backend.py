"""
radio/ui/backend.py - The receivers the window drives (the AM one is optional).
"""



from ..fm import FMStereoReceiver

__all__ = ['FMStereoReceiver', 'pi_call_candidates', 'AMReceiver', '_AM_ERR']
try:
    from ..fm import pi_call_candidates
except ImportError:
    pi_call_candidates = lambda pi: []
try:                                     # AM band (and C-QUAM AM stereo)
    from ..am import AMReceiver
    _AM_ERR = None
except Exception as _e:
    AMReceiver, _AM_ERR = None, str(_e)
