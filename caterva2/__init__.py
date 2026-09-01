###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Caterva2 - On demand access to remote Blosc2 data repositories"""

from .client import Array, BasicAuth, Client, Dataset, File, Group, Root, Table

__version__ = "2025.12.4.dev0"
"""The version in use of the Caterva2 package."""

__all__ = [
    "Array",
    "BasicAuth",
    "Client",
    "Dataset",
    "File",
    "Group",
    "Root",
    "Table",
]
"""List of symbols exported by the Caterva2 package."""
