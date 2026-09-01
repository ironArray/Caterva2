"""Remote-cache pool manager: per-chunk LRU under a byte budget.

Mechanism (verified, see design doc): evicting a chunk = replacing it with an
UNINIT special chunk via update_chunk; the sparse frame reclaims the chunk
file space immediately and the Proxy refetches on next access.
"""

import asyncio
import contextlib
import logging
import os
import pathlib
import tempfile
import time
import weakref

import blosc2
import numpy as np

logger = logging.getLogger("peercache")

HIGH = 1.0  # start evicting above budget
LOW = 0.8  # evict down to this fraction of budget

# nchunk sentinel for whole-file (.b2) entries in the candidate list: plain
# peer files are cached as one .b2 frame and evicted whole (the refetch unit
# is the whole download), unlike the per-chunk dataset caches.
WHOLE_FILE = -1

# Per-cache locks serializing fetch->read->touch vs eviction, one per cache
# frame (keyed by cache path). Frame integrity itself (no torn reads, no
# clobbered index rewrites under concurrent mutation) is delegated to
# blosc2's own opt-in locking (locking=True on every peer-cache handle,
# below) instead of being serialized here. The asyncio locks below exist for
# a different reason: the eviction policy sorts untouched chunks as oldest
# (touch() only runs after the read), so fetch->read->touch needs to be one
# atomic unit against a concurrent eviction of the same cache, or the evictor
# can reclaim a chunk between its fetch and its touch and the read silently
# returns UNINIT zeros. Per-cache (rather than one global lock) means
# fetches of different datasets no longer serialize against each other.
_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
pool_dir: pathlib.Path | None = None  # set at startup
budget: int | None = None  # bytes, set at startup
peer_quotas: dict[str, int] = {}  # peer name -> bytes for pool_dir/<name>, set at startup


def cache_lock(cpath) -> asyncio.Lock:
    """Serialize fetch->read->touch vs eviction, per cache frame.

    Held weakly, so the table holds locks that are in use and nothing else. The
    pool is bounded in bytes and not in keys -- a peer catalog runs to 10 000
    entries, several peers to as many again -- and a strong table grows one
    entry per distinct key ever touched, including keys whose cache files
    eviction deleted long ago, for as long as the server runs.

    Weak is safe because every caller takes the lock in an `async with` on this
    call's result: whoever holds it, and whoever waits on it, is a strong
    reference to it for as long as that matters. A lock nobody holds has no
    state worth keeping.
    """
    lock = _locks.get(str(cpath))
    if lock is None:
        # Bound to a name first: the table's own reference is weak, so an
        # unnamed lock could be collected before it is returned
        lock = asyncio.Lock()
        lock = _locks.setdefault(str(cpath), lock)
    return lock


def _atime_file(cache_urlpath):
    return pathlib.Path(str(cache_urlpath) + ".atime.npy")


def _load_atimes(af):
    """The atime array at `af`, or None if missing/unreadable (a truncated
    file from a concurrent/crashed writer must not break the fetch path)."""
    try:
        return np.load(af) if af.exists() else None
    except Exception:
        return None


def touch(proxy, slice_):
    """Stamp access times for the chunks a slice touches."""
    cache = proxy._cache
    nchunks = cache.schunk.nchunks
    af = _atime_file(cache.schunk.urlpath)
    atimes = _load_atimes(af)
    if atimes is None or len(atimes) != nchunks:
        atimes = np.zeros(nchunks)
    touched = range(nchunks) if slice_ in (None, ()) else blosc2.get_slice_nchunks(cache, slice_)
    atimes[list(touched)] = time.time()
    _stamp_atimes(af, atimes)


def touch_file(entry):
    """Stamp the access time of a whole-file (.b2) entry: a 1-element atime
    array in the same sidecar format the chunk caches use."""
    _stamp_atimes(_atime_file(entry), np.array([time.time()]))


def _stamp_atimes(af, atimes):
    # Atomic replace so concurrent readers never see a partially-written
    # file. The tmp name must be unique per call: touch runs via
    # asyncio.to_thread, and a cancelled await (client disconnect) leaves
    # the thread running after the cache lock is released — a fixed tmp
    # name then collides with the next request's touch (os.replace ->
    # FileNotFoundError, seen under load).
    fd, tmp = tempfile.mkstemp(dir=af.parent, prefix=af.name, suffix=".tmp")
    with os.fdopen(fd, "wb") as f:
        np.save(f, atimes)
    os.replace(tmp, af)


