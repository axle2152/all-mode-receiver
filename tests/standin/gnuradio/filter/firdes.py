from .. import _log


def _make(name):
    def taps(*a, **k):
        _log.log('firdes', name, a, k)
        return [1.0] + [0.0] * 100
    return taps


for _n in ('low_pass', 'high_pass', 'band_pass', 'band_reject', 'hilbert', 'complex_band_pass',
           'low_pass_2', 'high_pass_2', 'band_pass_2', 'root_raised_cosine', 'gaussian'):
    globals()[_n] = _make(_n)
