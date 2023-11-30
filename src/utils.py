###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import argparse
import asyncio
import contextlib
import logging
import pathlib

# Requirements
import blosc2
import fastapi
import fastapi_websocket_pubsub
import httpx
import numpy as np

# Project
import models


#
# Blosc2 related functions
#

def init_b2nd(urlpath, metadata):
    urlpath.parent.mkdir(exist_ok=True, parents=True)
    dtype = getattr(np, metadata.dtype)
    blosc2.uninit(metadata.shape, dtype, urlpath=str(urlpath))

def init_b2frame(urlpath, metadata):
    urlpath.parent.mkdir(exist_ok=True, parents=True)
    cparams = metadata.cparams.model_dump()
    blosc2.SChunk(
        metadata.chunksize,
        contiguous=metadata.contiguous,
        cparams=cparams,
        dparams={},
        urlpath=str(urlpath),
    )

def get_model_from_obj(obj, model_class, **kwargs):
    if type(obj) is dict:
        getter = lambda o, k: o[k]
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

def read_metadata(path):
    if type(path) is str:
        path = pathlib.Path(path)

    suffix = path.suffix
    if suffix == '.b2nd':
        array = blosc2.open(str(path))
        #print(f'{array.schunk.dparams=}')
        #print(f'{array.schunk.meta=}')
        #print(f'{array.schunk.vlmeta=}')
        #print(dict(array.schunk.vlmeta))
        #print()

        cparams = get_model_from_obj(array.schunk.cparams, models.CParams)
        schunk = get_model_from_obj(array.schunk, models.SChunk, cparams=cparams)
        return get_model_from_obj(array, models.Metadata, schunk=schunk)
    elif suffix == '.b2frame':
        schunk = blosc2.open(str(path))
        cparams = get_model_from_obj(schunk.cparams, models.CParams)
        return get_model_from_obj(schunk, models.SChunk, cparams=cparams)
    else:
        stat = path.stat()
        keys = ['mtime', 'size']
        data = {key: getattr(stat, f'st_{key}') for key in keys}
        return get_model_from_obj(data, models.File)


#
# Context managers
#

@contextlib.contextmanager
def log_exception(logger, message):
    try:
        yield
    except Exception:
        logger.exception(message)


#
# Filesystem helpers
#

def walk_files(root, exclude=None):
    if exclude is None:
        exclude = set()

    for path in root.glob('**/*'):
        if path.is_file():
            relpath = path.relative_to(root)
            if str(relpath) not in exclude:
                yield path, relpath

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
        async with asyncio.timeout(timeout):
            await client.disconnect()


#
# Command line helpers
#
def socket_type(string):
    host, port = string.split(':')
    port = int(port)
    return (host, port)

def get_parser(broker=None, http=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--loglevel', default='warning')
    if broker:
        parser.add_argument('--broker', default=broker)
    if http:
        parser.add_argument('--http', default=http, type=socket_type)
    return parser

def run_parser(parser):
    args = parser.parse_args()

    # Logging
    loglevel = args.loglevel.upper()
    logging.basicConfig(level=loglevel)

    return args


#
# HTTP client helpers
#
def get(url, params=None, model=None):
    response = httpx.get(url, params=params)
    response.raise_for_status()
    json = response.json()
    return json if model is None else model(**json)

def post(url, json=None):
    response = httpx.post(url, json=json)
    response.raise_for_status()
    return response.json()


#
# HTTP server helpers
#
def raise_bad_request(detail):
    raise fastapi.HTTPException(status_code=400, detail=detail)

def raise_not_found(detail='Not Found'):
    raise fastapi.HTTPException(status_code=404, detail=detail)
