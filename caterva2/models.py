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


class CParams(pydantic.BaseModel):
    codec: blosc2.Codec
    typesize: int
    blocksize: int


class SChunk(pydantic.BaseModel):
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
#   vlmeta
    nchunks: int


class Metadata(pydantic.BaseModel):
    shape: list[int]
    chunks: list[int]
    blocks: list[int]
    dtype: str
    schunk: SChunk
    size: int


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
