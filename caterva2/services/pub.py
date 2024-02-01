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
import pathlib

# Requirements
import blosc2
from fastapi import FastAPI, Response, responses
import uvicorn
from watchfiles import awatch

# Project
from caterva2 import utils, api_utils, models
from caterva2.services import srv_utils


logger = logging.getLogger('pub')

# Configuration
broker = None
name = None
root = None
nworkers = 1

# State
cache = None
client = None
database = None  # <Database> instance


def get_etag(abspath):
    stat = abspath.stat()
    return f'{stat.st_mtime}:{stat.st_size}'


async def worker(queue):
    while True:
        abspath = await queue.get()
        with utils.log_exception(logger, 'Publication failed'):
            assert isinstance(abspath, pathlib.Path)
            relpath = abspath.relative_to(root)
            key = str(relpath)
            if abspath.is_file():
                print('UPDATE', relpath)
                # Load metadata
                if abspath.suffix in {'.b2frame', '.b2nd'}:
                    metadata = srv_utils.read_metadata(abspath)
                else:
                    # Compress regular files in publisher's cache
                    b2path = cache / f'{relpath}.b2'
                    srv_utils.compress(abspath, b2path)
                    metadata = srv_utils.read_metadata(b2path)

                # Publish
                metadata = metadata.model_dump()
                data = {'path': relpath, 'metadata': metadata}
                await client.publish(name, data=data)
                # Update database
                database.etags[key] = get_etag(abspath)
                database.save()
            else:
                print('DELETE', relpath)
                data = {'path': relpath}
                await client.publish(name, data=data)
                # Update database
                if key in database.etags:
                    del database.etags[key]
                    database.save()

        queue.task_done()


async def watchfiles(queue):
    # On start, notify the network about changes to the datasets, changes done since the
    # last run.
    etags = database.etags.copy()
    for abspath, relpath in utils.walk_files(root):
        key = str(relpath)
        val = etags.pop(key, None)
        if val != get_etag(abspath):
            queue.put_nowait(abspath)

    # The etags left are those that were deleted
    for key in etags:
        abspath = root / key
        queue.put_nowait(abspath)
        del database.etags[key]
        database.save()

    # Watch directory for changes
    async for changes in awatch(root):
        paths = set([abspath for change, abspath in changes])
        for abspath in paths:
            abspath = pathlib.Path(abspath)
            queue.put_nowait(abspath)

    print('THIS SHOULD BE PRINTED ON CTRL+C')


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect to broker
    global client
    client = srv_utils.start_client(f'ws://{broker}/pubsub')

    # Create queue and start workers
    queue = asyncio.Queue()
    tasks = []
    for _ in range(nworkers):
        task = asyncio.create_task(worker(queue))
        tasks.append(task)

    # Watch dataset files (must wait before publishing)
    await client.wait_until_ready()
    watch_task = asyncio.create_task(watchfiles(queue))

    yield

    # Cancel watch task
    watch_task.cancel()

    # Cancel worker tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # Disconnect from broker
    await srv_utils.disconnect_client(client)


app = FastAPI(lifespan=lifespan)


@app.get("/api/list")
async def get_list():
    return [relpath for abspath, relpath in utils.walk_files(root)]


@app.get("/api/info/{path:path}")
async def get_info(
    path: str,
    response: Response,
    if_none_match: srv_utils.HeaderType = None,
):
    abspath = srv_utils.get_abspath(root, path)

    # Check etag
    etag = database.etags[path]
    if if_none_match == etag:
        return Response(status_code=304)

    # Regular files (.b2)
    if abspath.suffix not in {'.b2frame', '.b2nd'}:
        abspath = srv_utils.get_abspath(cache, f'{path}.b2')

    # Return
    response.headers['Etag'] = etag
    return srv_utils.read_metadata(abspath)


@app.get("/api/download/{path:path}")
async def get_download(path: str, nchunk: int = -1):
    if nchunk < 0:
        srv_utils.raise_bad_request('Chunk number required')

    abspath = srv_utils.get_abspath(root, path)

    suffix = abspath.suffix
    if suffix == '.b2nd':
        array = blosc2.open(abspath)
        schunk = array.schunk
    elif suffix == '.b2frame':
        schunk = blosc2.open(abspath)
    else:
        relpath = pathlib.Path(abspath).relative_to(root)
        b2path = cache / f'{relpath}.b2'
        schunk = blosc2.open(b2path)

    chunk = schunk.get_chunk(nchunk)
    downloader = srv_utils.iterchunk(chunk)

    return responses.StreamingResponse(downloader)


def main():
    conf = utils.get_conf('publisher', allow_id=True)
    _stdir = '_caterva2/pub' + (f'.{conf.id}' if conf.id else '')
    parser = utils.get_parser(broker=conf.get('broker.http', 'localhost:8000'),
                              http=conf.get('.http', 'localhost:8001'),
                              loglevel=conf.get('.loglevel', 'warning'),
                              statedir=conf.get('.statedir', _stdir),
                              id=conf.id)
    parser.add_argument('name', nargs='?', default=conf.get('.name'))
    parser.add_argument('root', nargs='?', default=conf.get('.root', 'data'))
    args = utils.run_parser(parser)
    if args.name is None:  # because optional positional arg w/o conf default
        raise RuntimeError(
            "root name was not specified in configuration nor in arguments")

    # Global configuration
    global broker, name, root
    broker = args.broker
    name = args.name
    root = pathlib.Path(args.root).resolve()

    # Init cache
    global cache
    statedir = args.statedir.resolve()
    cache = statedir / 'cache'
    cache.mkdir(exist_ok=True, parents=True)

    # Init database
    global database
    model = models.Publisher(etags={})
    database = srv_utils.Database(statedir / 'db.json', model)

    # Register
    host, port = args.http
    data = {'name': name, 'http': f'{host}:{port}'}
    api_utils.post(f'http://{broker}/api/roots', json=data)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    main()
