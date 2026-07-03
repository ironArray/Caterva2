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
import h5py
import safer
from fastapi_users.exceptions import UserNotExists
from sqlalchemy.future import select

# Project
from caterva2 import hdf5, models
from caterva2.services import db, schemas, settings, users

# Shared suffix constants
BLOSC2_ARRAY_SUFFIXES = {".b2nd", ".b2frame"}
BLOSC2_TABLE_SUFFIXES = {".b2z"}
BLOSC2_FRAME_SUFFIXES = {".b2"}
BLOSC2_NATIVE_SUFFIXES = BLOSC2_ARRAY_SUFFIXES | BLOSC2_TABLE_SUFFIXES | BLOSC2_FRAME_SUFFIXES

HDF5_SUFFIXES = {".h5", ".hdf5"}

# Container suffixes whose paths may descend into internal (virtual) members.
BLOSC2_CONTAINER_SUFFIXES = {".b2z"} | HDF5_SUFFIXES


def split_container_path(path):
    """Split a request path at a container-file boundary.

    A ``.b2z`` may hold a TreeStore, so a request path can descend *into* it,
    e.g. ``@public/dir/tree.b2z/level1/ctable``. Return
    ``(container_path, inner_key)`` where ``container_path`` is the ``.b2z``
    file and ``inner_key`` is a ``/...`` TreeStore key, or ``(path, None)`` when
    the path does not descend into a container.
    """
    parts = pathlib.Path(path).parts
    for i, part in enumerate(parts):
        if pathlib.PurePath(part).suffix in BLOSC2_CONTAINER_SUFFIXES and i < len(parts) - 1:
            return pathlib.Path(*parts[: i + 1]), "/" + "/".join(parts[i + 1 :])
    return pathlib.Path(path), None


def treestore_leaves(tree, prefix="/"):
    """Full leaf keys (e.g. ``/g/a``) under ``prefix`` of an open TreeStore.

    Leaves are nodes with no children (groups are skipped), matching the
    file-only semantics of :func:`walk_files` for directories.
    """
    return [d for d in tree.get_descendants(prefix) if not tree.get_children(d)]


def treestore_size(tree, prefix="/"):
    """On-disk size (bytes) of leaves under ``prefix``, summed cheaply from the
    ``.b2z`` zip index without opening any leaf. Returns None if unavailable."""
    get_offsets = getattr(tree, "_get_zip_offsets", None)
    if get_offsets is None:
        return None
    rel = prefix.strip("/")
    rel = f"{rel}/" if rel else ""
    return sum(info.get("length", 0) for m, info in get_offsets().items() if m.startswith(rel))


class _TreeStoreAdapter:
    """Adapter for a ``.b2z`` holding a TreeStore. Leaves survive `close()`
    (they're independent objects once fetched), so callers may close eagerly."""

    def __init__(self, tree):
        self.tree = tree

    def leaves(self, prefix="/"):
        try:
            return treestore_leaves(self.tree, prefix)
        except (KeyError, ValueError):
            return []

    def size(self, prefix="/"):
        return treestore_size(self.tree, prefix)

    def get(self, key):
        try:
            return self.tree[key]
        except (KeyError, ValueError):
            # ValueError: TreeStore rejects malformed keys (NUL bytes, empty
            # segments), and keys come straight from the URL — 404, not 500.
            return None

    def leaf_size(self, key):
        """Cheap on-disk (compressed) size of a single leaf, or None if
        `key` doesn't resolve to a leaf. Unlike `size()`, this doesn't sum
        anything: `treestore_size`'s prefix-matching is for group subtrees
        and doesn't match an exact leaf key."""
        node = self.get(key)
        if node is None or self.is_group(node):
            return None
        return node.cbytes

    def is_group(self, node):
        return isinstance(node, blosc2.TreeStore)

    def close(self):
        self.tree.close()


class _HDF5Adapter:
    """Adapter for a ``.h5``/``.hdf5`` file. Unlike TreeStore leaves, a leaf
    ``HDF5Proxy`` needs the underlying ``h5py.File`` to stay open for reads
    that happen after `get()` returns, so callers reading leaf data should
    let this adapter be garbage-collected rather than closing it eagerly
    (CPython drops the file's refcount to zero once the proxy itself goes out
    of scope). Only call `close()` when done with `leaves()`/`size()` alone."""

    def __init__(self, h5file):
        self.h5file = h5file

    def leaves(self, prefix="/"):
        return hdf5.hdf5_leaves(self.h5file, prefix)

    def size(self, prefix="/"):
        return hdf5.hdf5_size(self.h5file, prefix)

    def get(self, key):
        key = key.strip("/")
        try:
            node = self.h5file[key] if key else self.h5file
        except KeyError:
            return None
        if isinstance(node, (h5py.Group, h5py.File)):
            return node
        if not hdf5.h5dset_is_compatible(node):
            return None
        return hdf5.HDF5Proxy.open_leaf(self.h5file, key)

    def leaf_size(self, key):
        """Cheap on-disk (storage) size of a single dataset, or None if
        `key` doesn't resolve to a dataset. Reads straight off the h5py
        Dataset instead of `get()`, to avoid building a full HDF5Proxy
        (which reads Blosc2 super-chunk metadata) just for a listing."""
        key = key.strip("/")
        try:
            node = self.h5file[key] if key else self.h5file
        except KeyError:
            return None
        if isinstance(node, (h5py.Group, h5py.File)):
            return None
        return node.id.get_storage_size()

    def is_group(self, node):
        return isinstance(node, (h5py.Group, h5py.File))

    def close(self):
        self.h5file.close()


