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
import datetime
import inspect
import json
import pathlib
import random
import string
import types
import typing

# Requirements
import blosc2
import fastapi
import safer
from fastapi_users.exceptions import UserNotExists
from sqlalchemy.future import select

# Project
from caterva2 import hdf5, models
from caterva2.services import db, schemas, settings, users


def compress_file(path):
    with open(path, "rb") as src:
        data = src.read()
        schunk = blosc2.SChunk(data=data)
        data = schunk.to_cframe()
        path2 = f"{path}.b2"

    with open(path2, "wb") as dst:
        dst.write(data)

    path.unlink()


def get_model_from_obj(obj, model_class, **kwargs):
    if isinstance(obj, dict):

        def getter(o, k):
            return o[k]
    else:
        getter = getattr

    data = kwargs.copy()
    for key, info in model_class.model_fields.items():
        if key not in data:
            try:
                value = getter(obj, key)
            except AttributeError:
                continue

            # Problem is when a dtype is a numpy type, because it can be either a np.dtype
            # instance a class like numpy.dtypes.Int64DType
            # The workaround is to convert the dtype to a string and then in the pydantic
            # model tell to expect str.
            # TODO The correct solution would be to define pydantic custom validators
            # (field_validator).
            annotation = info.annotation
            if value is None:
                pass
            elif annotation is str or (
                isinstance(annotation, types.UnionType)
                and typing.get_args(annotation) == (str, types.NoneType)
            ):
                value = str(value)

            data[key] = value

    # from pprint import pprint
    # pprint(data)
    return model_class(**data)


def read_metadata(obj):
    # Open dataset
    if isinstance(obj, pathlib.Path):
        path = obj
        if not path.is_file():
            raise FileNotFoundError(f'File "{path}" does not exist or is a directory')
        stat = path.stat()
        mtime = stat.st_mtime
        size = stat.st_size

        if path.suffix in {".h5", ".hdf5"}:
            # HDF5 file
            # TODO: extract metadata from HDF5 file
            # try:
            #     import h5py
            # except ImportError:
            #     raise ImportError("h5py is required to read HDF5 files")
            # with h5py.File(path, "r") as f:
            #     obj = f
            return get_model_from_obj(obj, models.File, mtime=mtime, size=size)

        assert path.suffix in {".b2frame", ".b2nd", ".b2"}
        try:
            obj = blosc2.open(path)
        except blosc2.exceptions.MissingOperands as exc:
            error = "Lazy expression with missing operands"
            missing_ops = {k: get_relpath(v) for k, v in exc.missing_ops.items()}
            return get_model_from_obj(
                obj, models.MissingOperands, error=error, expr=exc.expr, missing_ops=missing_ops
            )
        except RuntimeError:
            return get_model_from_obj(obj, models.Corrupt, mtime=mtime, error="Unrecognized format")
    else:
        mtime = None

    # Read metadata
    if isinstance(obj, blosc2.ndarray.NDArray):
        array = obj
        cparams = get_model_from_obj(array.schunk.cparams, models.CParams)
        cparams = reformat_cparams(cparams)
        schunk = get_model_from_obj(array.schunk, models.SChunk, cparams=cparams)
        if "_ftype" in schunk.vlmeta and schunk.vlmeta["_ftype"] == "hdf5":
            array = hdf5.HDF5Proxy(array)
            schunk.cratio = array.cratio  # overwrite cratio (which will be 0) with HDF5Proxy value
            schunk.cbytes = array.cbytes
        return get_model_from_obj(array, models.Metadata, schunk=schunk, mtime=mtime)
    elif isinstance(obj, blosc2.schunk.SChunk):
        schunk = obj
        cparams = get_model_from_obj(schunk.cparams, models.CParams)
        cparams = reformat_cparams(cparams)
        return get_model_from_obj(schunk, models.SChunk, cparams=cparams, mtime=mtime)
    elif isinstance(obj, blosc2.LazyArray):
        # overwrite operands and expression with _tosave versions for metadata display
        if isinstance(obj, blosc2.LazyExpr):
            operands = operands_as_paths(
                obj.operands_tosave if hasattr(obj, "operands_tosave") else obj.operands,
            )
            expression = obj.expression_tosave if hasattr(obj, "expression_tosave") else obj.expression
        else:  # blosc2.LazyUDF
            operands = operands_as_paths(obj.inputs_dict)
            expression = inspect.getsource(obj.func)
        return get_model_from_obj(
            obj,
            models.LazyArray,
            operands=operands,
            mtime=mtime,
            expression=expression,
        )
    else:
        raise TypeError(f"unexpected {type(obj)}")


def reformat_cparams(cparams):
    cparams.__setattr__(
        "filters, meta",
        [
            (cparams.filters[i], cparams.filters_meta[i])
            for i in range(len(cparams.filters))
            if cparams.filters[i] != blosc2.Filter.NOFILTER
        ],
    )
    #   delattr(cparams, 'filters')
    #   delattr(cparams, 'filters_meta')
    return cparams


