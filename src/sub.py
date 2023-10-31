###############################################################################
# PubSub for Blosc2 - Access Blosc2 (and others) datasets via a Pub/Sub pattern
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import contextlib

# Requirements
from fastapi import FastAPI
from fastapi import responses
from fastapi_websocket_pubsub import PubSubClient
import uvicorn

# Project
import utils
from utils import Publisher


# Configuration
broker = None

# State
publishers = {} # name: <Publisher>
datasets = None # name/path: {}
subscribed = {} # name/path: <PubSubClient>


def start_client():
    client = PubSubClient()
    client.start_client(f'ws://{broker}/pubsub')
    return client

async def handle_new(data, topic):
    datasets.update(data)

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global datasets
    datasets = utils.get(f'http://{broker}/api/datasets')

    client = start_client()
    client.subscribe('@new', handle_new)
    yield

app = FastAPI(lifespan=lifespan)

async def handle_event(data, topic):
    print(f'RECEIVED {topic=} {data=}')

@app.get('/api/list')
async def app_list(all: bool = False):
    keys = datasets.keys() if all else subscribed.keys()
    return list(keys)

@app.post('/api/follow')
async def app_follow(add: list[str]):
    for name in add:
        if name not in subscribed:
            # Subscribe
            client = start_client()
            client.subscribe(name, handle_event)
            subscribed[name] = client
            # Get port
            src = name.split('/')[0]
            publishers[src] = utils.get(f'http://{broker}/api/info/{src}', model=Publisher)


@app.post('/api/unfollow')
async def app_unfollow(delete: list[str]):
    for name in delete:
        client = subscribed.pop(name, None)
        if client is not None:
            await client.disconnect()

@app.get("/api/{src}/{name}/download")
async def app_download(src: str, name: str, response_class=responses.PlainTextResponse):
    host = publishers[src].host
    data = utils.get(f'http://{host}/api/{name}/download')
    return data


if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
