###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Caterva2 - On demand access to remote Blosc2 data repositories"""

__version__ = "0.1"

from .api import bro_host_default, pub_host_default, sub_host_default
from .api import get_roots, subscribe, get_list, get_info, fetch, download
from .api import Root, File, Dataset

import pytest
import pathlib

def test(verbose=False):
    """Run the test suite.

    Parameters
    ----------
    verbose : bool
        If True, run the tests in verbose mode.

    Returns
    -------
    int
        Exit code of the test suite.
    """
    test_dir = pathlib.Path(__file__).parent / 'tests'
    verb = "-v" if verbose else ""
    return pytest.main([verb, test_dir])

__all__ = [
    'bro_host_default',
    'pub_host_default',
    'sub_host_default',
    'get_roots',
    'subscribe',
    'get_list',
    'get_info',
    'fetch',
    'download',
    'Root',
    'File',
    'Dataset',
    'test',
    ]
