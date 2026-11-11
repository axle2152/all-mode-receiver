"""
radio/ui/frequency.py - What the tuner's frequency digits show for a frequency and tuning step.

Seven big digits, then three small ones. Below 30 MHz the reading is in kHz, from 30 MHz up in
MHz. The big digits go down to 1 kHz, right-aligned against the unit; the small digits carry
what's below 1 kHz. The display shows as many digits as the tuning step needs, and more when
the frequency itself is off the step:

    1080 kHz    90.5 MHz    9580 kHz    14230.50 kHz    146.520 MHz    9999.999.999 MHz
"""

N_BIG, N_FINE = 7, 3
KHZ_BELOW_HZ = 30_000_000             # kHz below this, MHz from here up
MAX_HZ = 9_999_999_999                # 9999.999.999 MHz, the most the digits hold


def freq_digits(freq_hz, step_hz):
    """(big, dots, fine, unit) for the display.

    big:  N_BIG characters, right-aligned (' ' is unlit)
    dots: N_BIG flags, the decimal point after each big digit
    fine: N_FINE characters, left-aligned, the part below 1 kHz
    unit: 'kHz' or 'MHz'
    """
    f = int(round(freq_hz))
    step = max(1, int(round(step_hz)))
    if not 0 <= f <= MAX_HZ:
        return ('-',) * N_BIG, (False,) * N_BIG, (' ',) * N_FINE, 'MHz'
    if f < KHZ_BELOW_HZ:
        unit, unit_hz, big_places = 'kHz', 1_000, 0
    else:
        unit, unit_hz, big_places = 'MHz', 1_000_000, 3
    places, q = 0, unit_hz                    # decimals the step and the frequency need
    while q > 1 and (f % q or step % q):
        q //= 10
        places += 1
    whole, frac = divmod(f, unit_hz)
    frac = f'{frac:0{len(str(unit_hz)) - 1}d}'[:places]
    shown = min(places, big_places)
    big, fine = str(whole) + frac[:shown], frac[shown:]
    dots = [False] * N_BIG
    first = N_BIG - len(big)
    if shown:
        dots[first + len(str(whole)) - 1] = True
    if fine:
        dots[N_BIG - 1] = True
    return tuple(big.rjust(N_BIG)), tuple(dots), tuple(fine.ljust(N_FINE)), unit


def as_text(freq_hz, step_hz):
    """The same reading as one line of text, for tests and messages: '14230.50 kHz'."""
    big, dots, fine, unit = freq_digits(freq_hz, step_hz)
    s = ''.join(c + ('.' if d else '') for c, d in zip(big, dots)).strip()
    return f"{s}{''.join(fine).strip()} {unit}"
