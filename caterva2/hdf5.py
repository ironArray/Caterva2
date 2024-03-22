###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

from collections.abc import Callable, Iterator, Mapping

# Requirements
import blosc2
import h5py
import hdf5plugin  # enable Blosc2 support in HDF5
import msgpack
import numpy


"""The registered identifier for Blosc2 in HDF5 filters."""
BLOSC2_HDF5_FID = 32026


# Warning: Keep the reference to the returned result.
# Losing the reference to the array may result in a segmentation fault.
def b2_from_h5chunk(h5_dset: h5py.Dataset,
                    chunk_index: int) -> (blosc2.NDArray | blosc2.SChunk):
    h5chunk_info = h5_dset.id.get_chunk_info(chunk_index)
    return blosc2.open(h5_dset.file.filename, mode='r',
                       offset=h5chunk_info.byte_offset)


def h5dset_is_compatible(h5_dset: h5py.Dataset) -> bool:
    """Is the HDF5 dataset compatible with a Blosc2 dataset?"""
    shape = h5_dset.shape
    if shape is None:
        return False  # empty dataspace (``H5S_NULL``)
    if len(shape) > getattr(blosc2, 'MAX_DIM', 8):
        return False  # too many dimensions
    dtype = h5_dset.dtype
    if dtype.ndim != 0 or dtype.fields is not None:
        return False  # array or compound dtype
    if dtype.kind in ['O']:  # other kinds may be missing
        return False
    return True


def b2args_from_h5dset(h5_dset: h5py.Dataset) -> Mapping[str, object]:
    """Get Blosc2 array creation arguments for the given HDF5 dataset.

    Return a mapping which can be applied to array creation calls.

    This may be an expensive operation with Blosc2-compressed datasets, as it
    requires opening the Blosc2 super-chunk in the first HDF5 chunk to read
    its metadata (so as to return the exact same arguments).
    """
    if not h5dset_is_compatible(h5_dset):
        raise TypeError("HDF5 dataset is not compatible with Blosc2")

    b2_args = dict(
        chunks=h5_dset.chunks,  # None is ok (let Blosc2 decide)
    )

    if (h5_dset.chunks is None
            or list(h5_dset._filters) != [f'{BLOSC2_HDF5_FID:#d}']
            or h5_dset.id.get_num_chunks() < 1):
        return b2_args

    # Blosc2 is the sole filter, direct chunk copy is possible.
    # Get Blosc2 arguments from the first schunk.
    # HDF5 filter parameters are less reliable than these.
    b2_array = b2_from_h5chunk(h5_dset, 0)
    b2_schunk = getattr(b2_array, 'schunk', b2_array)
    if hasattr(b2_array, 'blocks'):  # rely on cparams blocksize otherwise
        b2_args['blocks'] = b2_array.blocks
    b2_args['cparams'] = b2_schunk.cparams
    b2_args['dparams'] = b2_schunk.dparams

    return b2_args


def _msgpack_h5attr(obj):
    if isinstance(obj, tuple):  # ad hoc Blosc2 tuple handling
        return ['__tuple__', *obj]

    if isinstance(obj, h5py.Empty):
        if obj.dtype.kind not in ['S', 'U']:  # not strings
            # This drops object type but is probably less dangerous
            # than using an actual zero value.
            return None
        obj = obj.dtype.type()  # use empty value of that type

    if isinstance(obj, (numpy.generic, numpy.ndarray)):
        return obj.tolist()

    return obj


