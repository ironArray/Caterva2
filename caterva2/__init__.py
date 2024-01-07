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
from .api import get_roots, Root, File, Dataset

__all__ = [
    'bro_host_default',
    'pub_host_default',
    'sub_host_default',
    'get_roots',
    'Root',
    'File',
    'Dataset',
    ]
