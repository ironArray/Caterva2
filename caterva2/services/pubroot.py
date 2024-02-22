###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import os
import pathlib
from abc import ABC, abstractmethod
from collections.abc import Iterator

# Requirements
import pydantic


class PubRoot(ABC):
    Path = pathlib.PurePosixPath

    @abstractmethod
    def walk_dsets(self) -> Iterator[Path]:
        ...

    @abstractmethod
    def exists_dset(self, relpath: Path) -> bool:
        ...

    @abstractmethod
    def get_dset_etag(self, relpath: Path) -> str:
        ...

    @abstractmethod
    def get_dset_meta(self, relpath: Path) -> pydantic.BaseModel:
        ...

    @abstractmethod
    def get_dset_chunk(self, relpath: Path, nchunk: int) -> bytes:
        ...


class DirectoryRoot:
    Path = PubRoot.Path

    def __init__(self, path: pathlib.Path):
        abspath = path.resolve(strict=True)
        # Force an error for non-dirs or non-readable dirs.
        next(abspath.iterdir())

        self.abspath = abspath

    def walk_dsets(self) -> Iterator[Path]:
        return (self.Path(p.relative_to(self.abspath))
                for p in self.abspath.glob('**/*')
                if not p.is_dir())

    def _rel_to_abs(self, relpath: Path) -> pathlib.Path:
        if relpath.is_absolute():
            raise ValueError(f"path is not relative: {str(relpath)!r}")
        # ``.`` is removed on path instantiation, no need to check for it.
        if os.path.pardir in relpath.parts:
            raise ValueError(f"{str(os.path.pardir)!r} not allowed "
                             f"in path: {str(relpath)!r}")
        return self.abspath / relpath

    def exists_dset(self, relpath: Path) -> bool:
        abspath = self._rel_to_abs(relpath)
        return abspath.is_file()

    # TODO: pending interface methods


PubRoot.register(DirectoryRoot)
