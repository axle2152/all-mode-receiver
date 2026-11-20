"""
radio/ui/backend.py - The receivers the window drives (the AM one is optional).
"""



from ..fm import FMStereoReceiver

__all__ = ['FMStereoReceiver', 'pi_call_candidates', 'AMReceiver', '_AM_ERR', 'NFMReceiver', 'HFReceiver',
           'band_has']
try:
    from ..fm import pi_call_candidates
except ImportError:
    pi_call_candidates = lambda pi: []
try:                                     # AM band (and C-QUAM AM stereo)
    from ..am import AMReceiver
    _AM_ERR = None
except Exception as _e:
    AMReceiver, _AM_ERR = None, str(_e)
try:                                     # narrow FM: repeaters, marine, weather, FRS/GMRS...
    from ..nfm import NFMReceiver
except Exception as _e:
    NFMReceiver = None
    print(f'Narrow FM unavailable: {_e}')

try:                                     # SSB, CW, synchronous AM
    from ..hf import HFReceiver
except Exception as _e:
    HFReceiver = None
    print(f'SSB, CW and sync AM unavailable: {_e}')

RECEIVERS = {'FM': FMStereoReceiver, 'AM': AMReceiver, 'NFM': NFMReceiver, 'HF': HFReceiver}


def band_has(band, feature):
    """Whether that band's receiver can do this (its FEATURES); for building the windows
    before any receiver is running."""
    cls = RECEIVERS.get(band)
    return cls is not None and feature in cls.FEATURES
