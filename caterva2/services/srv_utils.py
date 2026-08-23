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
import secrets
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


def ctable_row_range(slice_, nrows):
    """Normalize an ``api/fetch`` ``slice_`` into a CTable row range
    ``(start, stop)``: take the first (row) component, apply None defaults,
    negative wrap, and clamp to ``[0, nrows]``. Used by the local fetch
    branch and by peer providers, so both clamp identically."""
    # slice_ is a single slice/int/tuple; extract row start/stop.
    # Use `is None` (not truthiness) so that stop == 0 stays 0.
    sl0 = slice_[0] if isinstance(slice_, tuple) and len(slice_) > 0 else slice_
    if isinstance(sl0, slice):
        row_start = 0 if sl0.start is None else sl0.start
        row_stop = nrows if sl0.stop is None else sl0.stop
        if row_start < 0:
            row_start += nrows
        if row_stop < 0:
            row_stop += nrows
    elif isinstance(sl0, int):
        row_start = sl0
        if row_start < 0:
            row_start += nrows
        row_stop = row_start + 1
    else:
        row_start, row_stop = 0, nrows

    # Clamp to [0, nrows].
    row_start = max(0, min(row_start, nrows))
    row_stop = max(row_start, min(row_stop, nrows))
    return row_start, row_stop


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


class _Group:
    """What a container hands back for a group it has no object for.

    A DictStore is flat: its keys are paths, so the hierarchy a listing walks is
    the one they spell and a group is a prefix rather than a thing.  Something
    still has to come back for one, since `is_group` is how every caller here
    tells a group from a leaf.
    """

    def __repr__(self):
        return "<group>"


GROUP = _Group()


class _DictStoreAdapter:
    """Adapter for a ``.b2z`` holding a flat DictStore.

    A DictStore has no tree of its own -- no children, no descendants -- but its
    keys are paths, so the hierarchy a listing needs is the one they spell.  A
    group is a prefix that some key continues, and every key is a leaf.
    """

    def __init__(self, store):
        self.store = store

    def _keys(self):
        return [k if k.startswith("/") else f"/{k}" for k in list(self.store.keys())]

    def leaves(self, prefix="/"):
        rel = prefix if prefix.startswith("/") else f"/{prefix}"
        rel = rel.rstrip("/")
        return [k for k in self._keys() if k == rel or k.startswith(f"{rel}/")]

    def size(self, prefix="/"):
        return treestore_size(self.store, prefix)

    def get(self, key):
        try:
            return self.store[key]
        except (KeyError, ValueError):
            # As for a TreeStore: keys come straight from the URL, so a
            # malformed one is a 404 rather than a 500
            pass
        # Not a key, so it may still be a group: the store has no object for one
        # -- its keys are paths and nothing else -- but a prefix some key
        # continues is a directory as far as anything browsing this is
        # concerned, and answering None for it would 404 a path the listing
        # itself hands out
        rel = f"/{key.lstrip('/')}".rstrip("/")
        if not rel or any(k.startswith(f"{rel}/") for k in self._keys()):
            return GROUP
        return None

    def leaf_size(self, key):
        node = self.get(key)
        if node is None or self.is_group(node):
            return None
        return getattr(node, "cbytes", None)

    def is_group(self, node):
        # A DictStore stores leaves only, so the only group it has is the one
        # `get` synthesizes from a prefix its keys share
        return node is GROUP

    def close(self):
        with contextlib.suppress(Exception):
            self.store.close()


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
            store = blosc2.open(abspath)
        except Exception:
            return None
        # A `.b2z` used to open as a TreeStore whatever wrote it, the extension
        # deciding; blosc2 now reads the store type the file records, so one
        # written through the flat DictStore API comes back a DictStore.  Its
        # keys are still paths, and paths are all a listing needs -- a
        # `TreeStore` is a `DictStore`, so this order is the specific one first
        if isinstance(store, blosc2.TreeStore):
            return _TreeStoreAdapter(store)
        if isinstance(store, blosc2.DictStore):
            return _DictStoreAdapter(store)
        return None
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
        # A missing key is a missing attribute: a model may name a field that
        # whoever produced this dict never heard of (a peer running an older
        # Caterva2 answering `api/info`, say), and that field is to be left at
        # its default, exactly as for an object that lacks the attribute
        def getter(o, k):
            try:
                return o[k]
            except KeyError as exc:
                raise AttributeError(k) from exc
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


def is_hdf5_proxy_meta(meta):
    """Whether `meta` describes a `.b2nd` that proxies an HDF5 dataset.

    The same mark `open_b2` keys on, read back off the metadata: such a file is
    an `NDArray` on disk and an `HDF5Proxy` once opened, and only the second of
    those says what `api/fetch` will do with it -- rebuild what it serves, rather
    than send the stored file.
    """
    vlmeta = getattr(getattr(meta, "schunk", None), "vlmeta", None) or {}
    return vlmeta.get("_ftype") == "hdf5"


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
        if isinstance(obj, blosc2.DictStore):
            # A `.b2z` written through the flat DictStore API: browsed as a group
            # like a TreeStore one, its keys being the paths of its leaves.  After
            # the TreeStore branch, which is the specific one -- a `TreeStore` is
            # a `DictStore` and would be caught here otherwise
            return models.Directory(mtime=mtime, size=size, nfiles=len(list(obj.keys())))
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


RANGES_OK = {"Accept-Ranges": "bytes"}
"""Header for a response whose bytes can be seeked to, and so served in ranges."""

