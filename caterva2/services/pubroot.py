###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import pathlib
from abc import ABC, abstractmethod
from collections.abc import Iterator

# Requirements
import pydantic


class PubRoot(ABC):
    Path = pathlib.PurePosixPath

    @abstractmethod
    def walk_datasets(self) -> Iterator[Path]:
        ...

    @abstractmethod
    def exists_dataset(self, relpath: Path) -> bool:
        ...

    @abstractmethod
    def get_etag(self, relpath: Path) -> str:
        ...

    @abstractmethod
    def get_metadata(self, relpath: Path) -> pydantic.BaseModel:
        ...

    @abstractmethod
    def get_chunk(self, relpath: Path, nchunk: int) -> bytes:
        ...
