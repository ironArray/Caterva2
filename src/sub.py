import asyncio
import contextlib
import logging

# Requirements
from fastapi import FastAPI
from fastapi_websocket_pubsub import PubSubClient
import uvicorn

import utils


logger = logging.getLogger(__name__)

# Configuration
broker = None
client = None
topics = None

async def handle_event(data, topic):
    print(f'RECEIVED {topic=} {data=}')

async def main():
    client.start_client(f'ws://{broker}/pubsub')

    # Subscribe to topics
    for topic in topics:
        client.subscribe(topic, handle_event)

    # Must wait before publishing
    await client.wait_until_done()

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(main())
    yield

app = FastAPI(lifespan=lifespan)

if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    parser.add_argument('topics', action='append', default=[])
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker
    client = PubSubClient()
    topics = args.topics

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
