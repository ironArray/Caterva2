###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Server user management.

Used for user authentication (for the moment), based on FastAPI Users example:
https://fastapi-users.github.io/fastapi-users/latest/configuration/full-example/

The database should only be used if a (non-empty) secret token for the
management of users is set in the 'CATERVA2_SECRET' environment variable.
"""

import functools
import os
import uuid

from fastapi import Depends, Request
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase

from .db import User, get_user_db

conf = ConnectionConfig(
    MAIL_USERNAME="",
    MAIL_PASSWORD="",
    MAIL_FROM="noreply@cat2.cloud",
    MAIL_PORT=25,
    MAIL_SERVER="localhost",
    MAIL_STARTTLS=False,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=False,
    VALIDATE_CERTS=False,
    # SUPRESS_SEND=True,
    MAIL_DEBUG=True,
)


async def send_email(recipients, body):
    from caterva2.services import settings

    message = MessageSchema(
        subject="Reset password of your cat2.cloud account",
        recipients=recipients,
        body=body,
        subtype=MessageType.html,
    )

    if settings.urlbase.startswith("http://localhost:"):
        print("<<<<<<<<<<")
        print(message.body)
        print(">>>>>>>>>>")
    else:
        fm = FastMail(conf)
        await fm.send_message(message)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    @functools.cached_property
    def reset_password_token_secret(self):
        return os.environ.get("CATERVA2_SECRET")

    @functools.cached_property
    def verification_token_secret(self):
        return os.environ.get("CATERVA2_SECRET")

    # TODO: Replace with actual functionality;
    # support user verification and user deletion.

    async def validate_password(self, password: str, user):
        if len(password) < 8:
            raise InvalidPasswordException(reason="Password should be at least 8 characters")

    async def on_after_register(self, user: User, request: Request | None = None):
        print(f"User {user.email} with id {user.id} has been added.")

    async def on_after_forgot_password(self, user: User, token: str, request: Request | None = None):
        from caterva2.services.server import make_url, templates

        template = templates.get_template("emails/forgot-password.html")
        url = make_url(request, "html-reset-password", token=token)
        body = template.render({"reset_url": url})
        await send_email([user.email], body)

    async def on_after_request_verify(self, user: User, token: str, request: Request | None = None):
        print(f"Verification requested for user {user.id}. Verification token: {token}")


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


cookie_transport = CookieTransport(
    cookie_name="c2subauth",
    cookie_secure=False,  # TODO: only for testing
)


def get_jwt_strategy() -> JWTStrategy:
    # The token itself is valid for an hour, even after an explicit logout
    # (however the cookie transport would delete the cookie anyway).
    lifetime_seconds = 3600 * 12
    return JWTStrategy(secret=os.environ.get("CATERVA2_SECRET"), lifetime_seconds=lifetime_seconds)


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
