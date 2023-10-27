# Requirements
from fastapi import FastAPI
from fastapi_websocket_pubsub import PubSubClient
import uvicorn

# Project
import utils


# Configuration
broker = None

# State
topics = {}

app = FastAPI()

async def handle_event(data, topic):
    print(f'RECEIVED {topic=} {data=}')

@app.get('/api/list')
async def app_list():
    return list(topics.keys())

@app.post('/api/follow')
async def app_follow(add: list[str]):
    for topic in add:
        if topic not in topics:
            client = PubSubClient()
            client.start_client(f'ws://{broker}/pubsub')
            client.subscribe(topic, handle_event)
            topics[topic] = client

@app.post('/api/unfollow')
async def app_unfollow(delete: list[str]):
    for topic in delete:
        client = topics.pop(topic, None)
        if client is not None:
            await client.disconnect()


if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
