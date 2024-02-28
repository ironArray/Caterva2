###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Publisher root classes.

This includes an abstract `PubRoot` class defining the interface that concrete
classes must implement to support different publisher root sources.  New
classes may be registered with the `register_root_class()` function.

The `make_root()` function, given a target string argument, tries to find the
adequate class that understands the target and can create a publisher root
instance from it.
"""

import io
import os
import pathlib
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Collection, Iterator
try:
    from typing import Self
except ImportError:  # Python < 3.11
    from typing import TypeVar
    Self = TypeVar('Self', bound='PubRoot')

# Requirements
import blosc2
import pydantic
import watchfiles

# Project
from caterva2.services import srv_utils


class NoSuchDatasetError(LookupError):
    """The given dataset does not exist."""


class PubRoot(ABC):
    """Abstract class that represents a publisher root."""

    """The class of dataset (relative) paths."""
    Path = pathlib.PurePosixPath

    @classmethod
    @abstractmethod
    def get_maker(cls, target: str) -> Callable[[], Self]:
        """Return a callable that returns a root for the given `target`.

        If `target` cannot be used to create an instance of this class,
        return `None`, but no exception should be raised.
        """

    @abstractmethod
    def walk_dsets(self) -> Iterator[Path]:
        """Iterate over the relative paths of datasets in this root."""

    @abstractmethod
    def exists_dset(self, relpath: Path) -> bool:
        """Does the named dataset exist?"""

    @abstractmethod
    def get_dset_etag(self, relpath: Path) -> str:
        """Get a string that varies if the named dataset is modified.

        Raise `NoSuchDatasetError` if the dataset does not exist.
        """

    @abstractmethod
    def get_dset_meta(self, relpath: Path) -> pydantic.BaseModel:
        """Get the metadata of the named dataset.

        Raise `NoSuchDatasetError` if the dataset does not exist.
        """

    @abstractmethod
    def get_dset_chunk(self, relpath: Path, nchunk: int) -> bytes:
        """Get compressed chunk with index `nchunk` of the named dataset.

        Raise `NoSuchDatasetError` if the dataset does not exist.
        """

    @abstractmethod
    def open_dset_raw(self, relpath: Path) -> io.BufferedIOBase:
        """Get a byte reader for the raw contents of the named dataset.

        Raise `NoSuchDatasetError` if the dataset does not exist.
        """

    @abstractmethod
    async def awatch_dsets(self) -> AsyncIterator[Collection[Path]]:
        """Yield a set of datasets that have been modified."""


_registered_classes = []


def register_root_class(cls: type) -> bool:
    """Add a publisher root class to the registry.

    This also registers the class as a virtual subclass of `PubRoot`.

    Return whether the class was added or not (because it was already).
    """
    if cls in _registered_classes:
        return False
    PubRoot.register(cls)
    _registered_classes.append(cls)
    return True


class UnsupportedRootError(Exception):
    """No publisher root class supports the given target."""


def make_root(target: str) -> PubRoot:
    """Return a publisher root instance for the given `target`.

    If no registered publisher root class supports the given `target`,
    raise `UnsupportedRootError`.
    """
    for cls in _registered_classes:
        maker = cls.get_maker(target)
        if maker is not None:
            return maker()
    else:
        raise UnsupportedRootError(f"no publisher root class could be used "
                                   f"for target: {target!r}")


class DirectoryRoot:
    """Represents a publisher root which keeps datasets as files
    in a directory.
    """

    Path = PubRoot.Path

    @classmethod
    def get_maker(cls, target: str) -> Callable[[], Self]:
        try:
            path = pathlib.Path(target)
            if not path.is_dir():
                return None
        except Exception:
            return None
        return lambda: cls(path)

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
        abspath = self.abspath / relpath
        if not abspath.is_file():
            raise NoSuchDatasetError(relpath)
        return abspath

    def exists_dset(self, relpath: Path) -> bool:
        abspath = self._rel_to_abs(relpath)
        return abspath.is_file()

    def get_dset_etag(self, relpath: Path) -> str:
        abspath = self._rel_to_abs(relpath)
        stat = abspath.stat()
        return f'{stat.st_mtime}:{stat.st_size}'

    def get_dset_meta(self, relpath: Path) -> pydantic.BaseModel:
        abspath = self._rel_to_abs(relpath)
        return srv_utils.read_metadata(abspath)

    def get_dset_chunk(self, relpath: Path, nchunk: int) -> bytes:
        abspath = self._rel_to_abs(relpath)
        b2dset = blosc2.open(abspath)
        schunk = getattr(b2dset, 'schunk', b2dset)
        return schunk.get_chunk(nchunk)

    def open_dset_raw(self, relpath: Path) -> io.RawIOBase:
        abspath = self._rel_to_abs(relpath)
        return open(abspath, 'rb')

    async def awatch_dsets(self) -> AsyncIterator[Collection[Path]]:
        async for changes in watchfiles.awatch(proot.abspath):
            relpaths = set(
                proot.Path(pathlib.Path(abspath).relative_to(self.abspath))
                for change, abspath in changes)
            yield relpaths


register_root_class(DirectoryRoot)
