###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import pathlib
import pickle

# Requirements
import httpx



def parse_slice(string):
    if not string:
        return None
    obj = []
    for segment in string.split(','):
        if ':' not in segment:
            segment = int(segment)
        else:
            segment = [int(x) if x else None for x in segment.split(':')]
            segment = slice(*segment)
        obj.append(segment)

    return tuple(obj)


def download(host, path, params):
    response = httpx.get(f'http://{host}/api/download/{path}', params=params)
    response.raise_for_status()
    data = response.content
    download = params.get('download', False)
    slice_ = params.get('slice_', None)
    if not download:
        # TODO: decompression is not working yet. HTTPX does this automatically?
        # data = zlib.decompress(data)
        return pickle.loads(data)
    else:
        path = pathlib.Path(path)
        if slice_:
            suffix = path.suffix
            path = path.with_suffix('')
            path = pathlib.Path(f'{path}[{slice_}]{suffix}')
        # TODO: save chunk by chunk
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

#
# HTTP client helpers
#
def get(url, params=None, headers=None, timeout=5, model=None):
    response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    json = response.json()
    return json if model is None else model(**json)


def post(url, json=None):
    response = httpx.post(url, json=json)
    response.raise_for_status()
    return response.json()

