import argparse

# Requirements
from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi_websocket_pubsub import PubSubEndpoint
import uvicorn


publishers = {}

app = FastAPI()

@app.get("/publishers")
def read_publishers():
    keys = publishers.keys()
    return list(keys)


# Pub/Sub interface
async def on_connect(channel):
    print('CON', channel.id)
    publishers[channel.id] = None

async def on_disconnect(channel):
    print('DIS', channel.id)
    del publishers[channel.id]

router = APIRouter()
endpoint = PubSubEndpoint(
    on_connect=[on_connect],
    on_disconnect=[on_disconnect],
)
endpoint.register_route(router)
app.include_router(router)

async def on_register(subscription, data):
    print(f'REG {subscription} {data=}') # Debug
    name = data['name']
    publishers[name] = None

@app.on_event("startup")
async def startup_event():
    await endpoint.subscribe(['register'], on_register)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-l', '--listen', default='localhost:8000')
    args = parser.parse_args()

    # Run
    host, port = args.listen.split(':')
    port = int(port)
    uvicorn.run(app, host=host, port=port)
