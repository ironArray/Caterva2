###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import pytest


hdf5root = pytest.importorskip('caterva2.services.hdf5root',
                               reason="HDF5 support not present")


def test_test(services):
    pass
