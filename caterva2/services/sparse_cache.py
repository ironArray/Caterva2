"""Experimental private RemoteArray generations with shared soft admission.

All request mutations hold the existing path lock and a generation lock. Startup
recovery discards interrupted disposable generations without resolving sources.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import time
import uuid

import blosc2

from caterva2.services.storage_quota import QuotaExceeded, StorageBusy, file_lock, signature, sync_directory

log = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_objects (
    object_id TEXT PRIMARY KEY, path TEXT UNIQUE, carrier_generation TEXT NOT NULL,
    spec_hash TEXT NOT NULL, source_stamp TEXT, active_generation TEXT,
    parent_charge_bytes INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS remote_generations (
    generation_id TEXT PRIMARY KEY, object_id TEXT NOT NULL, relpath TEXT UNIQUE NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('building','active','retired','trash')),
    spec_hash TEXT NOT NULL, source_stamp TEXT NOT NULL, max_cache_bytes INTEGER,
    payload_bytes INTEGER NOT NULL, charge_bytes INTEGER NOT NULL, inode_count INTEGER NOT NULL,
    touched REAL NOT NULL, created REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_remote_generation
ON remote_generations(object_id) WHERE state='active';
CREATE TABLE IF NOT EXISTS remote_operations (
    id TEXT PRIMARY KEY, generation_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
    estimate INTEGER NOT NULL, previous_charge INTEGER NOT NULL, details BLOB NOT NULL,
    started REAL NOT NULL);
CREATE TABLE IF NOT EXISTS remote_work (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, reserved INTEGER NOT NULL,
    relpath TEXT NOT NULL, started REAL NOT NULL);
CREATE TABLE IF NOT EXISTS remote_orphans (
    id TEXT PRIMARY KEY, relpath TEXT UNIQUE NOT NULL, charge_bytes INTEGER NOT NULL,
    inode_count INTEGER NOT NULL, updated REAL NOT NULL);
"""


def allocated(path):
    st = path.lstat()
    return getattr(st, "st_blocks", None) * 512 if hasattr(st, "st_blocks") else st.st_size


def measure(path):
    """Never follow links, including an unexpected child link."""
    if not path.exists() and not path.is_symlink():
        return 0, 0
    total, count = allocated(path), 1
    if path.is_dir() and not path.is_symlink():
        for entry in path.iterdir():
            size, n = measure(entry)
            total += size
            count += n
    return total, count


def sync_tree(path):
    for entry in path.iterdir():
        if entry.is_symlink():
            raise ValueError("unexpected entry in sparse frame")
        if entry.is_dir():
            sync_tree(entry)
            continue
        with entry.open("rb") as stream:
            os.fsync(stream.fileno())
    sync_directory(path)
    sync_directory(path.parent)


