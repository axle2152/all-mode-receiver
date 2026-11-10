"""
Install the Crosby add-on (from the all-mode-receiver folder):

    pip install -e addons/crosby

Everything is described here rather than in pyproject.toml on purpose: Ubuntu 22.04's pip
and setuptools can't read a package's name and entry point from pyproject.toml, and install it
as "UNKNOWN" with no entry point, so the radio never finds it. setup.py works everywhere.
"""
from setuptools import setup

setup(
    name='crosby-stereo',
    version='1.0',
    description='Crosby FM-FM compatible stereo (1958), an add-on for the all-mode receiver',
    author='Michael Foley, KK4EKN',
    license='GPL-3.0-or-later',
    python_requires='>=3.10',
    packages=['crosby_stereo'],
    entry_points={'all_mode_receiver.addons': ['crosby = crosby_stereo.addon:Addon']},
)
