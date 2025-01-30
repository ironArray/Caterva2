###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################


# Requirements
from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi_websocket_pubsub import PubSubEndpoint

# Project
from caterva2 import models, utils
from caterva2.services import srv_utils

# State
database = None


# API
app = FastAPI()


@app.get("/api/roots", response_model_exclude_none=True)
async def get_roots() -> dict[str, models.Root]:
    return database.roots


@app.post("/api/roots")
async def post_roots(root: models.Root) -> models.Root:
    database.roots[root.name] = root
    database.save()
    await endpoint.publish(["@new"], root)
    return root


# Pub/Sub interface
router = APIRouter()
endpoint = PubSubEndpoint()
endpoint.register_route(router)
app.include_router(router)


def main():
    conf = utils.get_conf("broker")
    parser = utils.get_parser(
        http=conf.get(".http", "localhost:8000"),
        loglevel=conf.get(".loglevel", "warning"),
        statedir=conf.get(".statedir", "_caterva2/bro"),
    )
    args = utils.run_parser(parser)

    # Init database
    # roots = {name: <Root>}
    statedir = args.statedir.resolve()
    global database
    database = srv_utils.Database(statedir / "db.json", models.Broker(roots={}))
    print(database.data)

    # Run
    srv_utils.uvicorn_run(app, args)


if __name__ == "__main__":
    main()
