###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Export a Caterva2 root to datasets in an HDF5 file.

The content of the existing Caterva2 root must be readable, while the HDF5
file is created anew (it must not exist).  For each directory under the
Caterva2 root, a group with the same path name is created in the HDF5 file.
For each file or Blosc2 dataset, an HDF5 dataset with the same path name
(minus any ``.b2*`` extension) is created in the HDF5 file.

Blosc2 arrays (``*.b2nd``), frames (``*.b2frame``) and compressed files
(``*.b2``) have their chunk sizes respected during conversion, while plain
files use a default chunk size.  Arrays result in typed multidimensional
datasets, while the rest result in flat datasets of bytes.  Currently, all
exported datasets use some default Blosc2 compression parameters.

Warning: For the moment, the data in each file and dataset is read and
decompressed into memory in its entirety.

Datasets or attributes which are unsupported or fail to be converted are
simply reported and skipped, and they do not cause the program to fail.
"""

import functools
import logging
import operator
import os
import pathlib
import sys

import blosc2
import h5py
import hdf5plugin
import numpy

from collections.abc import Callable, Iterator, Mapping

from .common import BLOSC2_HDF5_FID


# Set to empty mapping to store files as uncompressed HDF5 datasets.
file_h5_compargs = hdf5plugin.Blosc2(cname='zstd', clevel=5, filters=1)


def export_leaf(c2_leaf: os.DirEntry, h5_group: h5py.Group) -> None:
    """Export Caterva2 leaf entry `c2_leaf` into
    open HDF5 group `h5_group`.
    """
    try:
        (h5mkdataset, attrs) = h5mkdataset_h5attrs_from_leaf(c2_leaf)
    except Exception as e:
        logging.error(f"Failed to translate Blosc2 dataset: "
                      f"{c2_leaf.path!r} -> {e!r}")
        return

    try:
        h5_dataset = h5mkdataset(h5_group)
    except Exception as e:
        logging.error(f"Failed to create dataset in HDF5 group "
                      f"{h5_group.name!r}: {c2_leaf.name!r} -> {e!r}")
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


def h5compargs_from_b2(b2_array: blosc2.NDArray | blosc2.SChunk) -> Mapping:
    ndim = getattr(b2_array, 'ndim', -1)
    if ndim == 0:  # scalar, no filters/compression allowed
        return {}

    b2_schunk = getattr(b2_array, 'schunk', b2_array)
    cparams = b2_schunk.cparams
    # This is what hdf5plugin does (more or less).
    # Option list as per ``hdf5-blosc2/src/blosc2_filter.c``.
    #
    # Note: These HDF5 filter options (``cd_values``)
    # are just an approximation to the actual cparams/dparams
    # in the Blosc2 super-chunks stored as HDF5 chunks,
    # as they are not yet able to encode all Blosc2 features
    # (like the ordered filter pipeline and meta as of 2024-02).
    # The cparams/dparams in each schunk are more reliable.
    opts = (
        1,  # filter revision
        cparams['blocksize'],  # block size (in bytes)
        cparams['typesize'],  # type size (in bytes)
        b2_schunk.chunksize,  # chunk size (in bytes)
        cparams['clevel'],  # compression level
        # Just a coarse hint that filters are used (see note above),
        # e.g. their order and meta are lost here.
        functools.reduce(operator.or_,  # shuffle method
                         (s.value for s in cparams['filters'])),
        cparams['codec'].value,  # compressor code
    )
    if ndim > 1:
        opts += (
            ndim,  # chunk rank (number of dimensions)
            *b2_array.chunks,  # length of chunk dimension i
        )
    return dict(compression=BLOSC2_HDF5_FID, compression_opts=opts)


def h5mkdataset_h5attrs_from_leaf(c2_leaf: os.DirEntry) -> (
        Callable[[h5py.Group, ...], h5py.Dataset],
        Mapping):
    # TODO: mark array / frame / file distinguishably
    h5_args = {}
    h5_attrs = {}

    # TODO: handle symlinks safely
    c2_leaf_name = pathlib.Path(c2_leaf.name)
    if c2_leaf_name.suffix == '.b2nd':
        # TODO: do not slurp & re-compress
        b2_array = blosc2.open(c2_leaf.path)
        h5_args |= dict(
            name=pathlib.Path(c2_leaf).stem,
            shape=b2_array.shape,
            dtype=b2_array.dtype,
            data=b2_array[()],  # ok for arrays & scalars
            chunks=(b2_array.chunks if b2_array.ndim > 0 else None),
            **h5compargs_from_b2(b2_array),
        )
        h5_attrs |= b2_array.schunk.vlmeta.getall()  # copy avoids SIGSEGV
    elif c2_leaf_name.suffix in ['.b2frame', '.b2']:
        # TODO: do not slurp & re-compress
        b2_schunk = blosc2.open(c2_leaf.path)
        h5_args |= dict(
            name=pathlib.Path(c2_leaf).stem,
            # TODO: check for other types/typesizes
            shape=(b2_schunk.nbytes,),
            dtype=numpy.uint8,
            data=numpy.frombuffer(b2_schunk[:], dtype=numpy.uint8),
            chunks=(b2_schunk.chunkshape,),
            **h5compargs_from_b2(b2_schunk),
        )
        h5_attrs |= b2_schunk.vlmeta.getall()  # copy avoids SIGSEGV
    else:
        # TODO: do not slurp & re-compress
        with open(c2_leaf, 'rb') as f:
            data = f.read()
        h5_args |= dict(
            name=pathlib.Path(c2_leaf).name,
            shape=(c2_leaf.stat(follow_symlinks=True).st_size,),
            dtype=numpy.uint8,
            data=numpy.frombuffer(data, dtype=numpy.uint8),
            chunks=True,
            **file_h5_compargs,
        )

    def h5_make_dataset(h5_group: h5py.Group, **kwds) -> h5py.Dataset:
        dtype = h5_args.pop('dtype')
        return h5_group.create_dataset(
            dtype=dtype,  # not allowed as a real keyword argument
            **(h5_args | kwds)
        )

    return h5_make_dataset, h5_attrs


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
        def eprint(*args):
            print(*args, file=sys.stderr)
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
