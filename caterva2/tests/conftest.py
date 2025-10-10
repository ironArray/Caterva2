import os
import platform
import sys

import blosc2
import httpx
import numpy as np
import pytest

import caterva2 as cat2
from caterva2 import utils

from .files import examples_dir  # noqa: F401
from .services import services, sub_user  # noqa: F401


def pytest_configure(config):
    print("\n" + "-=" * 38)
    print(f"Caterva2 version:      {cat2.__version__}")
    print(f"Python-Blosc2 version: {blosc2.__version__}")
    print(f"HTTPX version:         {httpx.__version__}")
    print(f"NumPy version:         {np.__version__}")
    print(f"Python version:        {sys.version}")
    print(f"Platform:              {platform.platform()}")
    print(f"Rootdir:               {config.rootdir}")
    print("-=" * 38)


@pytest.fixture(scope="session")
def client(services):  # noqa: F811
    urlbase = services.get_urlbase()
    return cat2.Client(urlbase)


@pytest.fixture(scope="session")
def auth_client(services, sub_user):  # noqa: F811
    if not sub_user:
        return None

    urlbase = services.get_urlbase()
    return cat2.Client(urlbase, sub_user)


@pytest.fixture(scope="session")
def client_conf():
    """Caterva2 client configuration"""
    # Always use the caterva2.toml in the tests directory
    parent = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(parent, "caterva2.toml")
    return utils.get_client_conf(conf=config_file, server="pytest")


@pytest.fixture(scope="session")
def server_conf():
    """Caterva2 server configuration"""
    return utils.get_server_conf()
