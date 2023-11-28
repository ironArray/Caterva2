###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import contextlib

# Requirements
from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi_websocket_pubsub import PubSubEndpoint
import uvicorn

# Project
import models
import utils


publishers = {}  # name: <Publisher>
datasets = {}    # name/path: {}


# Rest interface
async def handle_new(subscription, data):
    datasets.update(data)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await endpoint.subscribe(['@new'], handle_new)
    yield

app = FastAPI(lifespan=lifespan)


@app.get('/api/datasets')
async def get_datasets():
    return datasets


@app.get('/api/publishers')
async def get_publishers():
    values = publishers.values()
    return list(values)


@app.get('/api/publishers/{name}')
async def get_publisher(name: str):
    return publishers[name].model_dump()


@app.post('/api/publishers')
async def post_publishers(publisher: models.Publisher):
    publishers[publisher.name] = publisher
    return publisher

# Pub/Sub interface
router = APIRouter()
endpoint = PubSubEndpoint()
endpoint.register_route(router)
app.include_router(router)

if __name__ == '__main__':
    parser = utils.get_parser(http='localhost:8000')
    args = utils.run_parser(parser)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
