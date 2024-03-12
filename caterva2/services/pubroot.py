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
import pathlib
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Collection, Iterator
try:
    from typing import Self
except ImportError:  # Python < 3.11
    from typing import TypeVar
    Self = TypeVar('Self', bound='PubRoot')

# Requirements
import pydantic


class NoSuchDatasetError(LookupError):
    """The given dataset does not exist."""


class NoSuchChunkError(IndexError):
    """The given chunk does not exist in the dataset."""


class PubRoot(ABC):
    """Abstract class that represents a publisher root."""

    """The class of dataset (relative) paths."""
    Path = pathlib.PurePosixPath

    @classmethod
    @abstractmethod
    def get_maker(cls, target: str) -> Callable[[], Self] | None:
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
        Raise `NoSuchChunkError` if the chunk does not exist.
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
