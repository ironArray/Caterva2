###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import json
import pathlib
import typing

# Requirements
import blosc2
import fastapi
import fastapi_websocket_pubsub
import numpy as np
import safer

# Project
from caterva2 import models


def cache_lookup(cachedir, path):
    path = pathlib.Path(path)
    if path.suffix not in {'.b2frame', '.b2nd'}:
        path = f'{path}.b2'

    return get_abspath(cachedir, path)


def get_model_from_obj(obj, model_class, **kwargs):
    if isinstance(obj, dict):
        def getter(o, k):
            return o[k]
    else:
        getter = getattr

    data = kwargs.copy()
    for key, info in model_class.model_fields.items():
        if key not in data:
            value = getter(obj, key)
            if info.annotation is str:
                value = str(value)

            data[key] = value

    return model_class(**data)


def read_metadata(obj, cache=None, scratch=None):
    # Open dataset
    if isinstance(obj, pathlib.Path):
        path = obj
        if not path.is_file():
            raise FileNotFoundError('File does not exist or is a directory')

        suffix = path.suffix
        if suffix in {'.b2frame', '.b2nd', '.b2'}:
            obj = blosc2.open(path)
        else:
            # Special case for regular files
            stat = path.stat()
            keys = ['mtime', 'size']
            data = {key: getattr(stat, f'st_{key}') for key in keys}
            return get_model_from_obj(data, models.File)

    # Read metadata
    if isinstance(obj, blosc2.ndarray.NDArray):
        array = obj
        cparams = get_model_from_obj(array.schunk.cparams, models.CParams)
        cparams = reformat_cparams(cparams)
        schunk = get_model_from_obj(array.schunk, models.SChunk, cparams=cparams)
        return get_model_from_obj(array, models.Metadata, schunk=schunk)
    elif isinstance(obj, blosc2.schunk.SChunk):
        schunk = obj
        cparams = get_model_from_obj(schunk.cparams, models.CParams)
        cparams = reformat_cparams(cparams)
        model = get_model_from_obj(schunk, models.SChunk, cparams=cparams)
        return model
    elif isinstance(obj, blosc2.LazyExpr):
        operands = operands_as_paths(obj.operands, cache, scratch)
        return get_model_from_obj(obj, models.LazyArray, operands=operands)
    else:
        raise TypeError(f'unexpected {type(obj)}')


def reformat_cparams(cparams):
    cparams.__setattr__('filters, meta', [(cparams.filters[i], cparams.filters_meta[i])
                        for i in range(len(cparams.filters))
                        if cparams.filters[i] != blosc2.Filter.NOFILTER])
#   delattr(cparams, 'filters')
#   delattr(cparams, 'filters_meta')
    return cparams


def get_relpath(ndarr, cache, scratch):
    path = pathlib.Path(ndarr.schunk.urlpath)
    try:
        # Cache: /.../<root>/<subpath> to <root>/<subpath>
        path = path.relative_to(cache)
    except ValueError:
        # Scratch: /.../<uid>/<subpath> to @scratch/<subpath>
        path = path.relative_to(scratch)
        parts = list(path.parts)
        parts[0] = '@scratch'
        path = pathlib.Path(*parts)

    return path

def operands_as_paths(operands, cache, scratch):
    return dict(
        (nm, str(get_relpath(op, cache, scratch)))
        for (nm, op) in operands.items()
    )


#
# Pub/Sub helpers
#

def start_client(url):
    client = fastapi_websocket_pubsub.PubSubClient()
    client.start_client(url)
    return client


async def disconnect_client(client, timeout=5):
    if client is not None:
        # If the broker is down client.disconnect hangs, wo we wrap it in a timeout
        await asyncio.wait_for(client.disconnect(), timeout)


#
# HTTP server helpers
#

HeaderType = typing.Annotated[str | None, fastapi.Header()]


def raise_bad_request(detail):
    raise fastapi.HTTPException(status_code=400, detail=detail)


def raise_not_found(detail='Not Found'):
    raise fastapi.HTTPException(status_code=404, detail=detail)


def get_abspath(root, path):
    abspath = root / path

    # Security check
    if root not in abspath.parents:
        raise_bad_request(f'Invalid path {path}')

    # Existence check
    if not abspath.is_file():
        raise_not_found()

    return abspath


def check_dset_path(proot, path):
    try:
        exists = proot.exists_dset(path)
    except ValueError:
        raise_bad_request(f'Invalid path {path}')
    else:
        if not exists:
            raise_not_found()

#
# Blosc2 related helpers
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


def _init_b2(make_b2, metadata, urlpath=None):
    if urlpath is not None:
        urlpath.parent.mkdir(exist_ok=True, parents=True)
        if urlpath.exists():
            urlpath.unlink()

    schunk_meta = getattr(metadata, 'schunk', metadata)
    # Let default behaviour decide whether to use contiguous storage or not,
    # depending on where the dataset is going to be stored.
    # The original value is irrelevant.
    b2_args = dict(urlpath=urlpath, dparams={},
                   cparams=schunk_meta.cparams.model_dump())
    b2 = make_b2(**b2_args)

    b2_vlmeta = getattr(b2, 'schunk', b2).vlmeta
    for k, v in schunk_meta.vlmeta.items():
        b2_vlmeta[k] = v
    return b2


def init_b2nd(metadata, urlpath=None):
    def make_b2nd(**kwargs):
        dtype = metadata.dtype
        if dtype.startswith('['):
            # TODO: eval is dangerous, but we mostly trust the metadata
            # This is a list, so we need to convert it to a string
            dtype = eval(dtype)
        return blosc2.uninit(metadata.shape, np.dtype(dtype),
                             chunks=metadata.chunks, blocks=metadata.blocks,
                             **kwargs)
    return _init_b2(make_b2nd, metadata, urlpath)


def init_b2frame(metadata, urlpath=None):
    def make_b2frame(**kwargs):
        sc = blosc2.SChunk(metadata.chunksize, **kwargs)
        sc.fill_special(metadata.nbytes / sc.typesize,
                        special_value=blosc2.SpecialValue.UNINIT)
        return sc
    return _init_b2(make_b2frame, metadata, urlpath)


def init_b2(abspath, metadata):
    suffix = abspath.suffix
    if suffix == '.b2nd':
        metadata = models.Metadata(**metadata)
        init_b2nd(metadata, abspath)
    elif suffix == '.b2frame':
        metadata = models.SChunk(**metadata)
        init_b2frame(metadata, abspath)
    else:
        abspath = pathlib.Path(f'{abspath}.b2')
        metadata = models.SChunk(**metadata)
        init_b2frame(metadata, abspath)


def open_b2(abspath):
    suffix = abspath.suffix
    if suffix == '.b2nd':
        array = blosc2.open(abspath)
        schunk = getattr(array, 'schunk', None)  # may be lazy
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


#
# Facility to persist program state
#

class Database:

    def __init__(self, path, initial):
        self.path = path
        self.model = initial.__class__
        if path.exists():
            self.load()
        else:
            path.parent.mkdir(exist_ok=True, parents=True)
            self.data = initial
            self.save()

    def load(self):
        with self.path.open() as file:
            dump = json.load(file)
            self.data = self.model.model_validate(dump)

    def save(self):
        dump = self.data.model_dump_json(exclude_none=True)
        with safer.open(self.path, 'w') as file:
            file.write(dump)

    def __getattr__(self, name):
        return getattr(self.data, name)
