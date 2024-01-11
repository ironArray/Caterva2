###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Export datasets in an HDF5 file to a Caterva2 root."""

import logging
import os
import pathlib
import sys

import blosc2
import h5py
import hdf5plugin


def create_directory(name, node, c2_root):
    path = c2_root / name
    try:
        path.mkdir()  # parent should exist, not itself
    except OSError as ose:
        logging.error(f"Failed to create directory for node: {name!r} -> %r", ose)
        return
    logging.info(f"Exported group: {name!r} => {str(path)!r}")


def copy_dataset(name, node, c2_root):
    try:
        b2_array = blosc2.asarray(node[:])
    except ValueError as ve:
        logging.error(f"Failed to convert node to Blosc2 ND array: {name!r} -> %r", ve)
        return
    b2_path = c2_root / f'{name}.b2nd'
    try:
        with open(b2_path, 'wb') as f:
            f.write(b2_array.to_cframe())
    except Exception as e:
        b2_path.unlink(missing_ok=True)
        logging.error(f"Failed to save node as Blosc2 ND array: {name!r} -> %r", e)
        return
    logging.info(f"Exported dataset: {name!r} => {str(b2_path)!r}")


def node_exporter(c2_root):
    """Return an HDF5 node item visitor to export to existing Caterva2 root at `c2_root`."""
    def export_node(name, node):
        if any(d in [os.path.curdir, os.path.pardir] for d in name.split('/')):
            logging.warning(f"Invalid node name, skipping: {name!r}")
            return
        if isinstance(node, h5py.Group):
            do_export_node = create_directory
        elif isinstance(node, h5py.Dataset):
            do_export_node = copy_dataset
        else:
            logging.warning(f"Unsupported node type {type(node).__name__}, skipping: {name!r}")
            return

        if len(node.attrs.keys()) > 0:
            logging.warning(f"Exporting node attributes is not supported yet: {name!r}")

        do_export_node(name, node, c2_root)
    return export_node


def export_group(h5_group, c2_root):
    """Export open HDF5 group `h5_group` to existing Caterva2 root at `c2_root`."""
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
        prog = os.path.basename(sys.argv[0])
        print(f"Usage: {prog} HDF5_FILE CATERVA2_ROOT", file=sys.stderr)
        sys.exit(1)

    export(hdf5_path, cat2_path)


if __name__ == '__main__':
    main()
