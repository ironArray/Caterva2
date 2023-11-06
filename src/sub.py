###############################################################################
# PubSub for Blosc2 - Access Blosc2 (and others) datasets via a Pub/Sub pattern
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import contextlib
import logging

# Requirements
from fastapi import FastAPI
from fastapi import responses
import uvicorn

# Project
import utils
from utils import Publisher


logger = logging.getLogger(__name__)

# Configuration
broker = None

# State
publishers = {} # name: <Publisher>
datasets = None # name/path: {}
subscribed = {} # name/path: <PubSubClient>


async def new_dataset(data, topic):
    logger.info(f'NEW dataset {topic} {data=}')
    datasets.update(data)

async def updated_dataset(data, topic):
    logger.info(f'Updated dataset {topic} {data=}')


#
# API
#

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global datasets
    datasets = utils.get(f'http://{broker}/api/datasets')

    client = utils.start_client(f'ws://{broker}/pubsub')
    client.subscribe('@new', new_dataset)
    yield
    print('LIFESPAN DONE')
    await utils.disconnect_client(client)

app = FastAPI(lifespan=lifespan)

@app.get('/api/list')
async def app_list(all: bool = False):
    keys = datasets.keys() if all else subscribed.keys()
    return list(keys)

@app.post('/api/follow')
async def app_follow(add: list[str]):
    for name in add:
        if name not in subscribed:
            # Subscribe
            client = utils.start_client(f'ws://{broker}/pubsub')
            client.subscribe(name, updated_dataset)
            subscribed[name] = client
            # Get port
            src = name.split('/')[0]
            publishers[src] = utils.get(f'http://{broker}/api/info/{src}', model=Publisher)

@app.post('/api/unfollow')
async def app_unfollow(delete: list[str]):
    for name in delete:
        client = subscribed.pop(name, None)
        await utils.disconnect_client(client)

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
