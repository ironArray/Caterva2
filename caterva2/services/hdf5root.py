###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

# Requirements
import h5py

_MAX_CACHED_CHUNKERS = 32
"""Maximum number of dataset chunkers to keep in per-instance LRU cache."""


def create_example_root(path):
    """Create an example HDF5 file to be used as a root."""
    import numpy
    from hdf5plugin import Blosc2 as B2Comp

    with h5py.File(path, "x") as h5f:
        h5f.create_dataset("/scalar", data=123.456)
        h5f.create_dataset("/string", data=numpy.bytes_("Hello world!"))

        a = numpy.arange(100, dtype="uint8")
        h5f.create_dataset("/arrays/1d-raw", data=a)

        h5f["/arrays/soft-link"] = h5py.SoftLink("/arrays/1d-raw")

        a = numpy.array([b"foobar"] * 100)
        h5f.create_dataset("/arrays/1ds-blosc2", data=a, chunks=(50,), **B2Comp())

        a = numpy.arange(100, dtype="complex128").reshape(10, 10)
        a = a + a * 1j
        h5f.create_dataset("/arrays/2d-nochunks", data=a, chunks=None)

        a = numpy.arange(100, dtype="complex128").reshape(10, 10)
        a = a + a * 1j
        h5f.create_dataset("/arrays/2d-gzip", data=a, chunks=(4, 4), compression="gzip")

        a = numpy.arange(1000, dtype="uint8").reshape(10, 10, 10)
        h5f.create_dataset(
            "/arrays/3d-blosc2",
            data=a,
            chunks=(4, 10, 10),
            **B2Comp(cname="lz4", clevel=7, filters=B2Comp.BITSHUFFLE),
        )

        a = numpy.linspace(-1, 2, 1000).reshape(10, 10, 10)
        h5f.create_dataset(
            "/arrays/3d-blosc2-a",
            data=a,
            chunks=(4, 10, 10),
            **B2Comp(cname="lz4", clevel=7, filters=B2Comp.BITSHUFFLE),
        )

        a = numpy.linspace(-1, 2, 1000).reshape(10, 10, 10)
        h5f.create_dataset(
            "/arrays/3d-blosc2-b",
            data=a,
            chunks=(2, 5, 10),
            **B2Comp(cname="blosclz", clevel=7, filters=B2Comp.SHUFFLE),
        )

        h5f.create_dataset("/arrays/array-dtype", dtype=numpy.dtype(("float64", (4,))), shape=(10,))

        ds = h5f.create_dataset("/attrs", data=0)
        a = numpy.arange(4, dtype="uint8").reshape(2, 2)
        for k, v in {
            "Int": 42,
            "IntT": numpy.int16(42),
            "Bin": b"foo",
            "BinT": numpy.bytes_(b"foo"),
            "Str": "bar",  # StrT=numpy.str_("bar"),
            "Arr": a.tolist(),
            "ArrT": a,
            "NilBin": h5py.Empty("|S4"),
            # NilStr=h5py.Empty('|U4'),
            "NilInt": h5py.Empty("uint8"),
        }.items():
            ds.attrs[k] = v

        h5f.create_dataset("/arrays/empty", data=h5py.Empty("float64"))

        h5f.create_dataset("/arrays/compound-dtype", dtype=numpy.dtype("uint8,float64"), shape=(10,))

        a = numpy.arange(1, dtype="uint8").reshape((1,) * 23)
        h5f.create_dataset("/unsupported/too-many-dimensions", data=a)

        # TODO: This could be supported by mapping the vlstring dataset to an NDArray with shape=() and dtype="u1"
        h5f.create_dataset("/unsupported/vlstring", data="Hello world!")


def main():
    import os
    import sys

    try:
        _, h5fpath = sys.argv
    except ValueError:
        prog = os.path.basename(sys.argv[0])
        print(f"Usage: {prog} HDF5_FILE", file=sys.stderr)
        sys.exit(1)
    create_example_root(h5fpath)
    print(f"Created example HDF5 root: {h5fpath!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
