###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import os
from collections.abc import Callable, Iterator, Mapping

# Requirements
import b2h5py.auto  # noqa: F401A
import blosc2
import h5py

# For dynamic import of external HDF5 filters in hdf5plugin module
import msgpack
import numpy
import numpy as np
from caterva2.services.subscriber import ncores


"""The registered identifier for Blosc2 in HDF5 filters."""
BLOSC2_HDF5_FID = 32026


# Warning: Keep the reference to the returned result.
# Losing the reference to the array may result in a segmentation fault.
def b2_from_h5chunk(h5_dset: h5py.Dataset, chunk_index: int) -> blosc2.NDArray | blosc2.SChunk:
    h5chunk_info = h5_dset.id.get_chunk_info(chunk_index)
    return blosc2.open(h5_dset.file.filename, mode="r", offset=h5chunk_info.byte_offset)


def h5dset_is_compatible(h5_dset: h5py.Dataset) -> bool:
    """Is the HDF5 dataset compatible with a Blosc2 dataset?"""
    shape = h5_dset.shape
    if shape is None:
        # Empty dataset (``H5S_NULL``) shape is represented as () in NumPy/Blosc2
        shape = ()
    if len(shape) > getattr(blosc2, "MAX_DIM", 8):
        return False  # too many dimensions
    dtype = h5_dset.dtype
    if dtype.kind in ["O"]:  # other kinds may be missing
        return False
    return True


def b2args_from_h5dset(h5_dset: h5py.Dataset | h5py.Group) -> Mapping[str, object]:
    """Get Blosc2 array creation arguments for the given HDF5 dataset.

    Return a mapping which can be applied to array creation calls.

    This may be an expensive operation with Blosc2-compressed datasets, as it
    requires opening the Blosc2 super-chunk in the first HDF5 chunk to read
    its metadata (so as to return the exact same arguments).
    """
    if isinstance(h5_dset, h5py.Group):
        # Groups should always be supported (manily for attributes)
        return {}
    if not h5dset_is_compatible(h5_dset):
        raise TypeError(
            f"HDF5 dataset {h5_dset} in file {h5_dset.file.filename} is not compatible with Blosc2"
        )

    b2_args = {
        "chunks": h5_dset.chunks,  # None is ok (let Blosc2 decide)
    }

    if (
        h5_dset.chunks is None
        or list(h5_dset._filters) != [f"{BLOSC2_HDF5_FID:#d}"]
        or h5_dset.id.get_num_chunks() < 1
    ):
        return b2_args

    # Blosc2 is the sole filter, direct chunk copy is possible.
    # Get Blosc2 arguments from the first schunk.
    # HDF5 filter parameters are less reliable than these.
    b2_array = b2_from_h5chunk(h5_dset, 0)
    b2_schunk = getattr(b2_array, "schunk", b2_array)
    if hasattr(b2_array, "blocks"):  # rely on cparams blocksize otherwise
        b2_args["blocks"] = b2_array.blocks
    b2_args["cparams"] = b2_schunk.cparams
    b2_args["dparams"] = b2_schunk.dparams

    return b2_args


def _msgpack_h5attr(obj):
    if isinstance(obj, tuple):  # ad hoc Blosc2 tuple handling
        return ["__tuple__", *obj]

    if isinstance(obj, h5py.Empty):
        if obj.dtype.kind not in ["S", "U"]:  # not strings
            # This drops object type but is probably less dangerous
            # than using an actual zero value.
            return None
        obj = obj.dtype.type()  # use empty value of that type

    if isinstance(obj, (numpy.generic, numpy.ndarray)):
        return obj.tolist()

    return obj


