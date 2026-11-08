"""A stand-in for GNU Radio, for tests only: it builds every block the receivers ask for,
moves no real signal, and pretends a dongle is delivering samples (see _sim.py).
The tests put this folder first on the path; nothing else ever sees it."""
