###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Caterva2 - On demand access to remote Blosc2 data repositories"""

from .client import BasicAuth, Client, Dataset, File, Root

__version__ = "2025.12.0.dev0"
"""The version in use of the Caterva2 package."""

__all__ = [
    "BasicAuth",
    "Client",
    "Dataset",
    "File",
    "Root",
]
"""List of symbols exported by the Caterva2 package."""
