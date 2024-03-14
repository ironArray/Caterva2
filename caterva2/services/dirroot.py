###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import io
import os
import pathlib
from collections.abc import AsyncIterator, Callable, Collection, Iterator
try:
    from typing import Self
except ImportError:  # Python < 3.11
    from typing import TypeVar
    Self = TypeVar('Self', bound='PubRoot')

# Requirements
import blosc2
import pydantic
import watchfiles

# Project
from caterva2.services import pubroot, srv_utils


class DirectoryRoot:
    """Represents a publisher root which keeps datasets as files
    in a directory.
    """

    Path = pubroot.PubRoot.Path

    @classmethod
    def get_maker(cls, target: str) -> Callable[[], Self] | None:
        try:
            path = pathlib.Path(target)
            if not path.is_dir():
                return None
        except Exception:
            return None
        return lambda: cls(path)

    def __init__(self, path: pathlib.Path):
        abspath = path.resolve(strict=True)
        # Force an error for non-dirs or non-readable dirs.
        next(abspath.iterdir())

        self.abspath = abspath

    def walk_dsets(self) -> Iterator[Path]:
        return (self.Path(p.relative_to(self.abspath))
                for p in self.abspath.glob('**/*')
                if not p.is_dir())

    def _rel_to_abs(self, relpath: Path) -> pathlib.Path:
        if relpath.is_absolute():
            raise ValueError(f"path is not relative: {str(relpath)!r}")
        # ``.`` is removed on path instantiation, no need to check for it.
        if os.path.pardir in relpath.parts:
            raise ValueError(f"{str(os.path.pardir)!r} not allowed "
                             f"in path: {str(relpath)!r}")
        abspath = self.abspath / relpath
        if not abspath.is_file():
            raise pubroot.NoSuchDatasetError(relpath)
        return abspath

    def exists_dset(self, relpath: Path) -> bool:
        try:
            abspath = self._rel_to_abs(relpath)
        except pubroot.NoSuchDatasetError:
            return False
        return abspath.is_file()

    def get_dset_etag(self, relpath: Path) -> str:
        abspath = self._rel_to_abs(relpath)
        stat = abspath.stat()
        return f'{stat.st_mtime}:{stat.st_size}'

    def get_dset_meta(self, relpath: Path) -> pydantic.BaseModel:
        abspath = self._rel_to_abs(relpath)
        return srv_utils.read_metadata(abspath)

    def get_dset_chunk(self, relpath: Path, nchunk: int) -> bytes:
        abspath = self._rel_to_abs(relpath)
        b2dset = blosc2.open(abspath)
        schunk = getattr(b2dset, 'schunk', b2dset)
        if nchunk > schunk.nchunks:
            raise pubroot.NoSuchChunkError(nchunk)
        return schunk.get_chunk(nchunk)

    def open_dset_raw(self, relpath: Path) -> io.RawIOBase:
        abspath = self._rel_to_abs(relpath)
        return open(abspath, 'rb')

    async def awatch_dsets(self) -> AsyncIterator[Collection[Path]]:
        async for changes in watchfiles.awatch(self.abspath):
            relpaths = set(
                self.Path(pathlib.Path(abspath).relative_to(self.abspath))
                for change, abspath in changes)
            yield relpaths


pubroot.register_root_class(DirectoryRoot)


def create_example_root(path):
    """Create an example Caterva2 directory to be used as a root."""
    import pathlib

    import blosc2
    import numpy as np

    # The examples come from ``SPECS.md`` and ``root-example`` content.
    path = pathlib.Path(path)
    path.mkdir(parents=True)

    with open(path / "README.md", "w") as f:
        f.write("This is a simple example,\n"
                "with several lines,\n"
                "for showing purposes.\n")

    # A SChunk containing a data buffer.
    blosc2.SChunk(chunksize=100, data=b"Hello world!" * 100,
                  urlpath=path / "ds-hello.b2frame", mode="w")

    # A 1D array (int64).
    a = np.arange(1000, dtype="int64")
    blosc2.asarray(a, chunks=(100,), blocks=(10,),
                   urlpath=path / "ds-1d.b2nd", mode="w")

    # A 1D array (6-byte strings).
    a = np.array([b"foobar"] * 1000)
    blosc2.asarray(a, chunks=(100,), blocks=(10,),
                   urlpath=path / "ds-1d-b.b2nd", mode="w")

    # A scalar (string) with variable-length metalayers (user attributes).
    a = np.str_("foobar")
    b = blosc2.asarray(a, urlpath=path / "ds-sc-attr.b2nd", mode="w")
    for k, v in dict(a=1, b="foo", c=123.456).items():
        b.schunk.vlmeta[k] = v

    (path / "dir1").mkdir()

    # A 2D array (uint16).
    a = np.arange(200, dtype="uint16").reshape(10, 20)
    blosc2.asarray(a, chunks=(5, 5), blocks=(2, 3),
                   urlpath=path / "dir1/ds-2d.b2nd", mode="w")

    # A 3D array (float32).
    a = np.arange(60, dtype="float32").reshape(3, 4, 5)
    blosc2.asarray(a, chunks=(2, 3, 4), blocks=(2, 2, 2),
                   urlpath=path / "dir1/ds-3d.b2nd", mode="w")

    (path / "dir2").mkdir()

    # A 4D array (complex128).
    a = np.arange(120, dtype="complex128").reshape(2, 3, 4, 5)
    blosc2.asarray(a + a * 1j, chunks=(1, 2, 3, 4), blocks=(1, 2, 2, 2),
                   urlpath=path / "dir2/ds-4d.b2nd", mode="w")


def main():
    import os
    import sys
    try:
        _, c2dpath = sys.argv
    except ValueError:
        prog = os.path.basename(sys.argv[0])
        print(f"Usage: {prog} CATERVA2_DIR", file=sys.stderr)
        sys.exit(1)
    create_example_root(c2dpath)
    print(f"Created example Caterva2 root: {c2dpath!r}", file=sys.stderr)


if __name__ == '__main__':
    main()
