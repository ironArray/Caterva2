import argparse
import asyncio
import logging

# Requirements
import blosc2
import fastapi_websocket_pubsub
import httpx

# Project
import models


def get_model_from_obj(obj, model_class, **kwargs):
    data = kwargs.copy()
    for key, info in model_class.model_fields.items():
        if key not in data:
            value = getattr(obj, key)
            if info.annotation is str:
                value = str(value)

            data[key] = value

    return model_class(**data)


def read_metadata(path):
    array = blosc2.open(str(path))

#   print(f'{array.schunk.cparams=}')
#   print(f'{array.schunk.dparams=}')
#   print(f'{array.schunk.meta=}')
#   print(f'{array.schunk.vlmeta=}')
#   print(dict(array.schunk.vlmeta))
#   print()

    schunk = get_model_from_obj(array.schunk, models.SChunk)
    return get_model_from_obj(array, models.Metadata, schunk=schunk)


#
# Models (pydantic)
#

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
# HTTP helpers
#
def get(url, params=None, model=None):
    response = httpx.get(url, params=params)
    response.raise_for_status()
    json = response.json()
    return json if model is None else model(**json)

def post(url, json):
    response = httpx.post(url, json=json)
    response.raise_for_status()
    return response.json()
