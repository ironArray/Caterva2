import argparse
import contextlib

# Requirements
from fastapi import FastAPI
from fastapi_websocket_pubsub import PubSubClient
import uvicorn


broker = None
client = None

async def handle_event(data, topic):
    print(f'{topic=} {data=}')

async def main(sub, name):
    client = PubSubClient()
    client.start_client(f'ws://{broker}/pubsub')

    # Subscribe to topics
    for topic in sub:
        client.subscribe(topic, handle_event)

    # Must wait before publishing
    await client.wait_until_done()

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)

if __name__ == '__main__':
    # Arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('-b', '--broker', default='localhost:8000')
    parser.add_argument('-l', '--listen', default='localhost:8002')
    parser.add_argument('-s', '--sub', action='append', default=[])
    args = parser.parse_args()

    # Connect to broker
    broker = args.broker

    # Run
    host, port = args.listen.split(':')
    port = int(port)
    uvicorn.run(app, host=host, port=port)
