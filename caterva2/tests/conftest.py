from .conf import configuration  # noqa: F401
from .files import examples_dir, examples_hdf5  # noqa: F401
from .services import services  # noqa: F401

import caterva2 as cat2
import httpx
import numpy as np
import sys
import platform


try:  # Python-Blosc2 is optional
    import blosc2
except ImportError:
    blosc2 = None


def pytest_configure(config):
    print('\n' + '-=' * 38)
    print("Caterva2 version:      %s" % cat2.__version__)
    if blosc2 is not None:
        print("Python-Blosc2 version: %s" % blosc2.__version__)
    print("HTTPX version:         %s" % httpx.__version__)
    print("NumPy version:         %s" % np.__version__)
    print('Python version:        %s' % sys.version)
    print('Platform:              %s' % platform.platform())
    print('Rootdir:               %s' % config.rootdir)
    print('-=' * 38)
