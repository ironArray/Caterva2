###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Export datasets in an HDF5 file to a Caterva2 root.

The HDF5 file is opened in read-only mode, and the Caterva2 root directory is
created anew (it must not exist).  For each group in the HDF5 file hierarchy,
a directory with the same path name is created under the Caterva2 root.  For
each dataset, a Blosc2 dataset with the same path name (plus the ``.b2nd``
extension) is created under the Caterva2 root.

The only datasets supported currently are those which can be converted to
NumPy arrays.  Dataset attributes are supported as Blosc2 vlmeta entries if
they can be serialized with msgpack.  Group attributes are not supported yet.
Moreover, for the moment datasets get compressed with default Blosc2
parameters.

Warning: For the moment, the data in each dataset is read and decompressed
into memory in its entirety.

Datasets or attributes which are unsupported or fail to be converted are
simply reported and skipped, and they do not cause the program to fail.
"""

import logging
import os
import pathlib
import sys

import blosc2
import h5py
import hdf5plugin
import msgpack

from collections.abc import Callable, Iterable, Mapping

from blosc2 import blosc2_ext


"""The registered identifier for Blosc2 in HDF5 filters."""
BLOSC2_HDF5_FID = '32026'


def create_directory(name: str, node: h5py.Group,
                     c2_root: pathlib.Path) -> None:
    if len(node.attrs.keys()) > 0:
        logging.warning(f"Exporting group attributes "
                        f"is not supported yet: {name!r}")

    path = c2_root / name
    try:
        path.mkdir()  # parent should exist, not itself
    except OSError as ose:
        logging.error(f"Failed to create directory "
                      f"for node: {name!r} -> {ose!r}")
        return
    logging.info(f"Exported group: {name!r} => {str(path)!r}")


# Warning: Keep the reference to the returned result.
# Losing the reference to the array may result in a segmentation fault.
def b2_from_h5_chunk(node: h5py.Dataset,
                     chunk_index: int) -> (blosc2.NDArray | blosc2.SChunk):
    h5chunk_info = node.id.get_chunk_info(chunk_index)
    b2 = blosc2.open(node.file.filename, mode='r',
                     offset=h5chunk_info.byte_offset)
    return b2


def b2mkempty_b2chunkit_from_dataset(node: h5py.Dataset) -> (
        Callable[..., blosc2.NDArray],
        Iterable[bytes]):
    """Get empty Blosc2 array maker and compressed chunk iterator from `node`.

    The first returned value can be called to create an empty Blosc2 array
    with prepared construction arguments extracted from the HDF5 dataset
    `node`.  By default it is created without storage, but that may be changed
    by passing additional keyword arguments like ``urlpath``.

    The second returned value is an iterator that yields compressed Blosc2
    chunks compatible with the construction arguments used by the previous
    callable, for the data in `node`.  They may be stored straight away in
    order in a Blosc2 super-chunk without further processing.
    """
    b2_args = dict(
        chunks=node.chunks,  # None is ok (let Blosc2 decide)
    )

    if node.chunks is None:
        b2chunkit_from_dataset = b2chunkit_from_nonchunked
    elif BLOSC2_HDF5_FID in node._filters and node.id.get_num_chunks() > 0:
        # Get Blosc2 arguments from the first schunk.
        # HDF5 filter parameters are less reliable than these.
        b2_array = b2_from_h5_chunk(node, 0)
        b2_schunk = getattr(b2_array, 'schunk', b2_array)
        if hasattr(b2_array, 'blocks'):
            b2_args['blocks'] = b2_array.blocks
        b2_args['cparams'] = b2_schunk.cparams
        b2_args['dparams'] = b2_schunk.dparams
        b2chunkit_from_dataset = b2chunkit_from_blosc2
    else:
        b2chunkit_from_dataset = b2chunkit_from_chunked

    def b2_make_empty(**kwds) -> blosc2.NDArray:
        b2_empty = blosc2.empty(
            shape=node.shape, dtype=node.dtype,
            **(b2_args | kwds)
        )
        return b2_empty

    b2_chunkit = b2chunkit_from_dataset(node, b2_args)
    return b2_make_empty, b2_chunkit


def b2chunkit_from_blosc2(node: h5py.Dataset,
                          b2_args: Mapping) -> Iterable[bytes]:
    # Blosc2-compressed dataset, just pass chunks as they are.
    # Support both Blosc2 arrays and frames as HDF5 chunks.
    for h5_chunk_idx in range(node.id.get_num_chunks()):
        b2_array = b2_from_h5_chunk(node, h5_chunk_idx)
        b2_schunk = getattr(b2_array, 'schunk', b2_array)
        # TODO: check if schunk is compatible with creation arguments
        for b2_chunk_info in b2_schunk.iterchunks_info():
            yield b2_schunk.get_chunk(b2_chunk_info.nchunk)


def b2chunkit_from_nonchunked(node: h5py.Dataset,
                              b2_args: Mapping) -> Iterable[bytes]:
    # Contiguous or compact dataset,
    # slurp into Blosc2 array and get chunks from it.
    # Hopefully the data is small enough to be loaded into memory.
    src_array = blosc2.asarray(
        node[()],  # ok for arrays & scalars
        **b2_args
    )
    schunk = src_array.schunk
    yield from (schunk.get_chunk(ci.nchunk)
                for ci in schunk.iterchunks_info())


def b2chunkit_from_chunked(node: h5py.Dataset,
                           b2_args: Mapping) -> Iterable[bytes]:
    # Non-Blosc2 chunked dataset,
    # load each HDF5 chunk into chunk 0 of compatible Blosc2 array,
    # then get the resulting compressed chunk.
    # Thus, only one chunk worth of data is kept in memory.
    assert node.chunks == b2_args['chunks']
    src_array = blosc2.empty(
        shape=node.chunks, dtype=node.dtype,  # note that shape is chunkshape
        **b2_args
    )
    schunk = src_array.schunk
    for chunk_slice in node.iter_chunks():
        chunk_array = node[chunk_slice]
        # Always place at the beginning so that it fits in chunk 0.
        src_slice = tuple(slice(0, n, 1) for n in chunk_array.shape)
        src_array[src_slice] = chunk_array
        yield schunk.get_chunk(0)


def copy_dataset(name: str, node: h5py.Dataset,
                 c2_root: pathlib.Path) -> None:
    # TODO: handle array / frame / (compressed) file distinctly
    b2_path = c2_root / f'{name}.b2nd'
    try:
        b2mkempty, b2_chunks = b2mkempty_b2chunkit_from_dataset(node)
        b2_array = b2mkempty(urlpath=b2_path, mode='w')

        b2_schunk = b2_array.schunk
        for (nchunk, chunk) in enumerate(b2_chunks):
            b2_schunk.update_chunk(nchunk, chunk)

        b2_attrs = b2_schunk.vlmeta
        for (aname, avalue) in node.attrs.items():
            try:
                # This small workaround avoids Blosc2's strict type packing,
                # so we can handle value subclasses like `numpy.bytes_`
                # (e.g. for Fortran-style string attributes added by PyTables).
                pvalue = msgpack.packb(avalue, default=blosc2_ext.encode_tuple)
                b2_attrs.set_vlmeta(aname, pvalue, typesize=1)  # non-numeric data
                logging.info(f"Exported dataset attribute {aname!r}: {name!r}")
            except Exception as e:
                logging.error(f"Failed to export dataset attribute "
                              f"{aname!r}: {name!r} -> {e!r}")
    except Exception as e:
        b2_path.unlink(missing_ok=True)
        logging.error(f"Failed to export dataset "
                      f"to Blosc2 ND array: {name!r} -> {e!r}")
        return
    logging.info(f"Exported dataset: {name!r} => {str(b2_path)!r}")


def node_exporter(c2_root: pathlib.Path):
    """Return an HDF5 node item visitor to export to
    existing Caterva2 root at `c2_root`.
    """
    def export_node(name, node):
        if any(d in [os.path.curdir, os.path.pardir] for d in name.split('/')):
            logging.warning(f"Invalid node name, skipping: {name!r}")
            return
        if isinstance(node, h5py.Group):
            do_export_node = create_directory
        elif isinstance(node, h5py.Dataset):
            do_export_node = copy_dataset
        else:
            logging.warning(f"Unsupported node type "
                            f"{type(node).__name__}, skipping: {name!r}")
            return

        do_export_node(name, node, c2_root)
    return export_node


def export_group(h5_group: h5py.File, c2_root: pathlib.Path) -> None:
    """Export open HDF5 group `h5_group` to
    existing Caterva2 root at `c2_root`.
    """
    # TODO: soft & external links (not visited)
    h5_group.visititems(node_exporter(c2_root))


def export(hdf5_path: str, cat2_path: str) -> None:
    """Export HDF5 file in `hdf5_path` to new Caterva2 root at `cat2_path`."""
    with h5py.File(hdf5_path, 'r') as h5f:
        c2r = pathlib.Path(cat2_path).resolve()
        c2r.mkdir(parents=True)

        export_group(h5f, c2r)


def main():
    try:
        _, hdf5_path, cat2_path = sys.argv
    except ValueError:
        def eprint(*args):
            print(*args, file=sys.stderr)
        prog = os.path.basename(sys.argv[0])
        eprint(f"Usage: {prog} HDF5_FILE CATERVA2_ROOT")
        eprint("Export the hierarchy in the existing HDF5_FILE "
               "into the new CATERVA2_ROOT directory.")
        eprint('\n'.join(__doc__.splitlines()[1:]))
        sys.exit(1)
    else:
        export(hdf5_path, cat2_path)


if __name__ == '__main__':
    main()
