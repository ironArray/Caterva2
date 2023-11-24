###############################################################################
# Caterva - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import blosc2
import pydantic


class CParams(pydantic.BaseModel):
    codec: blosc2.Codec
    typesize: int

class SChunk(pydantic.BaseModel):
    blocksize: int
    cbytes: int
    chunkshape: int
    chunksize: int
    contiguous: bool
    cparams: CParams
    cratio: float
#   dparams
#   meta
    nbytes: int
    typesize: int
    urlpath: str
#   vlmeta
    nchunks: int

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

class File(pydantic.BaseModel):
    mtime: float
    size: int

class Publisher(pydantic.BaseModel):
    name: str
    http: str
