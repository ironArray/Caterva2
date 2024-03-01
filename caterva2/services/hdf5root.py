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

        def visitor(name, node):
            if isinstance(node, h5py.Group):
                return
            # TODO: handle array / frame / (compressed) file distinctly
            dsets.append(self.Path(f'{name}.b2nd'))

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

    def get_dset_meta(self, relpath: Path) -> pydantic.BaseModel:
        dset = self._path_to_dset(relpath)
        # TODO: cache
        b2_array = hdf5.b2empty_from_h5dset(dset)
        return srv_utils.read_metadata(b2_array)

    def get_dset_chunk(self, relpath: Path, nchunk: int) -> bytes:
        dset = self._path_to_dset(relpath)
        # TODO: cache
        b2_chunker = b2chunker_from_h5dset(dset)
        return b2_chunker(nchunk)

    async def awatch_dsets(self) -> AsyncIterator[Collection[Path]]:
        h5path = self.h5file.filename
        async for changes in watchfiles.awatch(h5path):
            self.h5file.close()
            self.h5file = h5py.File(h5path, mode='r')
            # All datasets are supposed to change along with their file.
            yield set(self.walk_dsets())


pubroot.register_root_class(HDF5Root)


def b2chunker_from_h5dset(dset: h5py.Dataset) -> Callable[[int], bytes]:
    b2_args = hdf5.b2args_from_h5dset(dset)

    if b2_args['chunks'] is None:
        b2chunker_from_dataset = b2chunker_from_nonchunked
    elif 'cparams' in b2_args:
        b2chunker_from_dataset = b2chunker_from_blosc2
    else:
        b2chunker_from_dataset = b2chunker_from_chunked

    return b2chunker_from_dataset(dset, b2_args)


def b2chunker_from_blosc2(dset: h5py.Dataset,
                          b2_args: Mapping) -> Callable[[int], bytes]:
    # Blosc2-compressed dataset, just pass chunks as they are.
    # Support both Blosc2 arrays and frames as HDF5 chunks.
    def b2chunker_blosc2(nchunk: int) -> bytes:
        if not (0 <= nchunk < dset.id.get_num_chunks()):
            raise pubroot.NoSuchChunkError(nchunk)
        b2_array = hdf5.b2_from_h5_chunk(dset, nchunk)
        b2_schunk = getattr(b2_array, 'schunk', b2_array)
        if b2_schunk.nchunks < 1:
            raise IOError(f"chunk #{nchunk} of HDF5 node {dset.name!r} "
                          f"contains Blosc2 super-chunk with no chunks")
        if b2_schunk.nchunks > 1:
            raise NotImplementedError(
                f"chunk #{nchunk} of HDF5 node {dset.name!r} "
                f"contains Blosc2 super-chunk with several chunks")
            raise NotImplementedError()
        return b2_schunk.get_chunk(0)
    return b2chunker_blosc2


def b2chunker_from_nonchunked(dset: h5py.Dataset,
                              b2_args: Mapping) -> Callable[[int], bytes]:
    # Contiguous or compact dataset,
    # slurp into Blosc2 array and get chunks from it.
    # Hopefully the data is small enough to be loaded into memory.
    b2_array = blosc2.asarray(
        dset[()],  # ok for arrays & scalars
        **b2_args
    )

    def b2chunker_nonchunked(nchunk: int) -> bytes:
        b2_schunk = b2_array.schunk  # keep array ref to prevent SIGSEGV
        if not (0 <= nchunk < b2_schunk.nchunks):
            raise pubroot.NoSuchChunkError(nchunk)
        return b2_schunk.get_chunk(nchunk)
    return b2chunker_nonchunked


def b2chunker_from_chunked(dset: h5py.Dataset,
                           b2_args: Mapping) -> Callable[[int], bytes]:
    # Non-Blosc2 chunked dataset,
    # load each HDF5 chunk into chunk 0 of compatible Blosc2 array,
    # then get the resulting compressed chunk.
    # Thus, only one chunk worth of data is kept in memory.
    assert dset.chunks == b2_args['chunks']
    b2_array = blosc2.empty(
        shape=dset.chunks, dtype=dset.dtype,  # note that shape is chunkshape
        **b2_args
    )

    def b2chunker_chunked(nchunk: int) -> bytes:
        if not (0 <= nchunk < dset.id.get_num_chunks()):
            raise pubroot.NoSuchChunkError(nchunk)
        chunk_start = dset.id.get_chunk_info(nchunk).chunk_offset
        chunk_slice = tuple(slice(cst, cst + csz, 1)
                            for (cst, csz) in zip(chunk_start, dset.chunks))
        chunk_array = dset[chunk_slice]
        # Always place at the beginning so that it fits in chunk 0.
        b2_slice = tuple(slice(0, n, 1) for n in chunk_array.shape)
        b2_array[b2_slice] = chunk_array
        return b2_array.schunk.get_chunk(0)
    return b2chunker_chunked
