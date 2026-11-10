"""
Install the Crosby add-on: see README.md for the command for your pip (from the
all-mode-receiver folder, `pip install -e addons/crosby` on Windows; Ubuntu 22.04's own pip
needs `python3 -m pip install --user --no-build-isolation -e addons/crosby`).

Everything is described here rather than in pyproject.toml on purpose: Ubuntu 22.04's pip
builds with setuptools 59.6, which can't read a package's name and entry point from
pyproject.toml. setup.py works with every pip.
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
