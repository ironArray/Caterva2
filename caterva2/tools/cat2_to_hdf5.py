###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Export a Caterva2 root to datasets in an HDF5 file."""

import os
import sys


def main():
    try:
        _, cat2_path, hdf5_path = sys.argv
    except ValueError:
        eprint = lambda *a: print(*a, file=sys.stderr)
        prog = os.path.basename(sys.argv[0])
        eprint(f"Usage: {prog} CATERVA2_ROOT HDF5_FILE")
        eprint("Export the existing CATERVA2_ROOT directory "
               "as a hierarchy in the new HDF5_FILE.")
        eprint('\n'.join(__doc__.splitlines()[1:]))
        sys.exit(1)
    else:
        export(cat2_path, hdf5_path)


if __name__ == '__main__':
    main()
