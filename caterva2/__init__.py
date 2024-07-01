###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Caterva2 - On demand access to remote Blosc2 data repositories"""

__version__ = "2024.07.01"
"""The version in use of the Caterva2 package."""

from .api import (bro_host_default, pub_host_default, sub_host_default,
                  sub_urlbase_default)
from .api import (get_roots, subscribe, get_list, get_info, fetch, download,
                  lazyexpr)
from .api import Root, File, Dataset
