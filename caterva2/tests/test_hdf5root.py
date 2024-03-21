###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import pytest

import caterva2 as cat2

from .services import TEST_HDF5_ROOT


hdf5root = pytest.importorskip('caterva2.services.hdf5root',
                               reason="HDF5 support not present")


@pytest.fixture
def sub_host(services):
    return services.get_endpoint('subscriber')


@pytest.fixture
def api_root(sub_host):
    return cat2.Root(TEST_HDF5_ROOT, host=sub_host)


def test_not_unsupported(api_root):
    for node in api_root.node_list:
        assert not node.startswith('unsupported/')
