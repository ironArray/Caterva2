###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Export a Caterva2 root to datasets in an HDF5 file."""

import logging
import os
import pathlib
import re
import sys

import blosc2
import h5py
import numpy

from collections.abc import Callable, Iterator, Mapping


def export_dataset(c2_leaf: os.DirEntry, h5_group: h5py.Group,
                   read: Callable[[str], tuple[Mapping, Mapping]]) -> None:
    try:
        (kwds, attrs) = read(c2_leaf.path)
    except Exception as e:
        logging.error(f"Failed to read Blosc2 dataset: "
                      f"{c2_leaf.path!r} -> {e!r}")
        return

    assert re.match(r'.*\.b2[^.]*$', c2_leaf.name)
    h5_name = pathlib.Path(c2_leaf.name).stem
    try:
        h5_dataset = h5_group.create_dataset(h5_name, **kwds)
    except Exception as e:
        logging.error(f"Failed to create dataset {h5_name!r} in HDF5 group: "
                      f"{h5_group.name!r} -> {e!r}")
        return

    for (aname, avalue) in attrs.items():
        try:
            h5_dataset.attrs[aname] = avalue
            logging.info(f"Exported dataset attribute "
                         f"{aname!r}: {h5_dataset.name!r}")
        except Exception as e:
            logging.error(f"Failed to export dataset attribute "
                          f"{aname!r}: {h5_dataset.name!r} -> {e!r}")

    logging.info(f"Exported dataset: {c2_leaf.path!r} => {h5_dataset.name!r}")


def read_array(path: str) -> tuple[Mapping, Mapping]:
    b2_array = blosc2.open(path)
    kwds = dict(
        data=b2_array[()],  # ok for arrays & scalars
        chunks=(b2_array.chunks if b2_array.ndim > 0 else None),
        # TODO: carry compression parameters (including blocks)
    )
    # TODO: mark array distinguishably
    return (kwds, b2_array.schunk.vlmeta.getall())  # copy avoids SIGSEGV


def read_frame(path: str) -> tuple[Mapping, Mapping]:
    b2_schunk = blosc2.open(path)
    kwds = dict(
        data=numpy.frombuffer(b2_schunk[:], dtype=numpy.uint8),
        chunks=(b2_schunk.chunkshape,),
        # TODO: carry compression parameters (including blocks)
    )
    # TODO: mark frame / compressed file distinguishably
    return (kwds, b2_schunk.vlmeta.getall())  # copy avoids SIGSEGV


def export_leaf(c2_leaf: os.DirEntry, h5_group: h5py.Group) -> None:
    """Export Caterva2 leaf entry `c2_leaf` into
    open HDF5 group `h5_group`.
    """
    # TODO: handle symlinks safely
    c2_leaf_name = pathlib.Path(c2_leaf.name)
    if c2_leaf_name.suffix == '.b2nd':
        export_dataset(c2_leaf, h5_group, read_array)
    elif c2_leaf_name.suffix in ['.b2frame', '.b2']:
        export_dataset(c2_leaf, h5_group, read_frame)
    else:  # TODO
        logging.warning(f"Exporting plain files "
                        f"is not supported yet: {c2_leaf.path!r}")


def export_root(c2_iter: Iterator[os.DirEntry], h5_group: h5py.Group) -> None:
    """Export existing Caterva2 root/directory iterator `c2_iter` into
    open HDF5 group `h5_group`.
    """
    for entry in c2_iter:
        if entry.is_dir(follow_symlinks=True):
            with os.scandir(entry) as c2i:
                h5g = h5_group.create_group(entry.name)
                export_root(c2i, h5g)
        else:
            export_leaf(entry, h5_group)


def export(cat2_path, hdf5_path):
    """Export Caterva2 root at `cat2_path` to new HDF5 file in `hdf5_path`."""
    with os.scandir(cat2_path) as c2i:  # keeps directory open
        with h5py.File(hdf5_path, 'x') as h5f:  # create, fail if existing
            export_root(c2i, h5f)


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