def _usage(scope=None):
    """Total bytes under `scope` (default: the whole pool). The scan is
    lock-free, and files can vanish between listing and stat (touch()'s
    atomic-replace tmp files) -- tolerate that instead of 500ing the fetch
    that triggered ensure_budget."""
    total = 0
    for f in (scope or pool_dir).rglob("*"):
        with contextlib.suppress(OSError):
            if f.is_file():
                total += f.stat().st_size
    return total


def _pool_usage():
    """One walk of the pool: `(total bytes, {top-level subdirectory: bytes})`.

    The scopes a quota is checked against are the pool and its per-peer
    subdirectories, and `_usage(pool_dir)` already stats every file that each
    `_usage(pool_dir / <name>)` does: with N peers configured, walking once
    here is N+1 full recursive scans of the pool saved on every fetch.

    Lock-free and tolerant of files vanishing mid-walk, as `_usage` is.
    """
    total = 0
    per_peer: dict[str, int] = {}
    for f in pool_dir.rglob("*"):
        with contextlib.suppress(OSError):
            if f.is_file():
                size = f.stat().st_size
                total += size
                parts = f.relative_to(pool_dir).parts
                if len(parts) > 1:
                    per_peer[parts[0]] = per_peer.get(parts[0], 0) + size
    return total, per_peer


