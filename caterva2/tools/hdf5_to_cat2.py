###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Export datasets in an HDF5 file to a Caterva2 root."""

import os
import pathlib
import sys

import h5py


def node_exporter(c2_root):
    """Return an HDF5 node item visitor to export to existing Caterva2 root at `c2_root`."""
    def export_node(name, node):
        print(f"Export {type(node).__name__} {name!r} -> {str(c2_root)!r}")
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
