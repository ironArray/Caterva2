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

from blosc2 import blosc2_ext


def create_directory(name, node, c2_root):
    if len(node.attrs.keys()) > 0:
        logging.warning(f"Exporting group attributes "
                        f"is not supported yet: {name!r}")

    path = c2_root / name
    try:
        path.mkdir()  # parent should exist, not itself
    except OSError as ose:
        logging.error(f"Failed to create directory "
                      f"for node: {name!r} -> %r", ose)
        return
    logging.info(f"Exported group: {name!r} => {str(path)!r}")


def copy_dataset(name, node, c2_root):
    try:
        b2_array = blosc2.asarray(node[:])
    except ValueError as ve:
        logging.error(f"Failed to convert dataset "
                      f"to Blosc2 ND array: {name!r} -> %r", ve)
        return

    b2_attrs = b2_array.schunk.vlmeta
    for (aname, avalue) in node.attrs.items():
        try:
            # This small workaround avoids Blosc2's strict type packing,
            # so we can handle value subclasses like `numpy.bytes_`
            # (e.g. for Fortran-style string attributes added by PyTables).
            pvalue = msgpack.packb(avalue, default=blosc2_ext.encode_tuple)
            b2_attrs.set_vlmeta(aname, pvalue, typesize=1)  # non-numeric data
        except Exception as e:
            logging.error(f"Failed to export dataset attribute "
                          f"{aname!r}: {name!r} -> %r", e)

    b2_path = c2_root / f'{name}.b2nd'
    try:
        with open(b2_path, 'wb') as f:
            f.write(b2_array.to_cframe())
    except Exception as e:
        b2_path.unlink(missing_ok=True)
        logging.error(f"Failed to save dataset "
                      f"as Blosc2 ND array: {name!r} -> %r", e)
        return
    logging.info(f"Exported dataset: {name!r} => {str(b2_path)!r}")


def node_exporter(c2_root):
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


def export_group(h5_group, c2_root):
    """Export open HDF5 group `h5_group` to
    existing Caterva2 root at `c2_root`.
    """
    h5_group.visititems(node_exporter(c2_root))


def export(hdf5_path, cat2_path):
    """Export HDF5 file in `hdf5_path` to new Caterva2 root at `cat2_path`."""
    with h5py.File(hdf5_path, 'r') as h5f:
        c2r = pathlib.Path(cat2_path).resolve()
        c2r.mkdir(parents=True)

        export_group(h5f, c2r)


def main():
    try:
        _, hdf5_path, cat2_path = sys.argv
    except ValueError:
        eprint = lambda *a: print(*a, file=sys.stderr)
        prog = os.path.basename(sys.argv[0])
        eprint(f"Usage: {prog} HDF5_FILE CATERVA2_ROOT")
        eprint("Export the hierarchy in the existing HDF5_FILE "
               "into the new CATERVA2_ROOT directory.")
        eprint('\n'.join(__doc__.splitlines()[1:]))
        sys.exit(1)

    export(hdf5_path, cat2_path)


if __name__ == '__main__':
    main()
