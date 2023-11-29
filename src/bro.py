###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

# Requirements
from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi_websocket_pubsub import PubSubEndpoint
import uvicorn

# Project
import models
import utils


# TODO Make this info persistent
roots = {}  # name: <Root>


app = FastAPI()


@app.get('/api/roots')
async def get_roots():
    return roots


@app.post('/api/roots')
async def post_roots(root: models.Root):
    roots[root.name] = root
    await endpoint.publish(['@new'], root)
    return root

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
