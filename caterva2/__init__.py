###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Caterva2 - On demand access to remote Blosc2 data repositories"""

from .api import (
                  Dataset,
                  File,
                  Root,
                  adduser,
                  bro_host_default,
                  c2context,
                  copy,
                  deluser,
                  download,
                  fetch,
                  get_chunk,
                  get_info,
                  get_list,
                  get_roots,
                  lazyexpr,
                  listusers,
                  move,
                  pub_host_default,
                  remove,
                  sub_host_default,
                  sub_urlbase_default,
                  subscribe,
                  upload,
)
from .api_utils import get_auth_cookie

__version__ = "2025.02.20"
"""The version in use of the Caterva2 package."""

__all__ = [
                  "Dataset",
                  "File",
                  "Root",
                  "adduser",
                  "bro_host_default",
                  "c2context",
                  "copy",
                  "deluser",
                  "download",
                  "fetch",
                  "get_auth_cookie",
                  "get_chunk",
                  "get_info",
                  "get_list",
                  "get_roots",
                  "lazyexpr",
                  "listusers",
                  "move",
                  "pub_host_default",
                  "remove",
                  "sub_host_default",
                  "sub_urlbase_default",
                  "subscribe",
                  "upload",
]
"""List of symbols exported by the Caterva2 package."""
