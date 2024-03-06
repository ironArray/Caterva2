import functools
import io
import logging
import pathlib
import re
from collections.abc import (
    AsyncIterator, Callable, Collection, Iterator, Mapping)
try:
    from typing import Self
except ImportError:  # Python < 3.11
    from typing import TypeVar
    Self = TypeVar('Self', bound='PubRoot')

# Requirements
import blosc2
import h5py
import pydantic
import watchfiles

# Project
from caterva2 import hdf5
from caterva2.services import pubroot, srv_utils


_MAX_CACHED_CHUNKERS = 32
"""Maximum number of dataset chunkers to keep in per-instance LRU cache."""


class HDF5Root:
    Path = pubroot.PubRoot.Path

    @classmethod
    def get_maker(cls, target: str) -> Callable[[], Self]:
        try:
            path = pathlib.Path(target)
            if not h5py.is_hdf5(path):
                return None
        except Exception:
            return None
        return lambda: cls(path)

    def __init__(self, path: pathlib.Path):
        self.h5file = h5py.File(path, mode='r')

    # There must be one cached function per instance,
    # so that it can be reset individually.

    @functools.cached_property
    def _b2args_from_h5dset(self):
        @functools.cache  # TODO: limit size?
        def _getb2args(dset: h5py.Dataset) -> Mapping[str, object]:
            return hdf5.b2args_from_h5dset(dset)
        return _getb2args

    @functools.cached_property
    def _b2chunkers_from_h5dset(self):
        @functools.lru_cache(maxsize=_MAX_CACHED_CHUNKERS)  # only hot datasets
        def _getb2chunkers(dset: h5py.Dataset) -> (
                Callable[[int], bytes],
                Callable[[], Iterator[bytes]]):
            b2_args = self._b2args_from_h5dset(dset)
            return hdf5.b2chunkers_from_h5dset(dset, b2_args)
        return _getb2chunkers

    def _clear_caches(self):
        del self._b2args_from_h5dset
        del self._b2chunkers_from_h5dset

    def walk_dsets(self) -> Iterator[Path]:
        # TODO: either iterate (without accumulation) or cache
        dsets = []
        warn = logging.getLogger().warning

        def visitor(name, node):
            if not _is_dataset(node):
                if not isinstance(node, h5py.Group):
                    warn("skipping incompatible HDF5 dataset: %r", name)
                return
            # TODO: handle array / frame / (compressed) file distinctly
            dsets.append(self.Path(f'{name}.b2nd'))

        self.h5file.visititems(visitor)
        yield from iter(dsets)

    def _path_to_dset(self, relpath: Path) -> h5py.Dataset:
        name = re.sub(r'\.b2(nd|frame)?$', '', str(relpath))
        node = self.h5file.get(name)
        if node is None or not _is_dataset(node):
            raise pubroot.NoSuchDatasetError(relpath)
        return node

    def exists_dset(self, relpath: Path) -> bool:
        try:
            self._path_to_dset(relpath)
        except pubroot.NoSuchDatasetError:
            return False
        return True

    def get_dset_etag(self, relpath: Path) -> str:
        dset = self._path_to_dset(relpath)
        # All datasets have the modification time of their file.
        h5path = pathlib.Path(self.h5file.filename)
        stat = h5path.stat()
        return f'{stat.st_mtime}:{dset.nbytes}'

    def get_dset_meta(self, relpath: Path) -> pydantic.BaseModel:
        dset = self._path_to_dset(relpath)
        b2_args = self._b2args_from_h5dset(dset)
        b2_array = hdf5.b2uninit_from_h5dset(dset, b2_args)
        return srv_utils.read_metadata(b2_array)

    def get_dset_chunk(self, relpath: Path, nchunk: int) -> bytes:
        dset = self._path_to_dset(relpath)
        b2getchunk, _ = self._b2chunkers_from_h5dset(dset)
        try:
            return b2getchunk(nchunk)
        except IndexError as ie:
            raise pubroot.NoSuchChunkError(*ie.args) from ie

    def open_dset_raw(self, relpath: Path) -> io.RawIOBase:
        # TODO: handle array / frame / (compressed) file distinctly
        raise NotImplementedError(
            "cannot read raw contents of array dataset")

    async def awatch_dsets(self) -> AsyncIterator[Collection[Path]]:
        h5path = self.h5file.filename
        old_dsets = set(self.walk_dsets())
        async for _ in watchfiles.awatch(h5path):
            self._clear_caches()
            self.h5file.close()
            self.h5file = h5py.File(h5path, mode='r')
            # All datasets are supposed to change along with their file.
            cur_dsets = set(self.walk_dsets())
            # Old datasets are included in case any of them disappeared.
            yield old_dsets | cur_dsets
            old_dsets = cur_dsets


pubroot.register_root_class(HDF5Root)


def _is_dataset(node: h5py.Group | h5py.Dataset) -> bool:
    return isinstance(node, h5py.Dataset) and hdf5.h5dset_is_compatible(node)
