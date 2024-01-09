###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import pickle

# Requirements
import httpx

# To remove


def parse_slice(string):
    if not string:
        return ()
    obj = []
    for segment in string.split(','):
        if ':' not in segment:
            segment = int(segment)
        else:
            segment = [int(x) if x else None for x in segment.split(':')]
            segment = slice(*segment)
        obj.append(segment)

    return tuple(obj)


def fetch_data(host, path, params):
    response = httpx.get(f'http://{host}/api/fetch/{path}', params=params)
    response.raise_for_status()
    data = response.content
    # TODO: decompression is not working yet. HTTPX does this automatically?
    # data = zlib.decompress(data)
    return pickle.loads(data)

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