def get_relpath(path):
    if not isinstance(path, pathlib.Path):
        path = pathlib.Path(path.schunk.urlpath)

    # Public: /.../<public>/<subpath> to <path> (i.e. no change)
    public = settings.public
    if public is not None and path.is_relative_to(public):
        path = path.relative_to(public)
        parts = ["@public"] + list(path.parts)
        return pathlib.Path(*parts)

    # Shared: /.../<shared>/<subpath> to <path> (i.e. no change)
    shared = settings.shared
    if shared is not None and path.is_relative_to(shared):
        path = path.relative_to(shared)
        parts = ["@shared"] + list(path.parts)
        return pathlib.Path(*parts)

    # Personal: /.../<uid>/<subpath> to @personal/<subpath>
    path = path.relative_to(settings.personal)
    parts = list(path.parts)
    parts[0] = "@personal"
    return pathlib.Path(*parts)


def operands_as_paths(operands):
    return {nm: None if op is None else str(get_relpath(op)) for (nm, op) in operands.items()}


#
# Datetime related
#


def epoch_to_iso(time):
    return datetime.datetime.fromtimestamp(time, tz=datetime.UTC).isoformat()


#
# Filesystem helpers
#


def iterdir(root):
    for path in root.iterdir():
        relpath = path.relative_to(root)
        yield path, relpath


def walk_files(root, exclude=None):
    if exclude is None:
        exclude = set()

    if root is not None:
        for path in root.glob("**/*"):
            if path.is_file():
                relpath = path.relative_to(root)
                if str(relpath) not in exclude:
                    yield path, relpath


#
# HTTP server helpers
#

HeaderType = typing.Annotated[str | None, fastapi.Header()]


def raise_bad_request(detail):
    raise fastapi.HTTPException(status_code=400, detail=detail)


def raise_unauthorized(detail="Unauthorized"):
    raise fastapi.HTTPException(status_code=401, detail=detail)


def raise_not_found(detail="Not Found"):
    raise fastapi.HTTPException(status_code=404, detail=detail)


#
# Blosc2 related helpers
#


def compress(data, dst=None):
    assert isinstance(data, (bytes, pathlib.Path))

    if dst is not None:
        dst.parent.mkdir(exist_ok=True, parents=True)
        if dst.exists():
            dst.unlink()

    # Create schunk
    cparams = {}
    dparams = {}
    storage = {
        "urlpath": dst,
        "cparams": cparams,
        "dparams": dparams,
    }
    schunk = blosc2.SChunk(**storage)

    # Append data
    if isinstance(data, pathlib.Path):
        with open(data, "rb") as f:
            data = f.read()

    schunk.append_data(data)

    return schunk


def iterchunk(data: bytes, chunk_size=2**20):
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


#
# Facility to persist program state
#


class Database:
    def __init__(self, path, initial):
        self.path = path
        self.model = initial.__class__
        if path.exists():
            self.load()
        else:
            path.parent.mkdir(exist_ok=True, parents=True)
            self.data = initial
            self.save()

    def load(self):
        with self.path.open() as file:
            dump = json.load(file)
            self.data = self.model.model_validate(dump)

    def save(self):
        dump = self.data.model_dump_json(exclude_none=True)
        with safer.open(self.path, "w") as file:
            file.write(dump)

    def __getattr__(self, name):
        return getattr(self.data, name)


# <https://fastapi-users.github.io/fastapi-users/10.3/cookbook/create-user-programmatically/>
UserAuth = collections.namedtuple("UserAuth", ["username", "password"])


async def aadd_user(username, password, is_superuser, state_dir=None):
    if password is None:
        password = "".join([random.choice(string.ascii_letters) for i in range(8)])
    user = UserAuth(username=username, password=password)

    sub_state = state_dir
    sub_state.mkdir(parents=True, exist_ok=True)
    await db.create_db_and_tables(sub_state)
    cx = contextlib.asynccontextmanager
    async with (
        cx(db.get_async_session)() as session,
        cx(db.get_user_db)(session) as udb,
        cx(users.get_user_manager)(udb) as umgr,
    ):
        # Check that the user does not exist
        try:
            await umgr.get_by_email(user.username)
            return user
        except UserNotExists:
            schema = schemas.UserCreate(
                email=user.username, password=user.password, is_superuser=is_superuser
            )
            await umgr.create(schema)

    return user


def add_user(username, password, is_superuser, state_dir=None):
    return asyncio.run(aadd_user(username, password, is_superuser, state_dir=state_dir))


async def adel_user(username: str):
    async with (
        contextlib.asynccontextmanager(db.get_async_session)() as session,
        contextlib.asynccontextmanager(db.get_user_db)(session) as udb,
        contextlib.asynccontextmanager(users.get_user_manager)(udb) as umgr,
    ):
        user = await umgr.get_by_email(username)
        if user:
            await umgr.delete(user)


def del_user(username):
    return asyncio.run(adel_user(username))


async def alist_users(username=None, exclude=None):
    exclude = exclude or set()
    async with (
        contextlib.asynccontextmanager(db.get_async_session)() as session,
        contextlib.asynccontextmanager(db.get_user_db)(session) as udb,
    ):
        # udb.user_table is likely your SQLModel class (e.g., User)
        UserClass = udb.user_table
        user_table = UserClass.__table__  # <-- this is the actual SQLAlchemy Table

        selected_columns = [col for col in user_table.c if col.name not in exclude]
        query = select(*selected_columns)

        if username:
            query = query.where(user_table.c.email == username)

        result = await session.execute(query)
        rows = result.fetchall()
        return [row._asdict() for row in rows]
