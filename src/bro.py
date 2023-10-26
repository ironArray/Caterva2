import logging

# Requirements
from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi_websocket_pubsub import PubSubEndpoint
from pydantic import BaseModel
import uvicorn

import utils


logger = logging.getLogger(__name__)

class Publisher(BaseModel):
    name: str
    http: str

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
    logger.info(f'New publisher {publisher.name}')
    return publisher

# Pub/Sub interface
router = APIRouter()
endpoint = PubSubEndpoint()
endpoint.register_route(router)
app.include_router(router)

if __name__ == '__main__':
    parser = utils.get_parser(http='localhost:8000')
    args = utils.run_parser(parser)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
