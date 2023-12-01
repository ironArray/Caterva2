###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import contextlib
import logging
from pathlib import Path

# Requirements
import blosc2
from fastapi import FastAPI, responses
import uvicorn
from watchfiles import Change, awatch

# Project
import utils


logger = logging.getLogger('pub')

# Configuration
broker = None
name = None
root = None
nworkers = 1

# State
client = None


async def worker(queue):
    while True:
        abspath, change = await queue.get()
        with utils.log_exception(logger, 'Publication failed'):
            if abspath.is_file():
                relpath = Path(abspath).relative_to(root)
                metadata = utils.read_metadata(abspath)
                metadata = metadata.model_dump()
                data = {'change': change.name, 'path': relpath, 'metadata': metadata}
                await client.publish(name, data=data)

        queue.task_done()


async def watchfiles(queue):
    # Notify the network about available datasets
    # TODO Notify only about changes from previous run, for this purpose we need to
    # persist state
    for path, relpath in utils.walk_files(root):
        queue.put_nowait((path, Change.added))

    # Watch directory for changes
    async for changes in awatch(root):
        for change, path in changes:
            queue.put_nowait((path, change))
    print('THIS SHOULD BE PRINTED ON CTRL+C')


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect to broker
    global client
    client = utils.start_client(f'ws://{broker}/pubsub')

    # Create queue and start workers
    queue = asyncio.Queue()
    tasks = []
    for i in range(nworkers):
        task = asyncio.create_task(worker(queue))
        tasks.append(task)

    # Watch dataset files (must wait before publishing)
    await client.wait_until_ready()
    asyncio.create_task(watchfiles(queue))

    yield

    # Cancel worker tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # Disconnect from broker
    await utils.disconnect_client(client)


app = FastAPI(lifespan=lifespan)

@app.get("/api/list")
async def get_list():
    return [relpath for path, relpath in utils.walk_files(root)]

@app.get("/api/info/{path:path}")
async def get_info(path):
    try:
        return utils.read_metadata(root / path)
    except FileNotFoundError:
        utils.raise_not_found()


def download_chunk(chunk):
    # TODO Send block by block
    yield chunk


def download_file(filepath):
    with open(filepath, 'rb') as file:
        yield from file


@app.get("/api/download/{name:path}")
async def get_download(name: str, nchunk: int = -1):
    filepath = root / name

    suffix = filepath.suffix
    if suffix == '.b2nd':
        if nchunk < 0:
            utils.raise_bad_request('Chunk number required')
        array = blosc2.open(str(filepath))
        chunk = array.schunk.get_chunk(nchunk)
        downloader = download_chunk(chunk)
    elif suffix == '.b2frame':
        if nchunk < 0:
            utils.raise_bad_request('Chunk number required')
        schunk = blosc2.open(str(filepath))
        chunk = schunk.get_chunk(nchunk)
        downloader = download_chunk(chunk)
    else:
        if nchunk >= 0:
            utils.raise_bad_request('Regular files don\'t have chunks')

        downloader = download_file(filepath)

    return responses.StreamingResponse(downloader)


if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8001')
    parser.add_argument('name')
    parser.add_argument('root', default='data')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker
    name = args.name
    root = Path(args.root).resolve()

    # Register
    host, port = args.http
    data = {'name': name, 'http': f'{host}:{port}'}
    utils.post(f'http://{broker}/api/roots', json=data)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
