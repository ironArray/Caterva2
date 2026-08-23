###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import datetime

# Requirements
import blosc2
import pydantic


class Corrupt(pydantic.BaseModel):
    mtime: datetime.datetime | None = None
    error: str


class MissingOperands(pydantic.BaseModel):
    mtime: datetime.datetime | None = None
    error: str
    expr: str
    missing_ops: dict


class CParams(pydantic.BaseModel, extra="allow"):
    codec: blosc2.Codec
    codec_meta: int
    clevel: int
    filters: list[blosc2.Filter]
    filters_meta: list[int]
    typesize: int
    blocksize: int
    nthreads: int
    splitmode: blosc2.SplitMode
    tuner: blosc2.Tuner
    use_dict: bool


class SChunk(pydantic.BaseModel, extra="allow"):
    cbytes: int
    chunkshape: int
    chunksize: int
    contiguous: bool
    cparams: CParams
    cratio: float
    #   dparams
    #   meta
    nbytes: int
    urlpath: str | None
    vlmeta: dict = {}
    nchunks: int
    mtime: datetime.datetime | None = None


class Metadata(pydantic.BaseModel):
    shape: tuple
    chunks: tuple
    blocks: tuple
    dtype: str
    schunk: SChunk
    mtime: datetime.datetime | None
    # Whether `api/fetch` will serve byte ranges of this dataset: "bytes" for one
    # this server has a file for, "none" for one it mounts from a peer and
    # re-serializes.  None where it is not settled here -- a container leaf,
    # whose answer depends on its own window -- and a client told None asks, as
    # every client did before this field existed.  It describes `api/fetch`, not
    # the reply carrying it, which is why it is a field and not an
    # `Accept-Ranges` header: that one would claim `api/info` served ranges.
    accept_ranges: str | None = None


class LazyArray(pydantic.BaseModel):
    # When an operand is missing some attributes will be None
    shape: tuple | None
    dtype: str | None
    expression: str
    operands: dict[str, str | None]
    mtime: datetime.datetime | None


class CTableMetadata(pydantic.BaseModel):
    kind: str = "ctable"
    nrows: int
    ncols: int
    chunks: tuple
    blocks: tuple
    schema_dict: dict
    columns: list[str]
    nbytes: int
    cbytes: int
    cratio: float
    vlmeta: dict = {}
    mtime: datetime.datetime | None


class Cat2LazyArr(pydantic.BaseModel):
    name: str | None
    expression: str | None
    func: str | None
    operands: dict[str, str]
    dtype: str | None
    shape: tuple | None
    compute: bool


class FetchPayload(pydantic.BaseModel, extra="forbid"):
    """What `POST api/fetch` carries, which is what its query string carries.

    A URL is a poor place for a long list of coordinates: a key of more than
    some thousands of them does not fit in one, and no encoding fixes that so
    much as move where it breaks.  A body has no such limit.

    The fields are the GET parameters by the same names, `indices` included as
    the same JSON string, so that one grammar and one parser serve both verbs
    and neither can drift from the other.

    A field this does not name is refused rather than dropped.  Every parameter
    here narrows the answer, so ignoring a misspelled one -- or one a newer
    client sends -- would not fail: it would quietly serve the whole dataset to
    a caller who asked for three coordinates.
    """

    indices: str | None = None
    slice_: str | None = None
    filter: str | None = None
    field: str | None = None


class MoveCopyPayload(pydantic.BaseModel):
    src: str
    dst: str


class AddUserPayload(pydantic.BaseModel):
    username: str
    password: str | None
    superuser: bool


class File(pydantic.BaseModel):
    mtime: datetime.datetime | None
    size: int


class Directory(pydantic.BaseModel):
    """A group-like container: a real directory, a TreeStore .b2z, or a
    virtual group inside one. ``size`` is None when it is not cheap to compute
    (virtual groups)."""

    kind: str = "group"
    mtime: datetime.datetime | None
    size: int | None = None
    nfiles: int


class Root(pydantic.BaseModel):
    name: str


class Server(pydantic.BaseModel):
    pass