class SparseCache:
    def __init__(self, quota, *, initialize=True):
        self.q = quota
        if quota.cache_backend == "sparse":
            import inspect

            required = ("with_sparse_cache", "read_cached", "trim_sparse_cache")
            if (
                any(not hasattr(blosc2.RemoteArray, name) for name in required)
                or "source_descriptor"
                not in inspect.signature(blosc2.RemoteArray.with_sparse_cache).parameters
            ):
                raise RuntimeError("sparse backend requires the Python-Blosc2 v7 cache APIs")
        self.root = quota.root / ".remote-cache"
        if self.root.is_symlink():
            raise ValueError("private cache root cannot be a symlink")
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.trash = self.root / ".trash"
        if self.trash.is_symlink():
            raise ValueError("private trash cannot be a symlink")
        self.trash.mkdir(mode=0o700, exist_ok=True)
        if initialize:
            with quota.connect() as db:
                db.executescript(SCHEMA)
                generation_columns = {row[1] for row in db.execute("PRAGMA table_info(remote_generations)")}
                if "kind" not in generation_columns:
                    db.execute(
                        "ALTER TABLE remote_generations ADD COLUMN kind TEXT NOT NULL DEFAULT 'array'"
                    )
                columns = {row[1] for row in db.execute("PRAGMA table_info(account)")}
                if "cache_fill_suspended" not in columns:
                    db.execute(
                        "ALTER TABLE account ADD COLUMN cache_fill_suspended INTEGER NOT NULL DEFAULT 0"
                    )
                if "cache_backend" not in columns:
                    db.execute("ALTER TABLE account ADD COLUMN cache_backend TEXT NOT NULL DEFAULT 'sparse'")
                previous = db.execute("SELECT cache_backend FROM account").fetchone()[0]
                if (
                    db.execute("PRAGMA user_version").fetchone()[0] in (2, 3)
                    and previous != quota.cache_backend
                ):
                    raise StorageBusy("backend switch requires explicit offline configuration migration")
                db.execute("UPDATE account SET cache_backend=?", (quota.cache_backend,))
                db.execute("PRAGMA user_version=3")
        with quota.connect() as db:
            if db.execute("SELECT cache_backend FROM account").fetchone()[0] != quota.cache_backend:
                raise StorageBusy("backend switch requires draining storage workers")

    @staticmethod
    def totals(db):
        used = db.execute("SELECT coalesce(sum(charge_bytes),0) FROM remote_generations").fetchone()[0]
        used += db.execute("SELECT coalesce(sum(parent_charge_bytes),0) FROM remote_objects").fetchone()[0]
        used += db.execute("SELECT coalesce(sum(charge_bytes),0) FROM remote_orphans").fetchone()[0]
        reserved = db.execute("SELECT coalesce(sum(estimate),0) FROM remote_operations").fetchone()[0]
        work = db.execute("SELECT coalesce(sum(reserved),0) FROM remote_work").fetchone()[0]
        return used, reserved, work

    def path(self, rel):
        if not re.fullmatch(r"\.remote-cache/(?:[0-9a-f]{32}/[0-9a-f]{32}|\.trash/[0-9a-f]{32})", rel):
            raise ValueError("invalid private generation path")
        path = self.q.root
        for part in rel.split("/"):
            path /= part
            if path.is_symlink():
                raise ValueError("private cache symlink")
        return path

    def guard(self, gid, *, blocking=True):
        if not re.fullmatch("[0-9a-f]{32}", gid):
            raise ValueError("invalid generation ID")
        return file_lock(self.q.control / f"remote-{gid}.lock", blocking=blocking)

    def retire_path(self, rel):
        """Caller owns the dataset path guard; physical cleanup is deferred."""
        with self.q.transaction() as db:
            rows = db.execute("SELECT object_id FROM remote_objects WHERE path=?", (rel,)).fetchall()
            for (oid,) in rows:
                db.execute("UPDATE remote_generations SET state='retired' WHERE object_id=?", (oid,))
                db.execute(
                    "UPDATE remote_objects SET path=NULL,active_generation=NULL WHERE object_id=?", (oid,)
                )

    def _intent(self, gid, kind, estimate=0, details=None):
        with self.q.transaction() as db:
            db.execute(
                "INSERT INTO remote_operations VALUES(?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, gid, kind, estimate, 0, json.dumps(details or {}), time.time()),
            )

    def _finish(self, gid, path, payload):
        charge, inodes = measure(path)
        parent_charge = allocated(path.parent)
        with self.q.transaction() as db:
            db.execute(
                "UPDATE remote_generations SET charge_bytes=?,inode_count=?,payload_bytes=?,touched=? "
                "WHERE generation_id=?",
                (charge, inodes, payload, time.time(), gid),
            )
            db.execute(
                "UPDATE remote_objects SET parent_charge_bytes=? WHERE object_id="
                "(SELECT object_id FROM remote_generations WHERE generation_id=?)",
                (parent_charge, gid),
            )
            db.execute("DELETE FROM remote_operations WHERE generation_id=?", (gid,))
        self._suspension()

    def _suspension(self):
        usage = self.q.usage()
        limit = usage["quota"]
        if not limit or usage["used"] <= int(limit * 0.9):
            suspended = 0
        elif usage["used"] > limit:
            suspended = 1
        else:
            return
        with self.q.transaction() as db:
            db.execute("UPDATE account SET cache_fill_suspended=?", (suspended,))

    def _admit(self, gid, estimate):
        with self.q.transaction() as db:
            used = db.execute("SELECT coalesce(sum(size),0) FROM objects").fetchone()[0]
            reserved = db.execute("SELECT coalesce(sum(reserved),0) FROM operations").fetchone()[0]
            cache, estimates, _ = self.totals(db)
            limit, suspended = db.execute("SELECT quota,cache_fill_suspended FROM account").fetchone()
            if limit and (suspended or used + cache + reserved + estimates + estimate > limit):
                return False
            db.execute(
                "INSERT INTO remote_operations VALUES(?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, gid, "fill", estimate if limit else 0, 0, "{}", time.time()),
            )
        return True

    def _attach(self, proxy, path, carrier=None):
        return blosc2.RemoteArray.with_sparse_cache(
            proxy.src,
            path,
            source_descriptor=proxy.requested_payload["source"],
            carrier=carrier,
            max_cache_bytes=proxy.max_cache_bytes,
        )

    def _bind(self, proxy, rel):
        sig = signature(proxy.path)
        if sig is None or sig != proxy.carrier_generation:
            raise StorageBusy("public carrier changed after authorization")
        spec = hashlib.sha256(json.dumps(proxy.requested_payload, sort_keys=True).encode()).hexdigest()
        stamp = json.dumps(proxy.src.stamp, sort_keys=True)
        with self.q.connect() as db:
            row = db.execute(
                "SELECT o.object_id,o.active_generation,o.carrier_generation,o.spec_hash,"
                "o.source_stamp,g.relpath FROM remote_objects o LEFT JOIN remote_generations g "
                "ON g.generation_id=o.active_generation WHERE o.path=?",
                (rel,),
            ).fetchone()
        if row and row[1] and row[2:5] == (json.dumps(sig), spec, stamp):
            path = self.path(row[5])
            if path.is_dir():
                return row[1], path
        if row:
            self.retire_path(rel)
        with file_lock(self.q.control / "remote-migration.lock", blocking=False):
            return self._build(proxy, rel, sig, spec, stamp)

    def _build(self, proxy, rel, sig, spec, stamp):
        from caterva2.services import remote_proxy

        oid, gid = uuid.uuid4().hex, uuid.uuid4().hex
        path = self.root / oid / gid
        relative = path.relative_to(self.q.root).as_posix()
        now = time.time()
        if shutil.disk_usage(self.root).free < sig[2] + (1 << 30):
            raise QuotaExceeded("insufficient migration headroom")
        with self.q.transaction() as db:
            db.execute(
                "INSERT INTO remote_objects VALUES(?,?,?,?,?,?,?,?)",
                (oid, rel, json.dumps(sig), spec, stamp, None, 0, now),
            )
            db.execute(
                "INSERT INTO remote_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'array')",
                (gid, oid, relative, "building", spec, stamp, proxy.max_cache_bytes, 0, 0, 0, now, now),
            )
        with self.guard(gid):
            self._intent(gid, "build", sig[2])
            path.parent.mkdir(mode=0o700)
            carrier = remote_proxy.raw_carrier(proxy.path)
            if carrier.schunk.vlmeta.get("b2o") != proxy.requested_payload or (
                carrier.shape,
                carrier.dtype,
                carrier.chunks,
                carrier.blocks,
            ) != (proxy.shape, proxy.dtype, proxy.chunks, proxy.blocks):
                raise StorageBusy("carrier specification changed during source authorization")
            runtime = self._attach(proxy, path, carrier)
            payload_bytes = runtime.cached_payload_bytes
            del runtime
            sync_tree(path)
            charge, count = measure(path)
            cold = remote_proxy.cold_cframe(carrier, proxy.requested_payload)
            del carrier
            parent_charge = allocated(path.parent)
            with self.q.transaction() as db:
                db.execute(
                    "UPDATE remote_generations SET state='active',charge_bytes=?,inode_count=?,"
                    "payload_bytes=? WHERE generation_id=?",
                    (charge, count, payload_bytes, gid),
                )
                db.execute(
                    "UPDATE remote_objects SET active_generation=?,parent_charge_bytes=? WHERE object_id=?",
                    (gid, parent_charge, oid),
                )
                db.execute(
                    "UPDATE remote_operations SET kind='coldify',estimate=0 WHERE generation_id=?", (gid,)
                )
            new_sig = self.q.publish_locked(rel, cold, expected=sig, preserve_remote=True)
            with self.q.transaction() as db:
                db.execute(
                    "UPDATE remote_objects SET carrier_generation=? WHERE object_id=?",
                    (json.dumps(new_sig), oid),
                )
                db.execute("DELETE FROM remote_operations WHERE generation_id=?", (gid,))
            proxy.carrier_generation = new_sig
        return gid, path

    def read(self, proxy, item=(), *, nchunk=None):
        """Fall back only for local retention errors, preserving upstream failures."""

        def uncached():
            return (
                proxy.src.get_chunk(nchunk)
                if nchunk is not None
                else blosc2.Proxy(proxy.src, _refresh_source=False)[item]
            )

        if proxy.cache_policy != "disk" or proxy.src.stamp is None:
            return uncached()
        result = None
        assembled = False
        try:
            rel = self.q.relative(proxy.path)
            with file_lock(self.q.control / "initialize.lock", shared=True), self.q.lock(rel):
                gid, path = self._bind(proxy, rel)
                with self.guard(gid):
                    with self.q.connect() as db:
                        pending = db.execute(
                            "SELECT 1 FROM remote_operations WHERE generation_id=?", (gid,)
                        ).fetchone()
                    if pending:
                        raise StorageBusy("generation needs recovery")
                    runtime = self._attach(proxy, path)
                    try:
                        hit, result = runtime.read_cached(item, nchunk=nchunk)
                        if hit:
                            assembled = True
                            with self.q.transaction() as db:
                                db.execute(
                                    "UPDATE remote_generations SET touched=? WHERE generation_id=? AND touched<?",
                                    (time.time(), gid, time.time() - 10),
                                )
                            return result
                        # Admission is deliberately coarse; the full-generation stat
                        # Full-generation measurement is the safe fallback until mutation reports
                        # are available.
                        estimate = (
                            min(
                                proxy.dtype.itemsize * math.prod(proxy.chunks),
                                proxy.max_cache_bytes or (1 << 60),
                            )
                            + 16384
                        )
                        if not self._admit(gid, estimate):
                            raise QuotaExceeded("cache retention refused")
                        result = runtime[item] if nchunk is None else runtime.get_chunk(nchunk)
                        assembled = True
                        payload = runtime.cached_payload_bytes
                    finally:
                        del runtime
                    sync_tree(path)
                    self._finish(gid, path, payload)
        except (OSError, sqlite3.Error, RuntimeError, QuotaExceeded, ValueError):
            log.debug("sparse retention unavailable", exc_info=True)
        if not assembled:
            result = uncached()
        try:
            self.prune(force=not assembled)
        except (OSError, sqlite3.Error, ValueError):
            log.debug("deferred cache maintenance", exc_info=True)
        return result

    def store_operation(self, store, callback, *, cached=None):
        """Serialize discovery and leaf fills through the existing generation ledger."""
        from blosc2.msgpack_utils import msgpack_packb

        from caterva2.services import remote_store

        def execute(path=None, operation=callback):
            with store.open(path) as runtime:
                with runtime._owner.lock:
                    result = operation(runtime)
                    payload = runtime.cache_bytes if path is not None else 0
                if path is not None:
                    manifest = runtime._owner.disk.load()
                    remote_store.validate_manifest(manifest)
                else:
                    nodes = {
                        key: (kind, value if kind == "unsupported" else None)
                        for key, (kind, value) in runtime._owner.nodes.items()
                    }
                    remote_store.validate_manifest(dict(store.manifest, nodes=nodes))
                    payload = 0
                return result, payload

        if store.cache_policy != "disk":
            return execute()[0]
        rel = self.q.relative(store.path)
        spec = hashlib.sha256(msgpack_packb(store.manifest)).hexdigest()
        sig = store.carrier_generation
        assembled = False
        try:
            with file_lock(self.q.control / "initialize.lock", shared=True), self.q.lock(rel):
                if signature(store.path) != sig:
                    raise StorageBusy("RemoteStore carrier changed after inspection")
                if hashlib.sha256(msgpack_packb(remote_store.inspect(store.path))).hexdigest() != spec:
                    raise StorageBusy("RemoteStore descriptor changed after inspection")
                with self.q.connect() as db:
                    row = db.execute(
                        "SELECT o.active_generation,g.relpath,o.carrier_generation,o.spec_hash "
                        "FROM remote_objects o JOIN remote_generations g ON g.generation_id=o.active_generation "
                        "WHERE o.path=? AND g.state='active'",
                        (rel,),
                    ).fetchone()
                if row and (row[2:] != (json.dumps(sig), spec) or not self.path(row[1]).exists()):
                    self.retire_path(rel)
                    row = None
                if row:
                    gid, private = row[:2]
                    path = self.path(private)
                else:
                    oid, gid = uuid.uuid4().hex, uuid.uuid4().hex
                    path = self.root / oid / gid
                    now = time.time()
                    with self.q.transaction() as db:
                        db.execute(
                            "INSERT INTO remote_objects VALUES(?,?,?,?,?,?,?,?)",
                            (
                                oid,
                                rel,
                                json.dumps(sig),
                                spec,
                                json.dumps(store.manifest["source"]),
                                gid,
                                0,
                                now,
                            ),
                        )
                        db.execute(
                            "INSERT INTO remote_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                gid,
                                oid,
                                path.relative_to(self.q.root).as_posix(),
                                "active",
                                spec,
                                json.dumps(store.manifest["source"]),
                                store.max_cache_bytes,
                                0,
                                0,
                                0,
                                now,
                                now,
                                "store",
                            ),
                        )
                with self.guard(gid):
                    with self.q.connect() as db:
                        if db.execute(
                            "SELECT 1 FROM remote_operations WHERE generation_id=?", (gid,)
                        ).fetchone():
                            raise StorageBusy("Store generation needs recovery")
                    if row and cached is not None:
                        self._intent(gid, "probe")
                        (hit, value), payload = execute(path, cached)
                        sync_tree(path)
                        self._finish(gid, path, payload)
                        if hit:
                            return value
                    # ponytail: coarse soft admission, as for array fills; exact mutation reports can refine it.
                    if not self._admit(gid, min(store.max_cache_bytes or (1 << 20), 1 << 20) + 65536):
                        raise QuotaExceeded("store cache retention refused")
                    path.mkdir(parents=True, exist_ok=True)
                    result, payload = execute(path)
                    assembled = True
                    sync_tree(path)
                    self._finish(gid, path, payload)
                    if store.manifest["caches"]:
                        self._intent(gid, "coldify")
                        cold = io.BytesIO()
                        remote_store.cold_export(store.manifest, cold)
                        new_sig = self.q.publish_locked(
                            rel, cold.getvalue(), expected=sig, preserve_remote=True
                        )
                        store.manifest = dict(store.manifest, caches=[])
                        store.carrier_generation = new_sig
                        spec = hashlib.sha256(msgpack_packb(store.manifest)).hexdigest()
                        with self.q.transaction() as db:
                            db.execute(
                                "UPDATE remote_objects SET carrier_generation=?,spec_hash=? WHERE active_generation=?",
                                (json.dumps(new_sig), spec, gid),
                            )
                            db.execute(
                                "UPDATE remote_generations SET spec_hash=? WHERE generation_id=?",
                                (spec, gid),
                            )
                            db.execute("DELETE FROM remote_operations WHERE generation_id=?", (gid,))
        except (OSError, sqlite3.Error, QuotaExceeded):
            log.debug("store retention unavailable", exc_info=True)
        if not assembled:
            result = execute()[0]
        try:
            self.prune(force=not assembled)
        except (OSError, sqlite3.Error, ValueError):
            log.debug("deferred store maintenance", exc_info=True)
        return result

    def export(self, proxy):
        """Return an immutable warm artifact and its response-lifetime cleanup."""
        from caterva2.services import remote_proxy

        opid = uuid.uuid4().hex
        folder = self.q.control / "exports"
        if folder.is_symlink():
            raise ValueError("export directory cannot be a symlink")
        folder.mkdir(mode=0o700, exist_ok=True)
        destination = folder / f"{opid}.b2nd"
        owner = file_lock(self.q.control / f"export-{opid}.lock")
        owner.__enter__()

        def cleanup():
            try:
                destination.unlink(missing_ok=True)
                sync_directory(folder)
                with self.q.transaction() as db:
                    db.execute("DELETE FROM remote_work WHERE id=?", (opid,))
            finally:
                owner.__exit__(None, None, None)

        try:
            rel = self.q.relative(proxy.path)
            with file_lock(self.q.control / "initialize.lock", shared=True), self.q.lock(rel):
                if proxy.cache_policy != "disk" or proxy.src.stamp is None:
                    raise ValueError("warm sparse export requires a stamped DISK source")
                gid, path = self._bind(proxy, rel)
                with self.guard(gid):
                    # Reserve the entire available work budget for a single export.
                    # This conservative rollout policy prevents concurrent exports
                    # from overcommitting an underestimated sparse serializer.
                    estimate = measure(path)[0] + signature(proxy.path)[2]
                    free = shutil.disk_usage(folder).free
                    with self.q.transaction() as db:
                        busy = db.execute("SELECT coalesce(sum(working),0) FROM operations").fetchone()[0]
                        _, _, work = self.totals(db)
                        budget = db.execute("SELECT work_bytes FROM account").fetchone()[0]
                        if busy + work:
                            raise QuotaExceeded("export staging budget is in use")
                        if estimate > budget or free < budget + (1 << 30):
                            raise QuotaExceeded("insufficient export headroom")
                        db.execute(
                            "INSERT INTO remote_work VALUES(?,?,?,?,?)",
                            (
                                opid,
                                "export",
                                budget,
                                destination.relative_to(self.q.root).as_posix(),
                                time.time(),
                            ),
                        )
                    runtime = self._attach(proxy, path)
                    try:
                        with destination.open("xb"):
                            pass
                        runtime.save(destination, mode="w")
                    finally:
                        del runtime
                    exported = remote_proxy.raw_carrier(destination, mode="a")
                    public = remote_proxy.raw_carrier(proxy.path)
                    from blosc2.proxy import _RESERVED_VLMETA

                    for key in public.schunk.vlmeta:
                        if key not in _RESERVED_VLMETA and key != "b2o":
                            exported.schunk.vlmeta[key] = public.schunk.vlmeta[key]
                    del public, exported
                    if destination.stat().st_size > budget:
                        raise QuotaExceeded("export exceeded its staging budget")
                    digest = hashlib.sha256()
                    with destination.open("rb") as stream:
                        os.fsync(stream.fileno())
                        for block in iter(lambda: stream.read(1 << 20), b""):
                            digest.update(block)
                    sync_directory(folder)
            return destination, digest.hexdigest(), cleanup
        except BaseException:
            cleanup()
            raise

    def export_store(self, store, *, include_cache=True):
        """Reserve response-lifetime staging for a portable store snapshot."""
        from caterva2.services import remote_store

        opid = uuid.uuid4().hex
        folder = self.q.control / "exports"
        if folder.is_symlink():
            raise ValueError("export directory cannot be a symlink")
        folder.mkdir(mode=0o700, exist_ok=True)
        destination = folder / f"{opid}.b2z"
        owner = file_lock(self.q.control / f"export-{opid}.lock")
        owner.__enter__()

        def cleanup():
            try:
                destination.unlink(missing_ok=True)
                sync_directory(folder)
                with self.q.transaction() as db:
                    db.execute("DELETE FROM remote_work WHERE id=?", (opid,))
            finally:
                owner.__exit__(None, None, None)

        try:
            with self.q.transaction() as db:
                busy = db.execute("SELECT coalesce(sum(working),0) FROM operations").fetchone()[0]
                _, _, work = self.totals(db)
                budget = db.execute("SELECT work_bytes FROM account").fetchone()[0]
                if busy + work:
                    raise QuotaExceeded("export staging budget is in use")
                if shutil.disk_usage(folder).free < budget:
                    raise QuotaExceeded("insufficient store export headroom")
                db.execute(
                    "INSERT INTO remote_work VALUES(?,?,?,?,?)",
                    (opid, "export", budget, destination.relative_to(self.q.root).as_posix(), time.time()),
                )
            if include_cache:
                self.store_operation(
                    store,
                    lambda runtime: runtime.save(destination, mutable=store.manifest.get("mutable", False)),
                )
            else:
                remote_store.cold_export(store.manifest, destination)
            if destination.stat().st_size > budget:
                raise QuotaExceeded("store export exceeded its staging budget")
            digest = hashlib.sha256()
            with destination.open("rb") as stream:
                os.fsync(stream.fileno())
                for block in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(block)
            return destination, digest.hexdigest(), cleanup
        except BaseException:
            cleanup()
            raise

    def prune(self, *, force=False):
        """Bounded whole-generation cleanup plus authorized-free chunk eviction."""
        try:
            with (
                file_lock(self.q.control / "initialize.lock", shared=True),
                file_lock(self.q.control / "remote-prune.lock", blocking=False),
            ):
                self.cleanup(max_generations=4)
                usage = self.q.usage()
                if not usage["quota"] or (
                    usage["used"] <= usage["quota"] and not force and not usage["cache_fill_suspended"]
                ):
                    return
                with self.q.connect() as db:
                    rows = db.execute(
                        "SELECT g.generation_id,g.relpath,g.payload_bytes,o.path,g.kind,g.source_stamp "
                        "FROM remote_generations g JOIN remote_objects o ON o.object_id=g.object_id "
                        "WHERE g.state='active' AND NOT EXISTS (SELECT 1 FROM remote_operations p "
                        "WHERE p.generation_id=g.generation_id) ORDER BY touched LIMIT 4"
                    ).fetchall()
                remaining = 64
                for gid, private, payload, rel, kind, source in rows:
                    try:
                        with self.q.lock(rel, blocking=False), self.guard(gid, blocking=False):
                            with self.q.connect() as db:
                                active = db.execute(
                                    "SELECT 1 FROM remote_generations WHERE generation_id=? "
                                    "AND state='active'",
                                    (gid,),
                                ).fetchone()
                            if not active:
                                continue
                            usage = self.q.usage()
                            needed = max(0, usage["used"] - int(usage["quota"] * 0.9))
                            if not needed or not remaining:
                                break
                            path = self.path(private)
                            if not path.exists():
                                continue
                            self._intent(gid, "prune")
                            if kind == "store":
                                evicted, payload = blosc2.RemoteStore.trim_sparse_cache(
                                    path,
                                    json.loads(source),
                                    max(0, payload - needed),
                                    max_chunks=remaining,
                                )
                            else:
                                evicted, payload = blosc2.RemoteArray.trim_sparse_cache(
                                    path, max(0, payload - needed), max_chunks=remaining
                                )
                            remaining -= len(evicted)
                            sync_tree(path)
                            self._finish(gid, path, payload)
                    except (OSError, sqlite3.Error, StorageBusy, ValueError):
                        log.debug("deferred sparse pruning", exc_info=True)
        except StorageBusy:
            pass

    def cleanup(self, *, max_generations=4):
        with self.q.connect() as db:
            rows = db.execute(
                "SELECT generation_id,object_id,relpath FROM remote_generations "
                "WHERE state IN ('retired','trash') LIMIT ?",
                (max_generations,),
            ).fetchall()
        for gid, oid, rel in rows:
            try:
                with self.guard(gid, blocking=False):
                    path = self.path(rel)
                    trash = self.trash / gid
                    with self.q.transaction() as db:
                        db.execute(
                            "UPDATE remote_generations SET state='trash' WHERE generation_id=?", (gid,)
                        )
                    if path.exists() and path != trash:
                        os.replace(path, trash)
                        sync_directory(path.parent)
                        sync_directory(self.trash)
                    with self.q.transaction() as db:
                        db.execute(
                            "UPDATE remote_generations SET relpath=? WHERE generation_id=?",
                            (trash.relative_to(self.q.root).as_posix(), gid),
                        )
                    if trash.exists():
                        shutil.rmtree(trash)
                        sync_directory(self.trash)
                    parent = self.root / oid
                    with contextlib.suppress(FileNotFoundError, OSError):
                        parent.rmdir()
                    parent_exists = parent.exists()
                    parent_charge = allocated(parent) if parent_exists else 0
                    with self.q.transaction() as db:
                        db.execute("DELETE FROM remote_operations WHERE generation_id=?", (gid,))
                        db.execute("DELETE FROM remote_generations WHERE generation_id=?", (gid,))
                        db.execute(
                            "UPDATE remote_objects SET parent_charge_bytes=? WHERE object_id=?",
                            (parent_charge, oid),
                        )
                        if not parent_exists:
                            db.execute(
                                "DELETE FROM remote_objects WHERE object_id=? AND path IS NULL AND parent_charge_bytes=0 AND NOT EXISTS "
                                "(SELECT 1 FROM remote_generations WHERE object_id=?)",
                                (oid, oid),
                            )
            except (OSError, StorageBusy):
                log.debug("deferred sparse cleanup", exc_info=True)
        self._suspension()

    def cleanup_empty_objects(self):
        with self.q.connect() as db:
            rows = db.execute(
                "SELECT object_id FROM remote_objects WHERE path IS NULL AND NOT EXISTS "
                "(SELECT 1 FROM remote_generations g WHERE g.object_id=remote_objects.object_id) LIMIT 64"
            ).fetchall()
        for (oid,) in rows:
            if not re.fullmatch("[0-9a-f]{32}", oid):
                raise ValueError("invalid object ID")
            parent = self.root / oid
            if parent.is_symlink():
                raise ValueError("invalid object directory")
            with contextlib.suppress(OSError):
                parent.rmdir()
            parent_exists = parent.exists()
            charge = allocated(parent) if parent_exists else 0
            with self.q.transaction() as db:
                db.execute(
                    "UPDATE remote_objects SET parent_charge_bytes=? WHERE object_id=?", (charge, oid)
                )
                if not parent_exists:
                    db.execute("DELETE FROM remote_objects WHERE object_id=?", (oid,))

    def recover(self):
        """Conservatively retire interrupted operations; never resolve a URL."""
        with self.q.connect() as db:
            rows = db.execute(
                "SELECT o.path,o.object_id,o.carrier_generation,g.generation_id,g.relpath "
                "FROM remote_objects o JOIN remote_generations g ON g.object_id=o.object_id"
            ).fetchall()
        for rel, oid, expected, gid, private in rows:
            try:
                guard = self.q.lock(rel, blocking=False) if rel else contextlib.nullcontext()
                with guard, self.guard(gid, blocking=False):
                    with self.q.connect() as db:
                        pending = db.execute(
                            "SELECT 1 FROM remote_operations WHERE generation_id=?", (gid,)
                        ).fetchone()
                    path = self.path(private)
                    if (
                        pending
                        or (rel and json.dumps(signature(self.q.root / rel)) != expected)
                        or not path.exists()
                    ):
                        with self.q.transaction() as db:
                            db.execute(
                                "UPDATE remote_generations SET state='retired' WHERE generation_id=?", (gid,)
                            )
                            db.execute(
                                "UPDATE remote_objects SET path=NULL,active_generation=NULL WHERE object_id=?",
                                (oid,),
                            )
                    if not path.exists() and (self.trash / gid).exists():
                        path = self.trash / gid
                    charge, count = measure(path)
                    parent = self.root / oid
                    parent_charge = allocated(parent) if parent.exists() else 0
                    with self.q.transaction() as db:
                        db.execute(
                            "UPDATE remote_objects SET parent_charge_bytes=? WHERE object_id=?",
                            (parent_charge, oid),
                        )
                        db.execute(
                            "UPDATE remote_generations SET charge_bytes=?,inode_count=? WHERE generation_id=?",
                            (charge, count, gid),
                        )
            except StorageBusy:
                continue
        self.cleanup(max_generations=64)
        self.cleanup_empty_objects()
        self.recover_exports()
        self.reconcile_orphans()

    def recover_exports(self):
        with self.q.connect() as db:
            rows = db.execute("SELECT id,relpath FROM remote_work").fetchall()
        for opid, rel in rows:
            if not re.fullmatch("[0-9a-f]{32}", opid) or rel not in {
                f".storage/exports/{opid}.b2nd",
                f".storage/exports/{opid}.b2z",
            }:
                raise ValueError("invalid export registry path")
            try:
                with file_lock(self.q.control / f"export-{opid}.lock", blocking=False):
                    path = self.q.root / rel
                    if path.parent.is_symlink():
                        raise ValueError("invalid export parent")
                    path.unlink(missing_ok=True)
                    if path.parent.exists():
                        sync_directory(path.parent)
                    with self.q.transaction() as db:
                        db.execute("DELETE FROM remote_work WHERE id=?", (opid,))
            except (StorageBusy, OSError):
                continue

    def reconcile_orphans(self):
        # Run under the initialization barrier exclusively: no builder can be
        # between row registration and directory creation during this inventory.
        with self.q.connect() as db:
            known = {row[0] for row in db.execute("SELECT relpath FROM remote_generations")}
            objects = {row[0] for row in db.execute("SELECT object_id FROM remote_objects")}
        for parent in self.root.iterdir():
            if parent.is_symlink() or not parent.is_dir():
                continue
            if parent.name != ".trash" and not re.fullmatch("[0-9a-f]{32}", parent.name):
                continue
            for path in parent.iterdir():
                rel = path.relative_to(self.q.root).as_posix()
                if rel in known or not re.fullmatch("[0-9a-f]{32}", path.name):
                    continue
                size, count = measure(path)
                with self.q.transaction() as db:
                    db.execute(
                        "INSERT INTO remote_orphans VALUES(?,?,?,?,?) ON CONFLICT(relpath) DO UPDATE SET "
                        "charge_bytes=excluded.charge_bytes,inode_count=excluded.inode_count,updated=excluded.updated",
                        (uuid.uuid4().hex, rel, size, count, time.time()),
                    )
                try:
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                    else:
                        shutil.rmtree(path)
                    sync_directory(parent)
                except OSError:
                    continue
                with self.q.transaction() as db:
                    db.execute("DELETE FROM remote_orphans WHERE relpath=?", (rel,))
            if parent.name != ".trash" and parent.name not in objects:
                # An orphan generation's parent also consumes space/inodes.
                # Keep its own charge until all children and the parent are gone.
                with contextlib.suppress(OSError):
                    parent.rmdir()
                exists = parent.exists()
                charge = allocated(parent) if exists else 0
                rel = parent.relative_to(self.q.root).as_posix()
                with self.q.transaction() as db:
                    if exists:
                        db.execute(
                            "INSERT INTO remote_orphans VALUES(?,?,?,?,?) ON CONFLICT(relpath) DO UPDATE SET "
                            "charge_bytes=excluded.charge_bytes,updated=excluded.updated",
                            (uuid.uuid4().hex, rel, charge, 1, time.time()),
                        )
                    else:
                        db.execute("DELETE FROM remote_orphans WHERE relpath=?", (rel,))
        self._suspension()

    def maintain(self):
        try:
            with file_lock(self.q.control / "initialize.lock", blocking=False):
                self.recover()
        except StorageBusy:
            pass
        self.prune()
