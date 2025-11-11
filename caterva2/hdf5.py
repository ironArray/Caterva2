###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import json
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


class HDF5Proxy(blosc2.Operand):
    """
    Simple proxy for an HDF5 array (or similar) that can be used with the Blosc2 compute engine.

    This only supports the __getitem__ method. No caching is performed.
    """

    def __init__(self, b2arr, h5file=None, dsetname=None):
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
            h5file = h5py.File(self.fname, "r")
            dsetname = self.dsetname
            self.dset = h5file[dsetname] if dsetname else h5file
            self.b2arr = b2arr
            return

        self.dset = h5file[dsetname] if dsetname else h5file
        self.fname = h5file.filename
        shape = self.dset.shape or ()  # empty datasets have no shape
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
        # Update the Blosc2 array's vlmeta with the HDF5 dataset's attributes
        for aname, avalue in b2attrs.items():
            b2vlmeta.set_vlmeta(aname, avalue, typesize=1)  # non-numeric

    @property
    def shape(self) -> tuple[int, ...]:
        return self.b2arr.shape

    @property
    def ndim(self) -> int:
        return self.b2arr.ndim

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
    def schunk(self):
        # This is basically needed to certificate that it is an NDArray in the LazyArray machinery
        return self.b2arr.schunk

    @property
    def cbytes(self) -> int:
        return self.dset.id.get_storage_size()

    @property
    def cratio(self) -> float:
        return 0 if self.cbytes == 0 else self.nbytes / self.cbytes

    @property
    def nbytes(self) -> int:
        return self.b2arr.nbytes

    @property
    def fields(self) -> Mapping[str, numpy.dtype]:
        return self.b2arr.fields

    # Is this useful in this context?
    # # Provide minimal __array_interface__ to allow NumPy to work with this object
    # @property
    # def __array_interface__(self):
    #     return {
    #         "shape": self.shape,
    #         "typestr": self.dtype.str,
    #         "data": self[()],
    #         "version": 3,
    #     }

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
        result = self.dset[item]
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
            # If item is a slice, one cannot guarantee that original chunks and blocks are valid anymore
            # (e.g. if the slice is stripping any dimension out)
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


class HDF5AttributeEncoder(json.JSONEncoder):
    def default(self, obj):
        # First try to decode bytes as UTF-8 strings (most HDF5 attributes are ASCII)
        if isinstance(obj, bytes):
            try:
                # Try to decode as UTF-8 string first (common case for HDF5 attributes)
                return obj.decode("utf-8")
            except UnicodeDecodeError:
                # Fall back to base64 for true binary data
                import base64

                return {"__bytes__": base64.b64encode(obj).decode("ascii")}

        if isinstance(obj, numpy.ndarray):
            return obj.tolist()
        if isinstance(obj, numpy.generic):
            return obj.item()
        if isinstance(obj, h5py.Empty):
            if obj.dtype.kind not in ["S", "U"]:  # not strings
                return None
            return obj.dtype.type().item() if hasattr(obj.dtype.type(), "item") else obj.dtype.type()

        # Let the base class handle it or raise TypeError
        return super().default(obj)


def serialize_h5_attrs_to_json(h5_attrs, indent=2):
    """
    Convert HDF5 attributes to a JSON string with proper handling of HDF5 types.

    Parameters
    ----------
    h5_attrs : h5py.AttributeManager
        The HDF5 attributes to serialize
    indent : int, optional
        Number of spaces for JSON indentation, default is 2

    Returns
    -------
    str
        A JSON string representation of the attributes
    """
    try:
        # Convert attributes to dict and serialize to JSON
        attrs_dict = dict(h5_attrs.items())
        json_str = json.dumps(attrs_dict, indent=indent, cls=HDF5AttributeEncoder)
    except Exception as e:
        print(f"Error serializing attributes to JSON: {e}")
        # Fallback: serialize individually
        json_dict = {}
        for key, value in h5_attrs.items():
            try:
                json_dict[key] = json.dumps(value, cls=HDF5AttributeEncoder)
            except Exception as e:
                json_dict[key] = f"[Error: {e}]"
        json_str = json.dumps(json_dict, indent=indent)

    return json_str


def create_hdf5_proxies(path: str | os.PathLike) -> Iterator[HDF5Proxy]:
    """Create a generator of HDF5 proxies from the given HDF5 file."""
    attrs_dsetname = "!_attrs_.json.b2"  # the Blosc2 dataset name for the Group attributes in HDF5
    h5file = h5py.File(path, "r")

    # Store HDF5 file attributes as JSON
    dirname = h5file.filename.rsplit(".", 1)[0]
    os.makedirs(dirname, exist_ok=True)
    jsonpath = os.path.join(dirname, attrs_dsetname)
    data = serialize_h5_attrs_to_json(h5file.attrs)
    blosc2.SChunk(data=data.encode("utf-8"), urlpath=jsonpath, mode="w")

    # Recursive function to visit all groups and datasets
    def visit_group(group):
        for name, obj in group.items():
            full_path = f"{group.name}/{name}".lstrip("/")

            if isinstance(obj, h5py.Dataset):
                yield HDF5Proxy(None, h5file, full_path)
            if isinstance(obj, h5py.Group):
                # Store HDF5 group attributes as JSON
                groupname = dirname + "/" + full_path
                os.makedirs(groupname, exist_ok=True)
                jsonpath = os.path.join(groupname, attrs_dsetname)
                data = serialize_h5_attrs_to_json(obj.attrs)
                blosc2.SChunk(data=data.encode("utf-8"), urlpath=jsonpath, mode="w")

                # Recursively visit subgroups
                yield from visit_group(obj)

    # Start from the root group
    yield from visit_group(h5file)

    h5file.close()
