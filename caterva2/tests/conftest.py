import platform
import sys

import httpx
import numpy as np
import pytest

import caterva2 as cat2

from .conf import configuration  # noqa: F401
from .files import examples_dir, examples_hdf5  # noqa: F401
from .services import services  # noqa: F401
from .sub_auth import sub_user  # noqa: F401

try:  # Python-Blosc2 is optional
    import blosc2
except ImportError:
    blosc2 = None


def pytest_configure(config):
    print("\n" + "-=" * 38)
    print(f"Caterva2 version:      {cat2.__version__}")
    if blosc2 is not None:
        print(f"Python-Blosc2 version: {blosc2.__version__}")
    print(f"HTTPX version:         {httpx.__version__}")
    print(f"NumPy version:         {np.__version__}")
    print(f"Python version:        {sys.version}")
    print(f"Platform:              {platform.platform()}")
    print(f"Rootdir:               {config.rootdir}")
    print("-=" * 38)


@pytest.fixture(scope="session")
def client(services):  # noqa: F811
    urlbase = services.get_urlbase("subscriber")
    return cat2.Client(urlbase)


@pytest.fixture(scope="session")
def auth_client(services, sub_user):  # noqa: F811
    if not sub_user:
        return None

    urlbase = services.get_urlbase("subscriber")
    return cat2.Client(urlbase, sub_user)
