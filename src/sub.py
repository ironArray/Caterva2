import logging

# Requirements
from fastapi import FastAPI
from fastapi_websocket_pubsub import PubSubClient
import uvicorn

import utils


logger = logging.getLogger(__name__)

# Configuration
broker = None
topics = None

app = FastAPI()

async def handle_event(data, topic):
    print(f'RECEIVED {topic=} {data=}')

@app.get('/topics')
async def get_topics():
    return topics.keys()

@app.post('/topics')
async def post_topics(add: list[str]):
    for topic in add:
        if topic not in topics:
            client = PubSubClient()
            client.start_client(f'ws://{broker}/pubsub')
            client.subscribe(topic, handle_event)
            topics[topic] = client


if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker
    topics = {}

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
