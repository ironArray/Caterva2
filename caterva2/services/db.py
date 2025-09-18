###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Server database.

Used for user authentication (for the moment), based on FastAPI Users example:
https://fastapi-users.github.io/fastapi-users/latest/configuration/full-example/

The database should only be used if a (non-empty) secret token for the
management of users is set in the 'CATERVA2_SECRET' environment variable.

The database will be stored in SQLite format inside the state directory
given to `create_db_and_tables()`.
"""

from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import Depends
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    pass


engine = None
async_session_maker = None


async def create_db_and_tables(statedir: Path):
    global engine, async_session_maker  # keep global as in original example
    engine = create_async_engine(f"sqlite+aiosqlite:///{statedir}/db.sqlite")
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)
