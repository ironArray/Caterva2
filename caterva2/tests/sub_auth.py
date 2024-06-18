###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import asyncio
import collections
import contextlib
import os

import httpx
import pytest
from fastapi_users.exceptions import UserAlreadyExists

from caterva2.services.subscriber import (
    db as sub_db, schemas as sub_schemas, users as sub_users)


UserAuth = collections.namedtuple('UserAuth', ['username', 'password'])


def sub_auth_enabled():
    return bool(os.environ.get(sub_users.SECRET_TOKEN_ENVVAR))


def make_sub_user(services):
    if not sub_auth_enabled():
        return None

    user = UserAuth(username='user@example.com', password='foobar')

    # <https://fastapi-users.github.io/fastapi-users/10.3/cookbook/create-user-programmatically/>
    async def create_user():
        sub_state = services.state_dir / 'subscriber'
        sub_state.mkdir(parents=True, exist_ok=True)
        await sub_db.create_db_and_tables(sub_state)
        try:
            cx = contextlib.asynccontextmanager
            async with cx(sub_db.get_async_session)() as session:
                async with cx(sub_db.get_user_db)(session) as udb:
                    async with cx(sub_users.get_user_manager)(udb) as umgr:
                        await umgr.create(sub_schemas.UserCreate(
                            email=user.username, password=user.password,
                        ))
        except UserAlreadyExists:
            pass

    asyncio.run(create_user())

    return user


@pytest.fixture(scope='session')
def sub_user(services):
    # TODO: This does not work with external services,
    # as their state directory is unknown.
    # One would need to register a new user via the API there.
    return make_sub_user(services)


@pytest.fixture(scope='session')
def sub_jwt_cookie(sub_user, services):
    if not sub_user:
        return None

    username, password = sub_user
    urlbase = services.get_urlbase('subscriber')

    resp = httpx.post(f'{urlbase}auth/jwt/login',
                      data=dict(username=username, password=password))
    resp.raise_for_status()
    return '='.join(list(resp.cookies.items())[0])