def b2attrs_from_h5dset(
        h5_dset: h5py.Dataset,
        attr_ok: Callable[[h5py.Dataset, str], None] = None,
        attr_err: Callable[[h5py.Dataset, str, Exception], None] = None) -> (
            Mapping[str, object]):
    """Get msgpack-encoded attributes from the given HDF5 dataset.

    NumPy and empty attribute values are first translated into native Python
    values.

    If given, call `attr_ok` or `attr_err` on attribute translation success or
    error, respectively.
    """
    b2_attrs = {}
    for (aname, avalue) in h5_dset.attrs.items():
        try:
            # This workaround allows NumPy objects
            # and converts them to similar native Python objects
            # which can be encoded by plain msgpack.
            # Of course, some typing information is lost in the process,
            # but the result is portable.
            pvalue = msgpack.packb(avalue, default=_msgpack_h5attr,
                                   strict_types=True)
        except Exception as e:
            if attr_err:
                attr_err(h5_dset, aname, e)
        else:
            b2_attrs[aname] = pvalue
            if attr_ok:
                attr_ok(h5_dset, aname)
    return b2_attrs


def _b2maker_from_h5dset(b2make: Callable[..., blosc2.NDArray]):
    """Get a factory to create a Blosc2 array compatible with an HDF dataset.

    The result may be called with the dataset and optional Blosc2 creation
    arguments and msgpack-encoded attributes (which will be added to the
    array's variable-length metadata), plus other keyword arguments.  The
    dataset-compatible Blosc2 array will be created using the `b2make`
    callable.

    If the aforementioned optional parameters are not given, they will be
    extracted anew from the HDF5 dataset (which may be an expensive operation
    depending on the case).
    """
    def _b2make_from_h5dset(h5_dset: h5py.Dataset,
                            b2_args=None, b2_attrs=None,
                            **kwds) -> blosc2.NDArray:
        b2_args = (b2_args if b2_args is not None
                   else b2args_from_h5dset(h5_dset))
        b2_attrs = (b2_attrs if b2_attrs is not None
                    else b2attrs_from_h5dset(h5_dset))

        b2_array = b2make(shape=h5_dset.shape, dtype=h5_dset.dtype,
                          **(b2_args | kwds))
        b2_vlmeta = b2_array.schunk.vlmeta
        for (aname, avalue) in b2_attrs.items():
            b2_vlmeta.set_vlmeta(aname, avalue, typesize=1)  # non-numeric
        return b2_array
    return _b2make_from_h5dset


b2empty_from_h5dset = _b2maker_from_h5dset(blosc2.empty)
b2uninit_from_h5dset = _b2maker_from_h5dset(blosc2.uninit)


def b2chunkers_from_h5dset(h5_dset: h5py.Dataset, b2_args=None) -> (
        Callable[[int], bytes],
        Callable[[], Iterator[bytes]]):
    """Get by-index and iterator chunkers for the given dataset.

    The first returned value (``getchunk``) can be called with an integer
    index to get the compressed Blosc2 chunk with that index.  An `IndexError`
    is raised if the index is out of bounds.

    The second returned value (``iterchunks``) can be called to get an
    iterator that yields compressed Blosc2 chunks in order.

    Chunks will be compatible with Blosc2 array construction arguments, either
    as provided by a non-empty mapping `b2_args`, or otherwise inferred from
    the dataset.  The former may be useful if you have already called
    `b2args_from_h5dset()` on the dataset.  The chunks may be stored straight
    away in order in a Blosc2 super-chunk without further processing.

    Some chunkers may hold a sizable amount of memory (especially for datasets
    with big chunks, or big non-chunked datasets).
    """
    b2_args = b2_args or b2args_from_h5dset(h5_dset)

    if b2_args['chunks'] is None:
        b2chunkers_from_dataset = b2chunkers_from_nonchunked
    elif 'cparams' in b2_args:
        b2chunkers_from_dataset = b2chunkers_from_blosc2
    else:
        b2chunkers_from_dataset = b2chunkers_from_chunked

    return b2chunkers_from_dataset(h5_dset, b2_args)


