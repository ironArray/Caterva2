"""Local, cross-process admission for immutable file replacements.

The ledger charges regular dataset files in public/shared/personal by st_size.
SQLite, locks, and staging are operational storage; staging has its own budget.
No database transaction waits for an OS lock or performs filesystem/network I/O.
Writers publish complete files with os.replace, so existing readers keep a valid
snapshot. External filesystem writers are not part of this protocol.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import sqlite3
import time
import uuid

ROOTS = {"public", "shared", "personal"}
WORK_BYTES = 1 << 30


class QuotaExceeded(ValueError):
    """The account or staging budget cannot admit this operation."""


class StorageBusy(RuntimeError):
    """A target changed or is already being mutated; retry from a new snapshot."""


def signature(path):
    try:
        st = pathlib.Path(path).stat()
    except FileNotFoundError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


@contextlib.contextmanager
def file_lock(path, *, blocking=True, shared=False):
    """An owner-death-released local lock; never remove its stable lock file."""
    with open(path, "a+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.seek(0, 2) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(lock.fileno(), mode, 1)
            except OSError as exc:
                raise StorageBusy("storage object is busy") from exc
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(lock, mode | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise StorageBusy("storage object is busy") from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class StorageQuota:
    def __init__(self, statedir, quota, *, work_bytes=WORK_BYTES):
        self.input_root = pathlib.Path(statedir).absolute()
        self.root = pathlib.Path(statedir).resolve()
        if not isinstance(quota, int) or quota <= 0:
            raise ValueError("quota must be a positive integer")
        if not isinstance(work_bytes, int) or work_bytes <= 0:
            raise ValueError("work_bytes must be a positive integer")
        self.quota, self.work_bytes = quota, work_bytes
        self.control = self.root / ".storage"
        self.control.mkdir(parents=True, exist_ok=True)
        self.dbpath = self.root / "storage.sqlite"
        with self.startup_guard() as reconcile:
            if not reconcile:
                return  # Active writers already protect a fully initialized ledger.
            with self.connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise RuntimeError("unsupported storage quota schema version")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS objects (
                        path TEXT PRIMARY KEY, size INTEGER NOT NULL CHECK(size >= 0),
                        generation TEXT, cache INTEGER NOT NULL DEFAULT 0,
                        touched REAL NOT NULL DEFAULT 0);
                    CREATE TABLE IF NOT EXISTS operations (
                        id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                        reserved INTEGER NOT NULL CHECK(reserved >= 0),
                        working INTEGER NOT NULL CHECK(working >= 0));
                    CREATE TABLE IF NOT EXISTS account (
                        id INTEGER PRIMARY KEY CHECK(id=1), quota INTEGER NOT NULL,
                        work_bytes INTEGER NOT NULL);
                """)
                initialized = db.execute("SELECT quota, work_bytes FROM account").fetchone()
            if initialized is None:
                # No coordinated writer can start until initialization is complete.
                inventory = list(self.inventory())
                with self.transaction() as db:
                    db.executemany(
                        "INSERT OR REPLACE INTO objects(path,size,generation) VALUES(?,?,?)", inventory
                    )
                    db.execute("INSERT INTO account VALUES(1,?,?)", (quota, work_bytes))
                    db.execute("PRAGMA user_version=1")
            elif initialized != (quota, work_bytes):
                # A configuration change is shared by all workers. Admission
                # reads the ledger value, never a stale worker-local quota.
                with self.transaction() as db:
                    db.execute("UPDATE account SET quota=?, work_bytes=?", (quota, work_bytes))
            # Startup reconciliation covers offline edits and quota re-enablement.
            # All publishes take this barrier shared; no scan runs in a DB txn.
            self.recover()
            inventory = list(self.inventory())
            with self.transaction() as db:
                known = {row[0] for row in db.execute("SELECT path FROM objects")}
                present = set()
                for rel, size, generation in inventory:
                    present.add(rel)
                    db.execute(
                        "INSERT INTO objects(path,size,generation) VALUES(?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET size=excluded.size,"
                        "cache=CASE WHEN objects.generation=excluded.generation THEN objects.cache ELSE 0 END,"
                        "generation=excluded.generation",
                        (rel, size, generation),
                    )
                db.executemany("DELETE FROM objects WHERE path=?", ((rel,) for rel in known - present))

    @contextlib.contextmanager
    def startup_guard(self):
        guard = file_lock(self.control / "initialize.lock", blocking=False)
        try:
            guard.__enter__()
        except StorageBusy:
            try:
                with self.connect() as db:
                    ready = db.execute("SELECT quota,work_bytes FROM account").fetchone()
            except sqlite3.Error:
                ready = None
            if ready is not None:
                if ready != (self.quota, self.work_bytes):
                    raise StorageBusy(
                        "quota configuration change requires quiescent storage writers"
                    ) from None
                yield False
            else:
                with file_lock(self.control / "initialize.lock"):
                    yield True
        else:
            try:
                yield True
            finally:
                guard.__exit__(None, None, None)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.dbpath, timeout=2, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def relative(self, path):
        path = pathlib.Path(path).absolute()
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            try:
                # Permit the configured state directory's own spelling (e.g.
                # macOS /tmp -> /private/tmp), not symlinks inside dataset roots.
                rel = path.relative_to(self.input_root)
            except ValueError:
                raise ValueError("storage target is outside the customer state directory") from None
        if not rel.parts or rel.parts[0] not in ROOTS or ".." in rel.parts:
            raise ValueError("storage target is outside the dataset roots")
        if rel.name.endswith(".b2lock"):
            raise ValueError("lock sidecars are reserved operational files")
        # Reject symlinks, including parents, rather than account a different file.
        current = self.root
        for part in rel.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("symlinks are not supported in quota-managed datasets")
        return rel.as_posix()

    def inventory(self):
        for name in sorted(ROOTS):
            base = self.root / name
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if path.is_symlink():
                    raise ValueError("remove dataset symlinks before enabling storage quota")
                if path.is_file() and not path.name.endswith(".b2lock"):
                    rel = self.relative(path)
                    sig = signature(path)
                    yield rel, sig[2], json.dumps(sig)

    def lock(self, rel, *, blocking=True):
        digest = hashlib.sha256(rel.encode()).hexdigest()
        return file_lock(self.control / f"{digest}.lock", blocking=blocking)

    def snapshot(self, path):
        rel = self.relative(path)
        with self.lock(rel):
            self._recover_path(rel)
            sig = signature(path)
            data = pathlib.Path(path).read_bytes() if sig is not None else None
            return data, sig

    def _recover_path(self, rel):
        """Caller owns the path lock: no previous owner can still publish."""
        with self.connect() as db:
            op = db.execute("SELECT id FROM operations WHERE path=?", (rel,)).fetchone()
        if op is None:
            return
        # Replacement is atomic: target is either the old or complete new file.
        sig = signature(self.root / rel)
        # A dead publisher may have renamed/unlinked without syncing the parent.
        # Make that state durable before releasing its reservation.
        parent = (self.root / rel).parent
        if parent.exists():
            sync_directory(parent)
        (self.control / f"{op[0]}.candidate").unlink(missing_ok=True)
        sync_directory(self.control)
        with self.transaction() as db:
            self._record(db, rel, sig)
            db.execute("DELETE FROM operations WHERE id=?", op)

    def recover(self):
        with self.connect() as db:
            paths = [row[0] for row in db.execute("SELECT path FROM operations")]
        for rel in paths:
            try:
                with self.lock(rel, blocking=False):
                    self._recover_path(rel)
            except StorageBusy:
                continue  # A live owner still holds its reservation; no TTL stealing.

    @staticmethod
    def _record(db, rel, sig, *, cache=False):
        if sig is None:
            db.execute("DELETE FROM objects WHERE path=?", (rel,))
        else:
            db.execute(
                "INSERT INTO objects VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                "size=excluded.size,generation=excluded.generation,cache=excluded.cache,"
                "touched=excluded.touched",
                (rel, sig[2], json.dumps(sig), int(cache), time.time()),
            )

    def usage(self):
        with self.transaction() as db:
            used = db.execute("SELECT coalesce(sum(size),0) FROM objects").fetchone()[0]
            reserved, working = db.execute(
                "SELECT coalesce(sum(reserved),0),coalesce(sum(working),0) FROM operations"
            ).fetchone()
            quota, budget = db.execute("SELECT quota,work_bytes FROM account").fetchone()
        return {"used": used, "reserved": reserved, "working": working, "quota": quota, "work_bytes": budget}

    def publish(self, path, data, *, expected, cache=False, prune=True):
        """Publish exact bytes (None deletes). A stale generation is never overwritten."""
        if data is not None and not isinstance(data, bytes):
            raise TypeError("publish requires serialized bytes")
        rel = self.relative(path)
        for attempt in range(3):
            try:
                return self._publish(rel, data, expected, cache)
            except QuotaExceeded:
                if attempt == 0:
                    # Another worker may have died while this worker stays up.
                    # Never reclaim an operation whose OS lock is still owned.
                    self.recover()
                elif not prune or attempt == 2 or not self.prune(exclude=rel):
                    raise
        raise AssertionError("unreachable admission retry")

    def _publish(self, rel, data, expected, cache):
        path = self.root / rel
        with file_lock(self.control / "initialize.lock", shared=True), self.lock(rel):
            self.relative(path)  # Recheck parent symlinks after taking mutation guards.
            self._recover_path(rel)
            actual = signature(path)
            if actual != expected:
                raise StorageBusy("dataset changed while preparing its replacement")
            oldsize = 0 if actual is None else actual[2]
            size = 0 if data is None else len(data)
            opid = uuid.uuid4().hex
            with self.transaction() as db:
                self._record(db, rel, actual, cache=cache)
                used = db.execute("SELECT coalesce(sum(size),0) FROM objects").fetchone()[0]
                reserved, working = db.execute(
                    "SELECT coalesce(sum(reserved),0),coalesce(sum(working),0) FROM operations"
                ).fetchone()
                quota, budget = db.execute("SELECT quota,work_bytes FROM account").fetchone()
                growth = max(0, size - oldsize)
                if (growth and used + reserved + growth > quota) or working + size > budget:
                    raise QuotaExceeded("customer quota or storage staging budget exceeded")
                db.execute("INSERT INTO operations VALUES(?,?,?,?)", (opid, rel, growth, size))
            candidate = self.control / f"{opid}.candidate"
            # From this point, any failure leaves durable intent for recovery.
            if data is None:
                path.unlink(missing_ok=True)
            else:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                # Persist each new directory entry before publishing into it.
                missing = []
                parent = path.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for directory in reversed(missing):
                    directory.mkdir(exist_ok=True)
                    sync_directory(directory.parent)
                sync_directory(self.control)
                os.replace(candidate, path)
            sync_directory(path.parent)
            sync_directory(self.control)
            sig = signature(path)
            with self.transaction() as db:
                self._record(db, rel, sig, cache=cache)
                db.execute("DELETE FROM operations WHERE id=?", (opid,))
            return sig

    def touch(self, path):
        rel = self.relative(path)
        now = time.time()
        with self.transaction() as db:
            db.execute(
                "UPDATE objects SET touched=?,cache=1 WHERE path=? AND touched<?", (now, rel, now - 10)
            )

    def prune(self, *, exclude, max_victims=4):
        """Cold-export a bounded number of inactive DISK carriers, oldest first.

        Pruning is whole-proxy batching initially. Descriptors and user metadata
        survive; space is credited only after atomic replacement is complete.
        """
        import blosc2

        reclaimed = 0
        with self.connect() as db:
            candidates = db.execute(
                "SELECT path FROM objects WHERE path!=? AND cache=1 ORDER BY touched LIMIT ?",
                (exclude, max_victims),
            ).fetchall()
        for (rel,) in candidates:
            try:
                with self.lock(rel, blocking=False):
                    self._recover_path(rel)
                    path = self.root / rel
                    expected = signature(path)
                    if expected is None or expected[2] > self.work_bytes:
                        continue
                    frame = path.read_bytes()
                # Work on an immutable snapshot, then compare-and-swap on publish.
                carrier = blosc2.ndarray_from_cframe(frame, copy=True)
                payload = carrier.schunk.vlmeta.get("b2o")
                marker = carrier.schunk.meta.get("b2o")
                if marker != {"kind": "remote_proxy", "version": 1}:
                    continue
                if not isinstance(payload, dict) or payload.get("kind") != "remote_proxy":
                    continue
                if set(payload) != {"kind", "version", "source", "cache_policy", "max_cache_bytes"}:
                    continue
                if payload.get("cache_policy") != "disk":
                    continue
                for chunk in range(carrier.schunk.nchunks):
                    carrier.schunk.update_special(chunk, blosc2.SpecialValue.UNINIT)
                for key in tuple(carrier.schunk.vlmeta):
                    if key in blosc2.proxy._RESERVED_VLMETA:
                        del carrier.schunk.vlmeta[key]
                cold = carrier.to_cframe()
                if len(cold) >= len(frame):
                    continue
                self.publish(path, cold, expected=expected, cache=False, prune=False)
                reclaimed += len(frame) - len(cold)
            except (StorageBusy, QuotaExceeded, OSError, RuntimeError, ValueError):
                continue
        return reclaimed
