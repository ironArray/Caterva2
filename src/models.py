###############################################################################
# PubSub for Blosc2 - Access Blosc2 (and others) datasets via a Pub/Sub pattern
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import pydantic


class SChunk(pydantic.BaseModel):
    blocksize: int
    cbytes: int
    chunkshape: int
    chunksize: int
    contiguous: bool
#   cparams
    cratio: float
#   dparams
#   meta
    nbytes: int
    typesize: int
    urlpath: str
#   vlmeta


class Metadata(pydantic.BaseModel):
    dtype: str
    ndim: int
    shape: list[int]
    ext_shape: list[int]
    chunks: list[int]
    ext_chunks: list[int]
    blocks: list[int]
    blocksize: int
    chunksize: int
    schunk: SChunk
    size: int


class Publisher(pydantic.BaseModel):
    name: str
    http: str
