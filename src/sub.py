###############################################################################
# PubSub for Blosc2 - Access Blosc2 (and others) datasets via a Pub/Sub pattern
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

# Requirements
from fastapi import FastAPI
from fastapi import responses
from fastapi_websocket_pubsub import PubSubClient
import uvicorn

# Project
import utils


# Configuration
broker = None

# State
sources = {}
topics = {}

app = FastAPI()

async def handle_event(data, topic):
    print(f'RECEIVED {topic=} {data=}')

@app.get('/api/list')
async def app_list():
    return list(topics.keys())

@app.post('/api/follow')
async def app_follow(add: list[str]):
    for name in add:
        if name not in topics:
            # Subscribe
            client = PubSubClient()
            client.start_client(f'ws://{broker}/pubsub')
            client.subscribe(name, handle_event)
            topics[name] = client
            # Get port
            src = name.split('/')[0]
            host = utils.get(f'http://{broker}/api/info/{src}')
            sources[src] = host

@app.post('/api/unfollow')
async def app_unfollow(delete: list[str]):
    for name in delete:
        client = topics.pop(name, None)
        if client is not None:
            await client.disconnect()

@app.get("/api/{src}/{name}/download")
async def app_download(src: str, name: str, response_class=responses.PlainTextResponse):
    host = sources[src]
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
