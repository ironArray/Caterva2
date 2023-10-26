import argparse
import asyncio
import contextlib
import logging

# Requirements
from fastapi import FastAPI
from fastapi_websocket_pubsub import PubSubClient
import uvicorn


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

def socket(string):
    host, port = string.split(':')
    port = int(port)
    return (host, port)

if __name__ == '__main__':
    # Arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--broker', default='localhost:8000')
    parser.add_argument('--http', default='localhost:8002', type=socket)
    parser.add_argument('--loglevel', default='warning')
    parser.add_argument('topics', action='append', default=[])
    args = parser.parse_args()

    # Logging
    loglevel = args.loglevel.upper()
    logging.basicConfig(level=loglevel)

    # Global configuration
    broker = args.broker
    client = PubSubClient()
    topics = args.topics

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
