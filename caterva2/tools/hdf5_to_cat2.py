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


def export(hdf5_path, cat2_path):
    with h5py.File(hdf5_path, 'r') as h5f:
        c2r = pathlib.Path(cat2_path).resolve()
        c2r.mkdir(parents=True)


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
