import types

from . import firdes  # noqa: F401  (before __getattr__, or the import would find a block)
from .._block import module_getattr as __getattr__  # noqa: F401

window = types.SimpleNamespace(WIN_HAMMING=0, WIN_HANN=1, WIN_BLACKMAN=2, WIN_RECTANGULAR=3,
                               WIN_KAISER=4, WIN_BLACKMAN_hARRIS=5, WIN_BLACKMAN_HARRIS=5)
