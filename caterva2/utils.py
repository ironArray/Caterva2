###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import argparse
import asyncio
import collections
import contextlib
import datetime
import logging
import os
import pathlib
import random
import string

import uvicorn
from fastapi_users.exceptions import UserNotExists
from sqlalchemy.future import select

try:
    import tomllib as toml
except ImportError:
    import tomli as toml

from caterva2.services.subscriber import db as sub_db
from caterva2.services.subscriber import schemas as sub_schemas
from caterva2.services.subscriber import users as sub_users

#
# Context managers
#


@contextlib.contextmanager
def log_exception(logger, message):
    try:
        yield
    except Exception:
        logger.exception(message)


#
# Datetime related
#


def epoch_to_iso(time):
    return datetime.datetime.fromtimestamp(time, tz=datetime.UTC).isoformat()


#
# Filesystem helpers
#


def walk_files(root, exclude=None):
    if exclude is None:
        exclude = set()

    for path in root.glob("**/*"):
        if path.is_file():
            relpath = path.relative_to(root)
            if str(relpath) not in exclude:
                yield path, relpath


def iterdir(root):
    for path in root.iterdir():
        relpath = path.relative_to(root)
        yield path, relpath


#
# Command line helpers
#
def urlbase_type(string):
    if not (string.startswith("http://") or string.startswith("https://")):
        raise ValueError("invalid HTTP(S) URL base", string)
    return string


class Socket(str):
    host = None
    port = None
    uds = None

    def __init__(self, string):
        try:
            host, port = string.rsplit(":", maxsplit=1)
        except ValueError:
            host = port = None

        if host and port:
            # Host and port
            self.host = host
            self.port = int(port)
            # Remove brackets from IPv6 addresses
            if host.startswith("[") and host.endswith("]"):
                self.host = host[1:-1]
        else:
            # Unix domain socket
            self.uds = string


def get_parser(loglevel="warning", statedir=None, id=None, http=None, url=None, broker=None):
    parser = argparse.ArgumentParser()
    _add_preliminary_args(parser, id=id)  # just for help purposes
    if broker is not None:
        parser.add_argument("--broker", default=broker, type=Socket, help="socket address of the broker")
    if http is not None:
        parser.add_argument(
            "--http", default=http, type=Socket, help="Listen to given hostname:port or unix socket"
        )
    if statedir is not None:
        parser.add_argument("--statedir", default=statedir, type=pathlib.Path)
    parser.add_argument("--loglevel", default=loglevel)
    return parser


def run_parser(parser):
    args = parser.parse_args()

    # Logging
    loglevel = args.loglevel.upper()
    logging.basicConfig(level=loglevel)

    return args


#
# Web server related
#


def uvicorn_run(app, args, root_path=""):
    http = args.http
    if http.uds:
        uvicorn.run(app, uds=http.uds, root_path=root_path)
    else:
        uvicorn.run(app, host=http.host, port=http.port, root_path=root_path)


#
# Configuration file
#

conf_file_name = "caterva2.toml"


class Conf:
    def __init__(self, conf, prefix=None, id=None):
        self._conf = conf
        self.prefix = prefix
        self.id = id

        self._pfx = (f"{prefix}.{id}" if id else prefix) if prefix else None

    def get(self, key, default=None):
        """Get configuration item with dot-separated `key`.

        The `default` value is returned if any part of the `key` is missing.

        The prefix (if given) is prepended to a `key` that starts with a dot.
        If an ID was given, it is appended to `prefix`, separated by a dot.
        """
        if self._pfx is not None and key.startswith("."):
            key = self._pfx + key
        keys = key.split(".")
        conf = self._conf
        for k in keys:
            conf = conf.get(k)
            if conf is None:
                return default
        return conf


def _add_preliminary_args(parser, id=None):
    parser.add_argument(
        "--conf",
        default=conf_file_name,
        type=pathlib.Path,
        help=("path to alternative configuration file " "(may not exist)"),
    )
    if id is not None:  # the empty string is a valid (default) ID
        parser.add_argument(
            "--id", default=id, help=("a string to distinguish services " "of the same category")
        )


def _parse_preliminary_args(allow_id):
    parser = argparse.ArgumentParser(add_help=False)
    _add_preliminary_args(parser, id="" if allow_id else None)
    return parser.parse_known_args()[0]


def get_conf(prefix=None, allow_id=False):
    """Get settings from the configuration file, if existing.

    If the configuration file does not exist, return an empty configuration.

    You may get the value for a key from the returned configuration ``conf``
    with ``conf.get('path.to.item'[, default])``.  If a `prefix` is given and
    the key starts with a dot, like ``.path.to.item``, `prefix` is prepended
    to it.  If `allow_id` is true and command line arguments has a non-empty
    value for the ``--id`` option, the value gets appended to `prefix`,
    separated by a dot.

    For instance, with ``conf = get_conf('foo')`` and ``--id=bar``,
    ``conf.get('.item')`` is equivalent to ``conf.get('foo.bar.item')``.
    """
    opts = _parse_preliminary_args(allow_id)
    if allow_id and opts.id and any(p in opts.id for p in [os.curdir, os.pardir, os.sep]):
        raise ValueError("invalid identifier", opts.id)

    id_ = opts.id if allow_id else None
    try:
        with open(opts.conf, "rb") as conf_file:
            conf = toml.load(conf_file)
            return Conf(conf, prefix=prefix, id=id_)
    except FileNotFoundError:
        return Conf({}, prefix=prefix, id=id_)


#
# Subscriber related
#

# <https://fastapi-users.github.io/fastapi-users/10.3/cookbook/create-user-programmatically/>
UserAuth = collections.namedtuple("UserAuth", ["username", "password"])


async def aadd_user(username, password, is_superuser, state_dir=None):
    if password is None:
        password = "".join([random.choice(string.ascii_letters) for i in range(8)])
    user = UserAuth(username=username, password=password)

    sub_state = state_dir
    sub_state.mkdir(parents=True, exist_ok=True)
    await sub_db.create_db_and_tables(sub_state)
    cx = contextlib.asynccontextmanager
    async with (cx(sub_db.get_async_session)() as session,
                cx(sub_db.get_user_db)(session) as udb,
                cx(sub_users.get_user_manager)(udb) as umgr):
        # Check that the user does not exist
        try:
            await umgr.get_by_email(user.username)
            return user
        except UserNotExists:
            schema = sub_schemas.UserCreate(
                email = user.username, password = user.password, is_superuser = is_superuser)
            await umgr.create(schema)

    return user


def add_user(username, password, is_superuser, state_dir=None):
    return asyncio.run(aadd_user(username, password, is_superuser, state_dir=state_dir))


async def adel_user(username: str):
    async with (
        contextlib.asynccontextmanager(sub_db.get_async_session)() as session,
        contextlib.asynccontextmanager(sub_db.get_user_db)(session) as udb,
        contextlib.asynccontextmanager(sub_users.get_user_manager)(udb) as umgr,
    ):
        user = await umgr.get_by_email(username)
        if user:
            await umgr.delete(user)


def del_user(username):
    return asyncio.run(adel_user(username))


async def alist_users(username=None):
    async with (
        contextlib.asynccontextmanager(sub_db.get_async_session)() as session,
        contextlib.asynccontextmanager(sub_db.get_user_db)(session) as udb,
    ):
        query = select(udb.user_table)
        if username:
            query = query.where(udb.user_table.email == username)
        result = await session.execute(query)
        # Return a list of dictionaries
        return result.scalars().all()


def list_users(username=None):
    return asyncio.run(alist_users(username))
