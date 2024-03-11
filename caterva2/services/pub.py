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

# Requirements
import blosc2
from fastapi import FastAPI, Response, responses
import uvicorn

# Project
from caterva2 import utils, api_utils, models
from caterva2.services import pubroot, srv_utils
import caterva2.services.dirroot
try:
    import caterva2.services.hdf5root
except ImportError:
    pass


logger = logging.getLogger('pub')

# Configuration
broker = None
name = None
proot = None
nworkers = 1

# State
cache = None
client = None
database = None  # <Database> instance


async def worker(queue):
    while True:
        relpath = await queue.get()
        with utils.log_exception(logger, 'Publication failed'):
            assert isinstance(relpath, proot.Path)
            key = str(relpath)
            if proot.exists_dset(relpath):
                print('UPDATE', relpath)
                # Load metadata
                if relpath.suffix in {'.b2frame', '.b2nd'}:
                    metadata = proot.get_dset_meta(relpath)
                else:
                    # Compress regular files in publisher's cache
                    with proot.open_dset_raw(relpath) as f:
                        data = f.read()
                    b2path = cache / f'{relpath}.b2'
                    srv_utils.compress(data, b2path)
                    metadata = srv_utils.read_metadata(b2path)

                # Publish
                metadata = metadata.model_dump()
                data = {'path': str(relpath), 'metadata': metadata}
                await client.publish(name, data=data)
                # Update database
                database.etags[key] = proot.get_dset_etag(relpath)
                database.save()
            else:
                print('DELETE', relpath)
                data = {'path': str(relpath)}
                await client.publish(name, data=data)
                # Update database
                if key in database.etags:
                    del database.etags[key]
                    database.save()

        queue.task_done()


async def watch_root(queue):
    # On start, notify the network about changes to the datasets, changes done since the
    # last run.
    etags = database.etags.copy()
    for relpath in proot.walk_dsets():
        key = str(relpath)
        val = etags.pop(key, None)
        if val != proot.get_dset_etag(relpath):
            queue.put_nowait(relpath)

    # The etags left are those that were deleted
    for key in etags:
        relpath = proot.Path(key)
        queue.put_nowait(relpath)
        del database.etags[key]
        database.save()

    # Watch root for changes
    async for changes in proot.awatch_dsets():
        for relpath in changes:
            queue.put_nowait(relpath)

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
    watch_task = asyncio.create_task(watch_root(queue))

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
    return list(proot.walk_dsets())


@app.get("/api/info/{path:path}")
async def get_info(
    path: str,
    response: Response,
    if_none_match: srv_utils.HeaderType = None,
):
    relpath = proot.Path(path)
    srv_utils.check_dset_path(proot, relpath)

    # Check etag
    etag = database.etags[str(relpath)]
    if if_none_match == etag:
        return Response(status_code=304)

    if relpath.suffix in {'.b2frame', '.b2nd'}:
        meta = proot.get_dset_meta(relpath)
    else:
        b2path = srv_utils.get_abspath(cache, '%s.b2' % relpath)
        meta = srv_utils.read_metadata(b2path)

    # Return
    response.headers['Etag'] = etag
    return meta


@app.get("/api/download/{path:path}")
async def get_download(path: str, nchunk: int = -1):
    if nchunk < 0:
        srv_utils.raise_bad_request('Chunk number required')

    relpath = proot.Path(path)
    srv_utils.check_dset_path(proot, relpath)

    if relpath.suffix in {'.b2frame', '.b2nd'}:
        chunk = proot.get_dset_chunk(relpath, nchunk)
    else:
        b2path = cache / ('%s.b2' % relpath)
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
    global broker, name, proot
    broker = args.broker
    name = args.name
    proot = pubroot.make_root(args.root)

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
