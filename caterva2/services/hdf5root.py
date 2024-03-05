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
        # TODO: cache
        b2_array = hdf5.b2empty_from_h5dset(dset)
        return srv_utils.read_metadata(b2_array)

    def get_dset_chunk(self, relpath: Path, nchunk: int) -> bytes:
        dset = self._path_to_dset(relpath)
        # TODO: cache
        b2_chunker = hdf5.b2chunker_from_h5dset(dset)
        try:
            return b2_chunker(nchunk)
        except IndexError as ie:
            raise pubroot.NoSuchChunkError(*ie.args) from ie

    def open_dset_raw(self, relpath: Path) -> io.RawIOBase:
        # TODO: handle array / frame / (compressed) file distinctly
        raise NotImplementedError(
            "cannot read raw contents of array dataset")

    async def awatch_dsets(self) -> AsyncIterator[Collection[Path]]:
        h5path = self.h5file.filename
        async for changes in watchfiles.awatch(h5path):
            self.h5file.close()
            self.h5file = h5py.File(h5path, mode='r')
            # All datasets are supposed to change along with their file.
            yield set(self.walk_dsets())


pubroot.register_root_class(HDF5Root)


def _is_dataset(node: h5py.Group | h5py.Dataset) -> bool:
    return isinstance(node, h5py.Dataset) and hdf5.h5dset_is_compatible(node)
