"""
Install the BTSC add-on: see README.md for the command for your pip (from the
all-mode-receiver folder, `pip install -e addons/btsc` on Windows; Ubuntu 22.04's own pip
needs `python3 -m pip install --user --no-build-isolation -e addons/btsc`).

Described here rather than in pyproject.toml on purpose: Ubuntu 22.04's pip builds with
setuptools 59.6, which can't read a package's name and entry point from pyproject.toml.
"""
from setuptools import setup

setup(
    name='btsc-stereo',
    version='1.0',
    description='BTSC (MTS) stereo, US analog TV sound, an add-on for the all-mode receiver',
    author='Michael Foley, KK4EKN',
    license='GPL-3.0-or-later',
    python_requires='>=3.10',
    packages=['btsc_stereo'],
    entry_points={'all_mode_receiver.addons': ['btsc = btsc_stereo.addon:Addon']},
)