def b2chunkers_from_blosc2(h5_dset: h5py.Dataset, b2_args: Mapping) -> (
        Callable[[int], bytes],
        Callable[[], Iterator[bytes]]):
    # Blosc2-compressed dataset, just pass chunks as they are.
    # Support both Blosc2 arrays and frames as HDF5 chunks.
    def b2getchunk_blosc2(nchunk: int) -> bytes:
        if not (0 <= nchunk < h5_dset.id.get_num_chunks()):
            raise IndexError(nchunk)
        return _b2getchunk_nchunk(nchunk)

    def _b2getchunk_nchunk(nchunk: int) -> bytes:
        b2_array = b2_from_h5chunk(h5_dset, nchunk)
        b2_schunk = getattr(b2_array, 'schunk', b2_array)
        # TODO: check if schunk is compatible with creation arguments
        if b2_schunk.nchunks < 1:
            raise IOError(f"chunk #{nchunk} of HDF5 node {h5_dset.name!r} "
                          f"contains Blosc2 super-chunk with no chunks")
        if b2_schunk.nchunks > 1:
            # TODO: warn, check shape, re-compress as single chunk
            raise NotImplementedError(
                f"chunk #{nchunk} of HDF5 node {h5_dset.name!r} "
                f"contains Blosc2 super-chunk with several chunks")
        return b2_schunk.get_chunk(0)

    def b2iterchunks_blosc2() -> Iterator[bytes]:
        for nchunk in range(h5_dset.id.get_num_chunks()):
            yield _b2getchunk_nchunk(nchunk)

    return b2getchunk_blosc2, b2iterchunks_blosc2


def b2chunkers_from_nonchunked(h5_dset: h5py.Dataset, b2_args: Mapping) -> (
        Callable[[int], bytes],
        Callable[[], Iterator[bytes]]):
    # Contiguous or compact dataset,
    # slurp into Blosc2 array and get chunks from it.
    # Hopefully the data is small enough to be loaded into memory.
    b2_array = blosc2.asarray(
        numpy.asanyarray(h5_dset[()]),  # ok for arrays & scalars
        **b2_args
    )

    def b2getchunk_nonchunked(nchunk: int) -> bytes:
        if not (0 <= nchunk < b2_array.schunk.nchunks):
            raise IndexError(nchunk)
        return _b2getchunk_nchunk(nchunk)

    def _b2getchunk_nchunk(nchunk: int) -> bytes:
        return b2_array.schunk.get_chunk(nchunk)

    def b2iterchunks_nonchunked() -> Iterator[bytes]:
        for chunk_info in b2_array.schunk.iterchunks_info():
            yield _b2getchunk_nchunk(chunk_info.nchunk)

    return b2getchunk_nonchunked, b2iterchunks_nonchunked


def b2chunkers_from_chunked(h5_dset: h5py.Dataset, b2_args: Mapping) -> (
        Callable[[int], bytes],
        Callable[[], Iterator[bytes]]):
    # Non-Blosc2 chunked dataset,
    # load each HDF5 chunk into chunk 0 of compatible Blosc2 array,
    # then get the resulting compressed chunk.
    # Thus, only one chunk worth of data is kept in memory.
    assert h5_dset.chunks == b2_args['chunks']
    b2_array = blosc2.empty(
        shape=h5_dset.chunks, dtype=h5_dset.dtype,  # array shape is chunkshape
        **b2_args
    )

    def b2getchunk_chunked(nchunk: int) -> bytes:
        if not (0 <= nchunk < h5_dset.id.get_num_chunks()):
            raise IndexError(nchunk)
        chunk_start = h5_dset.id.get_chunk_info(nchunk).chunk_offset
        chunk_slice = tuple(slice(cst, cst + csz, 1)
                            for (cst, csz) in zip(chunk_start, h5_dset.chunks))
        return _b2getchunk_slice(chunk_slice)

    def _b2getchunk_slice(chunk_slice) -> bytes:
        chunk_array = numpy.asanyarray(h5_dset[chunk_slice])
        # Always place at the beginning so that it fits in chunk 0.
        b2_slice = tuple(slice(0, n, 1) for n in chunk_array.shape)
        b2_array[b2_slice] = chunk_array
        return b2_array.schunk.get_chunk(0)

    def b2iterchunks_chunked() -> Iterator[bytes]:
        for chunk_slice in h5_dset.iter_chunks():
            yield _b2getchunk_slice(chunk_slice)

    return b2getchunk_chunked, b2iterchunks_chunked
