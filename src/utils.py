import argparse
import logging

# Requirements
import blosc2
import httpx
import pydantic


# https://www.blosc.org/python-blosc2/reference/ndarray_api.html#attributes
# https://www.blosc.org/python-blosc2/reference/schunk_api.html#attributes
# https://www.blosc.org/python-blosc2/reference/autofiles/schunk/attributes/vlmeta.html#schunk-vlmeta

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


def read_metadata(path):
    array = blosc2.open(path)

#   print(f'{array.schunk.cparams=}')
#   print(f'{array.schunk.dparams=}')
#   print(f'{array.schunk.meta=}')
#   print(f'{array.schunk.vlmeta=}')
#   print(dict(array.schunk.vlmeta))
#   print()

    keys = SChunk.model_fields.keys()
    data = {k: getattr(array.schunk, k) for k in keys}
    schunk = SChunk(**data)

    exclude = {'schunk'}
    keys = Metadata.model_fields.keys()
    data = {k: getattr(array, k) for k in keys if k not in exclude}
    return Metadata(schunk=schunk, **data)


def socket(string):
    host, port = string.split(':')
    port = int(port)
    return (host, port)

def get_parser(broker=None, http=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--loglevel', default='warning')
    if broker:
        parser.add_argument('--broker', default=broker)
    if http:
        parser.add_argument('--http', default=http, type=socket)
    return parser

def run_parser(parser):
    args = parser.parse_args()

    # Logging
    loglevel = args.loglevel.upper()
    logging.basicConfig(level=loglevel)

    return args

def get(url, params=None, model=None):
    response = httpx.get(url, params=params)
    response.raise_for_status()
    json = response.json()
    return json if model is None else model(**json)

def post(url, json):
    response = httpx.post(url, json=json)
    response.raise_for_status()
    return response.json()
