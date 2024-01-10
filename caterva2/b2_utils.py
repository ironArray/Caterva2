###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import pathlib

# Requirements
import blosc2
import numpy as np


#
# Blosc2 related functions
#

def compress(data, dst=None):
    assert isinstance(data, (bytes, pathlib.Path))

    if dst is not None:
        dst.parent.mkdir(exist_ok=True, parents=True)
        if dst.exists():
            dst.unlink()

    # Create schunk
    cparams = {}
    dparams = {}
    storage = {
        'urlpath': dst,
        'cparams': cparams,
        'dparams': dparams,
    }
    schunk = blosc2.SChunk(**storage)

    # Append data
    if isinstance(data, pathlib.Path):
        with open(data, 'rb') as f:
            data = f.read()

    schunk.append_data(data)

    return schunk


def init_b2nd(metadata, urlpath=None):
    if urlpath is not None:
        urlpath.parent.mkdir(exist_ok=True, parents=True)
        if urlpath.exists():
            urlpath.unlink()

    dtype = getattr(np, metadata.dtype)
    return blosc2.uninit(metadata.shape, dtype, urlpath=urlpath,
                         chunks=metadata.chunks, blocks=metadata.blocks)


def init_b2frame(metadata, urlpath=None):
    if urlpath is not None:
        urlpath.parent.mkdir(exist_ok=True, parents=True)
        if urlpath.exists():
            urlpath.unlink()

    cparams = metadata.cparams.model_dump()
    sc = blosc2.SChunk(
        metadata.chunksize,
        contiguous=metadata.contiguous,
        cparams=cparams,
        dparams={},
        urlpath=urlpath,
    )
    sc.fill_special(metadata.nbytes / metadata.typesize,
                    special_value=blosc2.SpecialValue.UNINIT)
    return sc


def open_b2(abspath):
    suffix = abspath.suffix
    if suffix == '.b2nd':
        array = blosc2.open(abspath)
        schunk = array.schunk
    elif suffix == '.b2frame':
        array = None
        schunk = blosc2.open(abspath)
    elif suffix == '.b2':
        array = None
        schunk = blosc2.open(abspath)
    else:
        raise NotImplementedError()

    return array, schunk


def chunk_is_available(schunk, nchunk):
    # Blosc2 flags are at offset 31
    # (see https://github.com/Blosc/c-blosc2/blob/main/README_CHUNK_FORMAT.rst)
    flag = (schunk.get_lazychunk(nchunk)[31] & 0b01110000) >> 4
    return flag != blosc2.SpecialValue.UNINIT.value


def iterchunk(chunk):
    # TODO Yield block by block
    yield chunk
