import pathlib
import re
from collections.abc import AsyncIterator, Callable, Collection, Iterator
try:
    from typing import Self
except ImportError:  # Python < 3.11
    from typing import TypeVar
    Self = TypeVar('Self', bound='PubRoot')

# Requirements
import h5py
import watchfiles

# Project
from caterva2.services import pubroot


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

        def visitor(name, node):
            if isinstance(node, h5py.Group):
                return
            # TODO: handle array / frame / (compressed) file distinctly
            dsets.append(f'{name}.b2nd')

        self.h5file.visititems(visitor)
        yield from iter(dsets)

    def _path_to_dset(self, relpath: Path) -> h5py.Dataset:
        name = re.sub(r'\.b2(nd|frame)?$', '', str(relpath))
        node = self.h5file.get(name)
        if node is None or isinstance(node, h5py.Group):
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

    # TODO: pending interface methods

    async def awatch_dsets(self) -> AsyncIterator[Collection[Path]]:
        h5path = self.h5file.filename
        async for changes in watchfiles.awatch(h5path):
            self.h5file.close()
            self.h5file = h5py.File(h5path, mode='r')
            # All datasets are supposed to change along with their file.
            yield set(self.walk_dsets())


pubroot.register_root_class(HDF5Root)
