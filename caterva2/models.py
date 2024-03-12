###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import typing

# Requirements
import blosc2
import pydantic


class CParams(pydantic.BaseModel, extra=pydantic.Extra.allow):
    codec: blosc2.Codec
    filters: list[blosc2.Filter]
    filters_meta: list[int]
    typesize: int
    blocksize: int


class SChunk(pydantic.BaseModel, extra=pydantic.Extra.allow):
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


class Metadata(pydantic.BaseModel):
    shape: tuple
    chunks: tuple
    blocks: tuple
    dtype: str
    schunk: SChunk


class File(pydantic.BaseModel):
    mtime: float
    size: int


class Root(pydantic.BaseModel):
    name: str
    http: str
    subscribed: typing.Optional[bool] = None  # Used only by the subscriber program


class Broker(pydantic.BaseModel):
    roots: typing.Dict[str, Root]


class Publisher(pydantic.BaseModel):
    etags: typing.Dict[str, str]


class Subscriber(pydantic.BaseModel):
    roots: typing.Dict[str, Root]
    etags: typing.Dict[str, str]