def b2attrs_from_h5dset(
    h5_dset: h5py.Dataset | h5py.Group,
    attr_ok: Callable[[h5py.Dataset, str], None] | None = None,
    attr_err: Callable[[h5py.Dataset, str, Exception], None] | None = None,
) -> Mapping[str, object]:
    """Get msgpack-encoded attributes from the given HDF5 dataset.

    NumPy and empty attribute values are first translated into native Python
    values.

    If given, call `attr_ok` or `attr_err` on attribute translation success or
    error, respectively.
    """
    b2_attrs = {}
    for aname, avalue in h5_dset.attrs.items():
        try:
            # This workaround allows NumPy objects
            # and converts them to similar native Python objects
            # which can be encoded by plain msgpack.
            # Of course, some typing information is lost in the process,
            # but the result is portable.
            pvalue = msgpack.packb(avalue, default=_msgpack_h5attr, strict_types=True)
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

    def _b2make_from_h5dset(h5_dset: h5py.Dataset, b2_args=None, b2_attrs=None, **kwds) -> blosc2.NDArray:
        b2_args = b2_args if b2_args is not None else b2args_from_h5dset(h5_dset)
        b2_attrs = b2_attrs if b2_attrs is not None else b2attrs_from_h5dset(h5_dset)
        # Empty dataset (``H5S_NULL``) shape is represented as () in NumPy/Blosc2
        shape = h5_dset.shape if h5_dset.shape is not None else ()
        b2_array = b2make(shape=shape, dtype=h5_dset.dtype, **(b2_args | kwds))
        b2_vlmeta = b2_array.schunk.vlmeta
        for aname, avalue in b2_attrs.items():
            b2_vlmeta.set_vlmeta(aname, avalue, typesize=1)  # non-numeric
        return b2_array

    return _b2make_from_h5dset


b2empty_from_h5dset = _b2maker_from_h5dset(blosc2.empty)
b2uninit_from_h5dset = _b2maker_from_h5dset(blosc2.uninit)


def b2chunkers_from_h5dset(
    h5_dset: h5py.Dataset, b2_args=None
) -> (Callable[[int], bytes], Callable[[], Iterator[bytes]]):
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

    if b2_args["chunks"] is None:
        b2chunkers_from_dataset = b2chunkers_from_nonchunked
    elif "cparams" in b2_args:
        b2chunkers_from_dataset = b2chunkers_from_blosc2
    else:
        b2chunkers_from_dataset = b2chunkers_from_chunked

    return b2chunkers_from_dataset(h5_dset, b2_args)


def b2chunkers_from_blosc2(
    h5_dset: h5py.Dataset, b2_args: Mapping
) -> (Callable[[int], bytes], Callable[[], Iterator[bytes]]):
    # Blosc2-compressed dataset, just pass chunks as they are.
    # Support both Blosc2 arrays and frames as HDF5 chunks.
    def b2getchunk_blosc2(nchunk: int) -> bytes:
        if not (0 <= nchunk < h5_dset.id.get_num_chunks()):
            raise IndexError(nchunk)
        return _b2getchunk_nchunk(nchunk)

    def _b2getchunk_nchunk(nchunk: int) -> bytes:
        b2_array = b2_from_h5chunk(h5_dset, nchunk)
        b2_schunk = getattr(b2_array, "schunk", b2_array)
        # TODO: check if schunk is compatible with creation arguments
        if b2_schunk.nchunks < 1:
            raise OSError(
                f"chunk #{nchunk} of HDF5 node {h5_dset.name!r} "
                f"contains Blosc2 super-chunk with no chunks"
            )
        if b2_schunk.nchunks > 1:
            # TODO: warn, check shape, re-compress as single chunk
            raise NotImplementedError(
                f"chunk #{nchunk} of HDF5 node {h5_dset.name!r} "
                f"contains Blosc2 super-chunk with several chunks"
            )
        return b2_schunk.get_chunk(0)

    def b2iterchunks_blosc2() -> Iterator[bytes]:
        for nchunk in range(h5_dset.id.get_num_chunks()):
            yield _b2getchunk_nchunk(nchunk)

    return b2getchunk_blosc2, b2iterchunks_blosc2