def _uninit_chunk(schunk, nchunk):
    """A compressed UNINIT special chunk sized for `nchunk` (handles the
    trailing partial chunk)."""
    # ponytail: full-chunksize special works for NDArray caches because all
    # NDArray chunks are padded; revisit if SChunk (1D, unpadded) caches appear.
    tmp = blosc2.SChunk(chunksize=schunk.chunksize, cparams=blosc2.CParams(typesize=schunk.typesize))
    tmp.fill_special(schunk.chunksize // schunk.typesize, blosc2.SpecialValue.UNINIT)
    return tmp.get_chunk(0)


async def ensure_budget():
    """Evict least-recently-used chunks until usage fits the quotas: first
    each configured per-peer quota over its own pool_dir/<name> subtree,
    then the pool-wide budget over everything. Called after each remote
    fetch.

    Candidate gathering is lock-free (read-only; blosc2's own handle locking
    keeps it safe against concurrent mutation). Eviction itself is grouped by
    cache and run under that cache's lock, one cache at a time -- so it never
    blocks a fetch/eviction of a *different* cache, only the same one.

    The quotas are read first, and the candidates gathered only for a scope
    that is actually over one. Gathering opens every cached frame in the pool
    and walks its chunks, which is the expensive part of this and was being
    paid on every single fetch -- against a cache with room to spare, for a
    list nothing then evicted from.

    Reading them costs one walk of the pool for all of them, and that measure
    is what the eviction pass then works from: a per-scope `_usage` here and
    another inside `_evict_to_quota` walked the same files two or three times
    over on the very fetches this is meant to be cheap on."""
    if pool_dir is None or (budget is None and not peer_quotas):
        return
    total, per_peer = await asyncio.to_thread(_pool_usage)
    over = [
        (pool_dir / name, quota, per_peer.get(name, 0))
        for name, quota in peer_quotas.items()
        if quota is not None and per_peer.get(name, 0) > HIGH * quota
    ]
    if budget is not None and total > HIGH * budget:
        over.append((pool_dir, budget, total))
    if not over:
        return
    candidates = await asyncio.to_thread(_gather_candidates)
    for scope, quota, usage in over:
        await _evict_to_quota(candidates, scope, quota, usage)


async def _evict_to_quota(candidates, scope, quota, usage):
    """One LRU eviction pass over the caches under `scope` (a directory), down
    to LOW * quota. `usage` is `scope`'s measured size, from the caller's walk
    of the pool; the pass is a no-op unless it exceeds HIGH * quota."""
    if quota is None or usage <= HIGH * quota:
        return
    target = LOW * quota
    by_cache: dict[str, list[tuple[float, int]]] = {}
    for at, cpath, nchunk in candidates:
        if pathlib.Path(cpath).is_relative_to(scope):
            by_cache.setdefault(cpath, []).append((at, nchunk))
    for cpath, chunks in by_cache.items():
        if usage <= target:
            break
        async with cache_lock(cpath):
            if chunks[0][1] == WHOLE_FILE:
                usage = await asyncio.to_thread(_evict_whole_file, cpath, chunks[0][0], usage, scope)
            else:
                usage = await asyncio.to_thread(_evict_from_cache, cpath, chunks, target, usage, scope)


def _gather_candidates():
    """Read-only scan across the whole pool: (atime, cache_path, nchunk) for
    every filled chunk, to be sorted and grouped by the caller. No lock held
    -- blosc2's own locking (every peer-cache handle opens with locking=True)
    makes concurrent reads safe against mutation elsewhere in the pool."""
    candidates = []
    for cdir in pool_dir.glob("*/*.b2nd"):
        atimes = _load_atimes(_atime_file(cdir))
        try:
            arr = blosc2.open(str(cdir), mode="a", locking=True)
        except Exception:
            continue  # corrupt cache: skip (never crash the fetch path)
        sc = getattr(arr, "schunk", arr)
        for info in sc.iterchunks_info():
            if info.special == blosc2.SpecialValue.NOT_SPECIAL:
                at = atimes[info.nchunk] if atimes is not None and info.nchunk < len(atimes) else 0.0
                candidates.append((at, str(cdir), info.nchunk))
    # Whole-file (.b2) entries: one candidate per file, competing in the same
    # LRU order as dataset chunks. (*.b2 never matches the *.b2nd caches, nor
    # the .b2.json/.b2.atime.npy sidecars.)
    for f in pool_dir.glob("*/*.b2"):
        atimes = _load_atimes(_atime_file(f))
        at = float(atimes[0]) if atimes is not None and len(atimes) else 0.0
        candidates.append((at, str(f), WHOLE_FILE))
    candidates.sort()  # oldest first
    return candidates


def _evict_whole_file(cpath, at, usage, scope):
    """Evict a whole-file (.b2) entry: unlink body + sidecars. Called with
    the entry's cache_lock held. A candidate re-served (fresh atime) or
    already removed since gathering is skipped, mirroring _evict_from_cache's
    staleness checks."""
    p = pathlib.Path(cpath)
    live = _load_atimes(_atime_file(p))
    live_at = float(live[0]) if live is not None and len(live) else 0.0
    if not p.exists() or live_at != at:
        return usage
    for f in (p, pathlib.Path(str(p) + ".json"), _atime_file(p)):
        f.unlink(missing_ok=True)
    new = _usage(scope)
    logger.info("evicted file entry %s (freed %d bytes)", cpath, usage - new)
    return new


def _evict_from_cache(cpath, chunks, target, usage, scope):
    """Evict `chunks` (a cache's candidates, as (atime, nchunk) pairs, oldest
    first) until `scope` usage drops to `target` or the candidates run out.
    Called with `cpath`'s cache_lock held.

    A candidate may be stale by the time we get here: already evicted by a
    concurrent eviction pass, or re-touched (fresh access) since it was
    gathered. Both are re-checked against the live frame/atimes and skipped
    -- harmless, and avoids evicting a chunk that was just fetched again
    while we waited for the lock."""
    if usage <= target:
        return usage
    arr = blosc2.open(cpath, mode="a", locking=True)
    sc = getattr(arr, "schunk", arr)
    specials = {info.nchunk: info.special for info in sc.iterchunks_info()}
    live_atimes = _load_atimes(_atime_file(cpath))
    for at, nchunk in chunks:
        if usage <= target:
            break
        if specials.get(nchunk) != blosc2.SpecialValue.NOT_SPECIAL:
            continue  # already evicted since we gathered candidates
        if live_atimes is not None and nchunk < len(live_atimes) and live_atimes[nchunk] != at:
            continue  # re-touched (refetched) since we gathered candidates
        before = _usage(scope)  # ponytail: O(scope) stat per eviction; batch later
        sc.update_chunk(nchunk, _uninit_chunk(sc, nchunk))
        usage = _usage(scope)
        logger.info("evicted chunk %d of %s (freed %d bytes)", nchunk, cpath, before - usage)
    return usage
