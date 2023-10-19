import argparse
import asyncio

# Requirements
from fastapi_websocket_pubsub import PubSubClient
import uvicorn


HOST = 'localhost:8000'
name = None

async def handle_event(data, topic):
    print(f'{topic=} {data=}')

async def on_connect(client, channel):
    if name:
        await client.publish(['register'], {'name': name}, sync=False)

async def main(sub, name):
    client = PubSubClient(on_connect=[on_connect])
    client.start_client(f'ws://{HOST}/pubsub')

    # Subscribe to topics
    for topic in sub:
        client.subscribe(topic, handle_event)

    # Must wait before publishing
    #await client.wait_until_ready()
    await client.wait_until_done()


if __name__ == '__main__':
    # Arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--sub', action='append', default=[])
    parser.add_argument('-n', '--name')
    args = parser.parse_args()
    name = args.name

    # Run
    coroutine = main(args.sub, args.name)
    asyncio.run(coroutine)

if __name__ == '__main__':
    uvicorn.run('bro:app', reload=True)
