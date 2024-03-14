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
they can be serialized with msgpack (with NumPy and empty values first
translated into native Python).  Group attributes are not supported yet.
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

from collections.abc import Callable, Iterator, Mapping

from .. import hdf5


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


def b2mkempty_b2chunkit_from_dataset(node: h5py.Dataset) -> (
        Callable[..., blosc2.NDArray],
        Iterator[bytes]):
    """Get empty Blosc2 array maker and compressed chunk iterator from `node`.

    The first returned value can be called to create an empty Blosc2 array
    with prepared construction arguments and attributes extracted from the
    HDF5 dataset `node`.  By default it is created without storage, but that
    may be changed by passing additional keyword arguments like ``urlpath``.

    The second returned value is an iterator that yields compressed Blosc2
    chunks compatible with the construction arguments used by the previous
    callable, for the data in `node`.  They may be stored straight away in
    order in a Blosc2 super-chunk without further processing.
    """
    b2_args = hdf5.b2args_from_h5dset(node)
    b2_attrs = hdf5.b2attrs_from_h5dset(
        node,
        attr_ok=lambda n, a: logging.info(
            f"Translated dataset attribute {a!r}: {n.name!r}"),
        attr_err=lambda n, a, e: logging.error(
            f"Failed to translate dataset attribute "
            f"{a!r}: {n.name!r} -> {e!r}"),
    )
    _, b2iterchunks = hdf5.b2chunkers_from_h5dset(node, b2_args)

    def b2_make_empty(**kwds) -> blosc2.NDArray:
        return hdf5.b2empty_from_h5dset(node, b2_args, b2_attrs, **kwds)

    return b2_make_empty, b2iterchunks()


def copy_dataset(name: str, node: h5py.Dataset,
                 c2_root: pathlib.Path) -> None:
    # TODO: handle array / frame / (compressed) file distinctly

    try:
        b2mkempty, b2_chunks = b2mkempty_b2chunkit_from_dataset(node)
    except Exception as e:
        logging.error(f"Failed to translate dataset "
                      f"to Blosc2 ND array: {name!r} -> {e!r}")
        return

    b2_path = c2_root / f'{name}.b2nd'
    try:
        b2_array = b2mkempty(urlpath=b2_path, mode='w')
        b2_schunk = b2_array.schunk
        for (nchunk, chunk) in enumerate(b2_chunks):
            b2_schunk.update_chunk(nchunk, chunk)
    except Exception as e:
        b2_path.unlink(missing_ok=True)
        logging.error(f"Failed to save dataset "
                      f"as Blosc2 ND array: {name!r} -> {e!r}")
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
