import argparse

# Requirements
from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi_websocket_pubsub import PubSubEndpoint
from pydantic import BaseModel
import uvicorn


class Publisher(BaseModel):
    name: str
    listen: str

publishers = {}

# Rest interface
app = FastAPI()

@app.get('/publishers')
def get_publishers():
    values = publishers.values()
    return list(values)

@app.post('/publishers')
async def post_publishers(publisher: Publisher):
    publishers[publisher.name] = publisher
    await endpoint.publish(['new'], publisher)
    return publisher

# Pub/Sub interface
router = APIRouter()
endpoint = PubSubEndpoint()
endpoint.register_route(router)
app.include_router(router)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-l', '--listen', default='localhost:8000')
    args = parser.parse_args()

    # Run
    host, port = args.listen.split(':')
    port = int(port)
    uvicorn.run(app, host=host, port=port)