NO_RANGES = {"Accept-Ranges": "none"}
"""Header for a response that is built as it is sent, and so cannot be seeked.

A dataset served straight from a file gets byte ranges for free -- Starlette's
`FileResponse` implements RFC 7233 by itself -- and a client can then read the
blocks of a slice instead of whole chunks.  A `StreamingResponse` has no such
thing and ignores the `Range` header entirely, so a client that asks for 32
bytes gets the whole body with a 200 and no way to notice.  This says which is
which, and `refuse_range` makes the mistake cheap instead of silent.
"""


def parse_ranges(range_header, size):
    """The spans a ``Range: bytes=`` header names over *size* bytes.

    Sorted, and the ones that touch merged, which is what Starlette does to the
    ranges of a file response: a client sees one shape of answer whichever path
    served it, and none of them can count on getting a part per span it asked
    for.  An empty list means nothing it named is satisfiable (a 416); None
    means it is not a byte range at all, which RFC 7233 says to ignore rather
    than refuse.  Anything malformed raises ValueError.
    """
    units, _, spec = range_header.partition("=")
    if units.strip().lower() != "bytes":
        return None
    spans = []
    for part in spec.split(","):
        first, sep, last = part.strip().partition("-")
        if not sep:
            raise ValueError(f"not a range: {part.strip()!r}")
        if not first:  # bytes=-N: the last N bytes, which is how a suffix is asked for
            wanted = int(last)
            start, end = max(size - wanted, 0), size - 1
            if wanted <= 0:
                continue
        else:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        if start > end or start >= size:
            continue  # unsatisfiable on its own: dropped, as RFC 7233 says
        spans.append((start, end))
    if not spans:
        return spans
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def ranged_window_response(
    abspath, window, range_header, media_type="application/octet-stream", headers=None
):
    """Answer *range_header* out of the ``(offset, size)`` *window* of *abspath*.

    The window is a leaf's frame inside a container, so the coordinates are the
    leaf's own: its byte 0 is the frame's first byte, and a range reaching past
    its end is clamped to it.  Nothing outside the window a client named is ever
    served through here, whatever it asks for.

    *headers* carries the validator, where the caller has one: a ranged read of a
    leaf is the two-request pattern -- header first, chunk offsets second -- and
    without an `ETag` the client cannot tell that the container was not rewritten
    between them, which is exactly when the offsets it reads are someone else's.

    None when the header is not a byte range at all, which leaves the caller to
    answer as if it had not been sent.
    """
    headers = headers or {}
    offset, size = window
    try:
        spans = parse_ranges(range_header, size)
    except ValueError:
        raise_bad_request(f"malformed Range header: {range_header!r}")
    if spans is None:
        return None
    if not spans:
        raise fastapi.HTTPException(
            status_code=416,
            detail=f"none of {range_header!r} lies within the {size} bytes there are",
            headers={**RANGES_OK, "Content-Range": f"bytes */{size}"},
        )
    with open(abspath, "rb") as frame:
        if len(spans) == 1:
            start, end = spans[0]
            frame.seek(offset + start)
            return fastapi.responses.Response(
                frame.read(end - start + 1),
                status_code=206,
                media_type=media_type,
                headers={**RANGES_OK, **headers, "Content-Range": f"bytes {start}-{end}/{size}"},
            )
        # Several at once, which is one round trip instead of one each: RFC 7233
        # carries them as a multipart body, each part saying which bytes it holds
        boundary = secrets.token_hex(16)
        body = bytearray()
        for start, end in spans:
            frame.seek(offset + start)
            body += (
                f"--{boundary}\r\nContent-Type: {media_type}\r\n"
                f"Content-Range: bytes {start}-{end}/{size}\r\n\r\n"
            ).encode()
            body += frame.read(end - start + 1) + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
    return fastapi.responses.Response(
        bytes(body),
        status_code=206,
        media_type=f"multipart/byteranges; boundary={boundary}",
        headers={**RANGES_OK, **headers},
    )


def window_response(abspath, window, range_header=None, headers=None):
    """The stored image of a container leaf, out of the *window* of *abspath*.

    What `FileResponse` is for a dataset with a file of its own, for one that
    lives inside a container: the whole of its frame, or the ranges of it a
    client asked for.  Streamed rather than read whole, since a leaf can be as
    large as any dataset.

    *headers* goes on either answer, and carries the container's validator: a
    leaf is a window of that file, so what says the file did not change says the
    window did not move.  `FileResponse` gets the same one for a stored dataset,
    and `api/info` reports it for both, which is what a client compares against.
    """
    headers = headers or {}
    if range_header:
        ranged = ranged_window_response(abspath, window, range_header, headers=headers)
        if ranged is not None:  # None: not a byte range, so answer as if unasked
            return ranged
    offset, size = window

    def stream():
        with open(abspath, "rb") as container:
            container.seek(offset)
            left = size
            while left > 0:
                data = container.read(min(left, 2**20))
                if not data:
                    break
                left -= len(data)
                yield data

    return fastapi.responses.StreamingResponse(
        stream(),
        media_type="application/octet-stream",
        headers={**RANGES_OK, **headers, "Content-Length": str(size)},
    )


def refuse_range(range_header, path):
    """416 a ranged request for something that is computed rather than stored."""
    if range_header:
        raise fastapi.HTTPException(
            status_code=416,
            detail=(
                f"{path} is not served from a file (it is computed, filtered or held "
                "inside a container), so byte ranges are not available for it"
            ),
            headers=NO_RANGES,
        )


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
