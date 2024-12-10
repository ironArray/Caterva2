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
    urlpath: str
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


class LazyArray(pydantic.BaseModel):
    shape: tuple
    dtype: str
    expression: str
    operands: dict[str, str]
    mtime: datetime.datetime | None


class NewLazyExpr(pydantic.BaseModel):
    name: str
    expression: str
    operands: dict[str, str]


class MoveCopyPayload(pydantic.BaseModel):
    src: str
    dst: str


class AddUserPayload(pydantic.BaseModel):
    username: str
    password: str | None
    superuser: bool


class File(pydantic.BaseModel):
    mtime: float
    size: int


class Root(pydantic.BaseModel):
    name: str
    http: str
    subscribed: bool | None = None  # Used only by the subscriber program


class Broker(pydantic.BaseModel):
    roots: dict[str, Root]


class Publisher(pydantic.BaseModel):
    etags: dict[str, str]


class Subscriber(pydantic.BaseModel):
    roots: dict[str, Root]
    etags: dict[str, str]
