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


class LazyArray(pydantic.BaseModel):
    # When an operand is missing some attributes will be None
    shape: tuple | None
    dtype: str | None
    expression: str
    operands: dict[str, str | None]
    mtime: datetime.datetime | None


class Cat2LazyArr(pydantic.BaseModel):
    name: str | None
    expression: str | None
    func: str | None
    operands: dict[str, str]
    dtype: str | None
    shape: tuple | None
    compute: bool


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


class Root(pydantic.BaseModel):
    name: str


class Server(pydantic.BaseModel):
    pass