def b2chunkers_from_nonchunked(
    h5_dset: h5py.Dataset, b2_args: Mapping
) -> (Callable[[int], bytes], Callable[[], Iterator[bytes]]):
    # Contiguous or compact dataset,
    # slurp into Blosc2 array and get chunks from it.
    # Hopefully the data is small enough to be loaded into memory.
    b2_array = blosc2.asarray(
        numpy.asanyarray(h5_dset[()]),  # ok for arrays & scalars
        **b2_args,
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


def b2chunkers_from_chunked(
    h5_dset: h5py.Dataset, b2_args: Mapping
) -> (Callable[[int], bytes], Callable[[], Iterator[bytes]]):
    # Non-Blosc2 chunked dataset,
    # load each HDF5 chunk into chunk 0 of compatible Blosc2 array,
    # then get the resulting compressed chunk.
    # Thus, only one chunk worth of data is kept in memory.
    assert h5_dset.chunks == b2_args["chunks"]
    b2_array = blosc2.empty(
        shape=h5_dset.chunks,
        dtype=h5_dset.dtype,  # array shape is chunkshape
        **b2_args,
    )

    def b2getchunk_chunked(nchunk: int) -> bytes:
        if not (0 <= nchunk < h5_dset.id.get_num_chunks()):
            raise IndexError(nchunk)
        chunk_start = h5_dset.id.get_chunk_info(nchunk).chunk_offset
        chunk_slice = tuple(
            slice(cst, cst + csz, 1) for (cst, csz) in zip(chunk_start, h5_dset.chunks, strict=False)
        )
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


def get_slice_info(item, shape, chunks):
    """
    Get both the resulting shape after a slice operation and the affected chunks.

    Parameters
    ----------
    item : int, slice, tuple
        The slicing specification (same as used in array[item])
    shape : tuple
        Shape of the array
    chunks : tuple
        Chunk shape of the array (can be None for non-chunked arrays)

    Returns
    -------
    tuple
        (result_shape, affected_chunks)
        - result_shape: tuple representing shape after slicing
        - affected_chunks: list of chunk indices (in C-order) if slice aligns with chunks,
          or None if the slice can't be made of entire chunks or chunks is None
    """
    # Return shape info for non-chunked arrays
    if chunks is None:
        # Handle empty shape specially
        if not shape:
            return (), None

        # Convert single integer or slice to tuple for uniform handling
        if isinstance(item, (int, slice)):
            item = (item,)

        # If item is empty tuple or None, return original shape
        if not item:
            return shape, None

        # Normalize item to handle Ellipsis
        ndim = len(shape)
        norm_item = [slice(None)] * ndim

        # Handle Ellipsis if present
        ell_idx = None
        for i, s in enumerate(item):
            if s is Ellipsis:
                ell_idx = i
                break

        if ell_idx is not None:
            # Replace Ellipsis with appropriate number of slices
            before = item[:ell_idx]
            after = item[ell_idx + 1 :]
            middle = [slice(None)] * (ndim - len(before) - len(after))
            item = before + middle + after

        # Fill normalized item (up to the original dimensionality)
        for i, s in enumerate(item[:ndim]):
            norm_item[i] = s

        # Calculate resulting shape
        result_shape = []
        for i, (s, dim_size) in enumerate(zip(norm_item, shape)):
            # Integer index reduces dimensionality
            if isinstance(s, int):
                continue

            # Handle slice
            elif isinstance(s, slice):
                start = s.start if s.start is not None else 0
                stop = s.stop if s.stop is not None else dim_size
                step = s.step if s.step is not None else 1

                # Handle negative indices
                if start < 0:
                    start += dim_size
                if stop < 0:
                    stop += dim_size

                # Clamp to valid range
                start = max(0, min(start, dim_size))
                stop = max(start, min(stop, dim_size))

                # Calculate size of this dimension after slicing
                slice_size = max(0, (stop - start + (step - 1)) // step)
                result_shape.append(slice_size)

            # Handle tuple/list of indices for fancy indexing
            elif isinstance(s, (list, tuple, np.ndarray)):
                if hasattr(s, "shape"):  # NumPy array
                    result_shape.append(len(s))
                else:
                    result_shape.append(len(s))

        return tuple(result_shape), None

    # For chunked arrays, handle both shape and chunks
    # Convert single integer or slice to tuple for uniform handling
    if isinstance(item, (int, slice)):
        item = (item,)

    # If shape is scalar or empty, handle specially
    if not shape:
        result_shape = ()
        affected_chunks = [0] if not item or item == (Ellipsis,) else None
        return result_shape, affected_chunks

    # Normalize item to full dimensionality
    ndim = len(shape)
    norm_item = [slice(None)] * ndim

    # Handle Ellipsis if present
    ell_idx = None
    for i, s in enumerate(item):
        if s is Ellipsis:
            ell_idx = i
            break

    if ell_idx is not None:
        # Replace Ellipsis with appropriate number of slices
        before = item[:ell_idx]
        after = item[ell_idx + 1 :]
        middle = [slice(None)] * (ndim - len(before) - len(after))
        item = before + middle + after

    # Fill normalized item
    for i, s in enumerate(item[:ndim]):
        norm_item[i] = s

    # Track whether chunks are perfectly aligned
    chunks_aligned = True
    chunk_ranges = []
    result_shape = []

    # Check each dimension
    for i, (s, dim_size, chunk_size) in enumerate(zip(norm_item, shape, chunks)):
        # Handle integer index
        if isinstance(s, int):
            # Convert negative indices
            if s < 0:
                s += dim_size

            # Integer indexing reduces dimensionality (don't add to result_shape)

            # Check if this index is at a chunk boundary for chunk alignment
            if s % chunk_size != 0 and s % chunk_size != chunk_size - 1:
                chunks_aligned = False

            # For integer index, we'll have a single chunk in this dimension
            chunk_idx = s // chunk_size
            chunk_ranges.append([chunk_idx])

        # Handle slice
        elif isinstance(s, slice):
            # Normalize slice
            start = s.start if s.start is not None else 0
            stop = s.stop if s.stop is not None else dim_size
            step = s.step if s.step is not None else 1

            # Convert negative indices
            if start < 0:
                start += dim_size
            if stop < 0:
                stop += dim_size

            # Clamp to valid range for shape calculation
            start_clamped = max(0, min(start, dim_size))
            stop_clamped = max(start_clamped, min(stop, dim_size))

            # Calculate size of this dimension after slicing
            slice_size = max(0, (stop_clamped - start_clamped + (step - 1)) // step)
            result_shape.append(slice_size)

            # Non-unit step can't be aligned with chunks
            if step != 1:
                chunks_aligned = False

            # Check alignment with chunk boundaries
            if start % chunk_size != 0:
                chunks_aligned = False
            if stop % chunk_size != 0 and stop != dim_size:
                chunks_aligned = False

            # Calculate chunk indices for this dimension
            if chunks_aligned:
                start_chunk = start // chunk_size
                stop_chunk = (stop + chunk_size - 1) // chunk_size
                chunk_ranges.append(list(range(start_chunk, stop_chunk)))

        else:
            # Fancy indexing
            result_shape.append(len(s) if hasattr(s, "__len__") else 1)
            chunks_aligned = False  # Fancy indexing not supported for chunk alignment

    if not chunks_aligned:
        return tuple(result_shape), None

    # Generate all combinations of chunk indices (in C-order)
    affected_chunks = []

    # Calculate strides for C-order chunk indices
    chunk_counts = [(dim_size + chunk_size - 1) // chunk_size for dim_size, chunk_size in zip(shape, chunks)]
    strides = [1]
    for i in range(ndim - 1, 0, -1):
        strides.insert(0, strides[0] * chunk_counts[i])

    # Helper function to recursively generate combinations
    def generate_combinations(dimension, current_index):
        if dimension == ndim:
            affected_chunks.append(current_index)
            return

        for chunk_idx in chunk_ranges[dimension]:
            generate_combinations(dimension + 1, current_index + chunk_idx * strides[dimension])

    generate_combinations(0, 0)
    return tuple(result_shape), affected_chunks


class HDF5Proxy(blosc2.Operand):
    """
    Simple proxy for an HDF5 array (or similar) that can be used with the Blosc2 compute engine.

    This only supports the __getitem__ method. No caching is performed.
    """

    def __init__(self, b2arr, h5file=None, dsetname=None):
        attrs_dsetname = "!_attrs_"  # the Blosc2 dataset name for the Group attributes in HDF5
        if b2arr is not None:
            # The file has been opened already, so we just need to set the filename and dataset name
            self.dsetname = b2arr.vlmeta["_dsetname"]
            # Build the fname from the dsetname, using a relative path
            nlevels = self.dsetname.count("/")
            # Now, get the filename from the HDF5 file by going up nlevels
            fname = os.path.abspath(os.path.dirname(b2arr.urlpath) + "/.." * nlevels)
            # Add the proper extension
            if os.path.exists(fname + ".h5"):
                fname += ".h5"
            elif os.path.exists(fname + ".hdf5"):
                fname += ".hdf5"
            else:
                # We only support .h5 and .hdf5 extensions for now
                raise ValueError(f"File {fname} does not exist with .h5 or .hdf5 extension")
            # Convert to absolute path, and add the extension
            self.fname = fname
            # print("Opening HDF5 file", self.fname)
            h5file = h5py.File(self.fname, "r")
            if attrs_dsetname in self.dsetname:
                dsetname = self.dsetname[:self.dsetname.index(attrs_dsetname)]
            else:
                dsetname = self.dsetname
            # print("h5file, dsetname", h5file, dsetname)
            self.dset = h5file[dsetname] if dsetname else h5file
            self.b2arr = b2arr
            return

        # print("Creating HDF5Proxy from", h5file, dsetname)
        self.dset = h5file[dsetname] if dsetname else h5file
        self.fname = h5file.filename
        if not hasattr(self.dset, "shape") or not hasattr(self.dset, "dtype"):
            # This is probably a Group
            shape = ()
            dtype = numpy.dtype("u1")
            b2dsetname = (dsetname + "/" + attrs_dsetname) if dsetname else attrs_dsetname
        else:
            shape = self.dset.shape or ()   # empty datasets have no shape
            dtype = self.dset.dtype
            b2dsetname = dsetname
        self.dsetname = b2dsetname

        # Store the Blosc2 array below a fake HDF5 hierarchy
        # First, create the necessary directories in the filesystem
        # these will start with the name of the HDF5 file without the extension
        dirname = self.fname.rsplit(".", 1)[0]
        # Use a .b2nd extension for now
        urlpath = os.path.join(dirname, b2dsetname + ".b2nd")

        # Create the directory if it doesn't exist
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        # Create any necessary subdirectories
        subdirs = b2dsetname.split("/")
        for subdir in subdirs[:-1]:
            dirname = os.path.join(dirname, subdir)
            if not os.path.exists(dirname):
                os.makedirs(dirname)

        # print("Creating Blosc2 array in", urlpath, self.dsetname)
        try:
            b2args = b2args_from_h5dset(self.dset)
            self.b2arr = blosc2.empty(
                shape=shape,
                dtype=dtype,
                urlpath=urlpath,
                mode="w",
                **b2args,
            )
        except Exception as e:
            print(f"Unable to create Blosc2 array from {self.fname}/{self.dsetname}: {e}")
            # Remove the attributes and the possible urlpath file and return
            del self.dset
            del self.fname
            del self.dsetname
            if os.path.exists(urlpath):
                os.remove(urlpath)
            return

        # Mark file as special type, and store fname and dsetname in the Blosc2 array's metadata
        b2vlmeta = self.b2arr.schunk.vlmeta
        b2vlmeta["_ftype"] = "hdf5"
        b2vlmeta["_dsetname"] = self.dsetname
        # Use the dset attrs to populate the Blosc2 array's vlmeta
        b2attrs = b2attrs_from_h5dset(self.dset)
        # print("b2attrs", b2attrs)
        # Update the Blosc2 array's vlmeta with the HDF5 dataset's attributes
        for aname, avalue in b2attrs.items():
            b2vlmeta.set_vlmeta(aname, avalue, typesize=1)  # non-numeric

    @property
    def shape(self) -> tuple[int, ...]:
        return self.b2arr.shape

    @property
    def chunks(self):
        return self.b2arr.chunks

    @property
    def blocks(self):
        return self.b2arr.blocks

    @property
    def dtype(self) -> numpy.dtype:
        return self.b2arr.dtype

    @property
    def cparams(self):
        return self.b2arr.cparams

    @property
    def dparams(self):
        return self.b2arr.dparams

    @property
    def fields(self) -> Mapping[str, numpy.dtype]:
        return self.b2arr.fields

    # TODO: would that be useful?
    # @property
    # def chunks(self) -> tuple[int, ...]:
    #     return self.dset.chunks

    def __getitem__(self, item: slice | list[slice] | tuple | None) -> np.ndarray:
        """
        Get a slice as a numpy.ndarray from the HDF5 dataset.

        Parameters
        ----------
        item

        Returns
        -------
        out: numpy.ndarray
            An array with the data slice.
        """
        if isinstance(self.dset, h5py.Group | h5py.File):
            # If the dataset is a group, return an empty array
            return np.zeros(self.shape, dtype=self.dtype)
            # return self.b2arr[item]
        # TODO: optimize this for the case where the Blosc2 codec is used inside HDF5
        if False:
            result_shape, chunk_list = get_slice_info(item, self.shape, self.chunks)
            if chunk_list is None:
                result = self.dset[item]
            else:
                # Build the resulting array as empty
                result = blosc2.empty(
                    shape=result_shape,
                    dtype=self.dtype,
                    chunks=self.chunks,
                )
                # Get the chunks from the HDF5 dataset
                # TODO: it is probably better to use the b2h5py package for this
        # If the result is Empty, return it as a numpy array
        if isinstance(result, h5py.Empty):
            result = np.zeros((), dtype=self.dtype)
        return result

    def slice(self, item: slice | list[slice] | tuple | None) -> blosc2.NDArray:
        """
        Get a slice as a Blosc2 array from the HDF5 dataset.

        Parameters
        ----------
        item

        Returns
        -------
        out: NDArray
            An array with the data slice.
        """
        result = self.dset[item]
        # If the result is Empty, return it an empty array
        if isinstance(result, h5py.Empty):
            result = blosc2.empty((), dtype=self.dtype)
        return blosc2.asarray(result, cparams=self.b2arr.cparams)

    def indices(self, order: str | list[str] | None = None, **kwargs) -> blosc2.NDArray:
        """
        Get the indices of the HDF5 dataset.

        Parameters
        ----------
        order: str | list[str] | None
            The order of the indices. If None, use the default order.
        kwargs: Any
            Additional arguments to pass to the Blosc2 array.

        Returns
        -------
        out: NDArray
            An array with the indices.
        """
        # TODO: optimize this for the case where the Blosc2 codec is used inside HDF5
        nda = blosc2.asarray(self.dset[:], cparams=self.b2arr.cparams, **kwargs)
        return nda.indices(order=order, **kwargs)

    def sort(self, order: str | list[str] | None = None, **kwargs) -> blosc2.NDArray:
        """
        Sort the HDF5 dataset.

        Parameters
        ----------
        order: str | list[str] | None
            The order of the indices. If None, use the default order.
        kwargs: Any
            Additional arguments to pass to the Blosc2 array.

        Returns
        -------
        out: NDArray
            An array with the sorted data.
        """
        # TODO: optimize this for the case where the Blosc2 codec is used inside HDF5
        nda = blosc2.asarray(self.dset[:], cparams=self.b2arr.cparams, **kwargs)
        return nda.sort(order=order, **kwargs)

    def to_cframe(self, item=()) -> bytes:
        # Convert the HDF5 dataset to a Blosc2 CFrame
        # TODO: optimize this for the case where the Blosc2 codec is used inside HDF5 and item == ()
        data = self[item]
        if not item and not hasattr(self.dset.dtype, "shape"):
            # For the whole thing, lets specify chunks and blocks
            array = blosc2.asarray(data, cparams=self.b2arr.cparams, chunks=self.chunks, blocks=self.blocks)
        else:
            # If item is a slice, we better not specify chunks and blocks
            array = blosc2.asarray(data, cparams=self.b2arr.cparams)
        return array.to_cframe()

    def __del__(self):
        # Close the HDF5 file when the proxy is deleted
        if hasattr(self, "h5file"):
            self.h5file.close()

    # TODO: would that be useful?
    # def get_chunk(self, chunk_index: int) -> bytes:
    #     """
    #     Get a chunk from the HDF5 dataset, compressed via Blosc2.
    #
    #     Parameters
    #     ----------
    #     chunk_index
    #
    #     Returns
    #     -------
    #     out: bytes
    #         The chunk data.
    #     """
    #     h5chunk_info = self.dset.id.get_chunk_info(chunk_index)
    #     # Compute the slice corresponding to the chunk
    #     chunk_slice = tuple(
    #         slice(cst, cst + csz, 1)
    #         for (cst, csz) in zip(h5chunk_info.chunk_offset, self.dset.chunks, strict=False)
    #     )
    #     # Now, get the chunk data from the HDF5 dataset (uncompressed)
    #     chunk_data = self.dset[chunk_slice]
    #     # Compress the chunk data using Blosc2
    #     return blosc2.compress2(
    #         chunk_data.tobytes(),
    #         cparams=self.b2arr.cparams,
    #     )

    def remove(self):
        # Remove the Blosc2 array from the filesystem
        if hasattr(self, "b2arr"):
            os.remove(self.b2arr.urlpath)


def create_hdf5_proxies(
    path: str | os.PathLike,
    b2_args=None,
) -> Iterator[HDF5Proxy]:
    """Create a generator of HDF5 proxies from the given HDF5 file."""
    if b2_args is None:
        b2_args = {}
    h5file = h5py.File(path, "r")

    # Recursive function to visit all groups and datasets
    def visit_group(group):
        yield HDF5Proxy(None, h5file, "")
        for name, obj in group.items():
            full_path = f"{group.name}/{name}".lstrip("/")

            if isinstance(obj, h5py.Dataset | h5py.Group):
                yield HDF5Proxy(None, h5file, full_path)
            if isinstance(obj, h5py.Group):
                # Recursively visit subgroups
                yield from visit_group(obj)

    # Start from the root group
    yield from visit_group(h5file)

    h5file.close()


# def remove_hdf5_proxies(
#     h5fname: str,
# ) -> None:
#     """Remove the HDF5 proxies from the given HDF5 file.
#
#     The proxies will be removed for each dataset in the file.
#     """
#     # TODO
