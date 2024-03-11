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
        abspath = self._rel_to_abs(relpath)
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