def open_container(abspath):
    """Adapter for a *browsable hierarchical* file, or ``None`` if `abspath`
    is not one (single-array .b2z, corrupt/non-container file, wrong suffix)."""
    suffix = abspath.suffix
    if suffix == ".b2z":
        try:
            tree = blosc2.open(abspath)
        except Exception:
            return None
        return _TreeStoreAdapter(tree) if isinstance(tree, blosc2.TreeStore) else None
    if suffix in HDF5_SUFFIXES:
        try:
            return _HDF5Adapter(h5py.File(abspath, "r"))
        except Exception:
            return None
    return None


def open_container_member(abspath, inner_key):
    """The leaf at `inner_key` inside a container file, or ``None`` if the
    container cannot be opened, the key does not exist, or it is a group.

    The returned leaf stays readable after the adapter goes out of scope:
    TreeStore leaves are independent objects, and an HDF5 leaf keeps its
    h5py.File alive via the dataset reference (only an explicit ``.close()``
    on the file invalidates it)."""
    container = open_container(abspath)
    if container is None:
        return None
    node = container.get(inner_key)
    if node is None or container.is_group(node):
        return None
    return node


def is_container_file(abspath):
    """Cheap "is this mountable?" probe for the web path-list hot path (runs
    per file per search keystroke), avoiding a full open where possible."""
    suffix = abspath.suffix
    if suffix in HDF5_SUFFIXES:
        return h5py.is_hdf5(abspath)
    if suffix == ".b2z":
        container = open_container(abspath)
        if container is None:
            return False
        container.close()
        return True
    return False


def container_member_info(abspath, inner_key):
    """Metadata model for a member inside a container file: a
    ``models.Directory`` for a virtual group, dataset metadata for a leaf, or
    ``None`` if the container or the member does not exist."""
    container = open_container(abspath)
    if container is None:
        return None
    try:
        node = container.get(inner_key)
        if node is None:
            return None
        if container.is_group(node):
            # A virtual group: report leaf count and on-disk size (cheap,
            # no per-leaf open).
            return models.Directory(
                mtime=abspath.stat().st_mtime,
                size=container.size(inner_key),
                nfiles=len(container.leaves(inner_key)),
            )
        return read_metadata(node, mtime=abspath.stat().st_mtime)
    finally:
        container.close()


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


def read_metadata(obj, mtime=None):
    # `mtime` is used when `obj` is an already-opened object (e.g. a container
    # leaf) with no file of its own; callers pass the container's mtime.
    # Open dataset
    if isinstance(obj, pathlib.Path):
        path = obj
        if not path.is_file():
            raise FileNotFoundError(f'File "{path}" does not exist or is a directory')
        stat = path.stat()
        mtime = stat.st_mtime
        size = stat.st_size

        if path.suffix in HDF5_SUFFIXES:
            # A browsable .h5/.hdf5 is presented as a directory (its root
            # group), mirroring the .b2z/TreeStore case below; a corrupt or
            # unopenable file falls back to a plain File.
            container = open_container(path)
            if container is None:
                return get_model_from_obj(obj, models.File, mtime=mtime, size=size)
            try:
                return models.Directory(mtime=mtime, size=size, nfiles=len(container.leaves("/")))
            finally:
                container.close()

        assert path.suffix in BLOSC2_NATIVE_SUFFIXES
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

        # A .b2z may hold a TreeStore (a hierarchical container); it is browsed
        # as a group (its leaves are addressed as inner paths), so present the
        # container itself as a directory (the root group).
        if isinstance(obj, blosc2.TreeStore):
            return models.Directory(mtime=mtime, size=size, nfiles=len(treestore_leaves(obj, "/")))
    # else: obj is an already-opened object; keep the caller-supplied mtime

    # Read metadata
    if isinstance(obj, hdf5.HDF5Proxy):
        # A file-less HDF5 leaf proxy (from a container adapter): not an
        # NDArray, so it needs its own branch (unlike the on-disk-proxy
        # NDArray case below, which is keyed on vlmeta["_ftype"]).
        proxy = obj
        cparams = get_model_from_obj(proxy.b2arr.schunk.cparams, models.CParams)
        cparams = reformat_cparams(cparams)
        schunk = get_model_from_obj(proxy.b2arr.schunk, models.SChunk, cparams=cparams)
        schunk.cratio = proxy.cratio
        schunk.cbytes = proxy.cbytes
        return get_model_from_obj(proxy, models.Metadata, schunk=schunk, mtime=mtime)
    elif isinstance(obj, blosc2.ndarray.NDArray):
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
    elif isinstance(obj, blosc2.CTable):
        schema = obj.schema_dict()
        return models.CTableMetadata(
            nrows=obj.nrows,
            ncols=obj.ncols,
            chunks=obj.chunks,
            blocks=obj.blocks,
            schema_dict=schema,
            columns=[c["name"] for c in schema.get("columns", [])],
            nbytes=obj.nbytes,
            cbytes=obj.cbytes,
            cratio=obj.cratio,
            vlmeta=dict(obj.vlmeta[:]) if obj.vlmeta[:] else {},
            mtime=mtime,
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


async def _cleanup_db():
    """Clean up the global database engine and connection pool."""
    if db.engine is not None:
        await db.engine.dispose()
        db.engine = None
        db.async_session_maker = None


def add_user(username, password, is_superuser, state_dir=None):
    result = asyncio.run(aadd_user(username, password, is_superuser, state_dir=state_dir))
    asyncio.run(_cleanup_db())
    return result


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
    result = asyncio.run(adel_user(username))
    asyncio.run(_cleanup_db())
    return result


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
