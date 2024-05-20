###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Subscriber user management.

Used for user authentication (for the moment), based on FastAPI Users example:
https://fastapi-users.github.io/fastapi-users/latest/configuration/full-example/

The database should only be used if a (non-empty) secret token for the
management of users is set in the environment variable named by
`SECRET_TOKEN_ENVVAR`.
"""

import functools
import os
import uuid
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase

from .db import User, get_user_db


SECRET_TOKEN_ENVVAR = 'CATERVA2_AUTH_SECRET'


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    @functools.cached_property
    def reset_password_token_secret(self):
        return os.environ.get(SECRET_TOKEN_ENVVAR)

    @functools.cached_property
    def verification_token_secret(self):
        return os.environ.get(SECRET_TOKEN_ENVVAR)

    # TODO: Replace with actual functionality;
    # support user verification, allow password reset and user deletion.

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        print(f"User {user.id} has registered.")

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"User {user.id} has forgot their password. Reset token: {token}")

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"Verification requested for user {user.id}. Verification token: {token}")


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


cookie_transport = CookieTransport(
    cookie_name='c2subauth',
    cookie_secure=False,  # TODO: only for testing
)


def get_jwt_strategy() -> JWTStrategy:
    # The token itself is valid for an hour, even after an explicit logout
    # (however the cookie transport would delete the cookie anyway).
    lifetime_seconds = 3600 * 12
    return JWTStrategy(secret=os.environ.get(SECRET_TOKEN_ENVVAR),
                       lifetime_seconds=lifetime_seconds)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(
    active=True,
    verified=False,  # TODO: set when verification works
)
