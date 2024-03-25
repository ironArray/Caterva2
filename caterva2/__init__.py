###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Caterva2 - On demand access to remote Blosc2 data repositories"""

__version__ = "0.2.0"
"""The version in use of the Caterva2 package."""

from .api import bro_host_default, pub_host_default, sub_host_default
from .api import get_roots, subscribe, get_list, get_info, fetch, download
from .api import Root, File, Dataset
