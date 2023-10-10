import argparse
import asyncio

# Requirements
from fastapi_websocket_pubsub import PubSubClient
import httpx


HOST = 'localhost:8000'

async def main(sub):
    client = PubSubClient()

    async def on_event(data, topic):
        print(f'{topic=} {data=}')
        #asyncio.create_task(client.disconnect())

    for topic in sub:
        print('Subscribe to', topic)
        client.subscribe(topic, on_event)

    client.start_client(f'ws://{HOST}/pubsub')
    await client.wait_until_done()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--sub', action='append')
    args = parser.parse_args()

    if args.sub:
        asyncio.run(main(args.sub))
    else:
        print('List of resources...')
        response = httpx.get(f'http://{HOST}/')
        response.raise_for_status()
        json = response.json()
        print(json)
