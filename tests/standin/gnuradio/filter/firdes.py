def _taps(*a, **k):
    return [1.0] + [0.0] * 100


low_pass = high_pass = band_pass = band_reject = hilbert = complex_band_pass = _taps
low_pass_2 = high_pass_2 = band_pass_2 = root_raised_cosine = gaussian = _taps
