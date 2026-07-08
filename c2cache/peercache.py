"""Remote-cache pool manager: per-chunk LRU under a byte budget.

Mechanism (verified, see design doc): evicting a chunk = replacing it with an
UNINIT special chunk via update_chunk; the sparse frame reclaims the chunk
file space immediately and the Proxy refetches on next access.
"""

import asyncio
import logging
import os
import pathlib
import time

import blosc2
import numpy as np

logger = logging.getLogger("peercache")

HIGH = 1.0  # start evicting above budget
LOW = 0.8  # evict down to this fraction of budget

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
_locks: dict[str, asyncio.Lock] = {}
pool_dir: pathlib.Path | None = None  # set at startup
budget: int | None = None  # bytes, set at startup


def cache_lock(cpath) -> asyncio.Lock:
    """Serialize fetch->read->touch vs eviction, per cache frame.

    Bounded by the number of caches in the pool; never pruned (ponytail:
    fine, a pool has at most a few hundred entries).
    """
    return _locks.setdefault(str(cpath), asyncio.Lock())


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
    # Atomic replace so concurrent readers never see a partially-written file.
    tmp = af.with_name(af.name + ".tmp")
    with open(tmp, "wb") as f:
        np.save(f, atimes)
    os.replace(tmp, af)


def _usage():
    return sum(f.stat().st_size for f in pool_dir.rglob("*") if f.is_file())


def _uninit_chunk(schunk, nchunk):
    """A compressed UNINIT special chunk sized for `nchunk` (handles the
    trailing partial chunk)."""
    # ponytail: full-chunksize special works for NDArray caches because all
    # NDArray chunks are padded; revisit if SChunk (1D, unpadded) caches appear.
    tmp = blosc2.SChunk(chunksize=schunk.chunksize, cparams=blosc2.CParams(typesize=schunk.typesize))
    tmp.fill_special(schunk.chunksize // schunk.typesize, blosc2.SpecialValue.UNINIT)
    return tmp.get_chunk(0)


async def ensure_budget():
    """Evict least-recently-used chunks across the whole pool until usage is
    below LOW * budget. Called after each remote fetch.

    Candidate gathering is lock-free (read-only; blosc2's own handle locking
    keeps it safe against concurrent mutation). Eviction itself is grouped by
    cache and run under that cache's lock, one cache at a time -- so it never
    blocks a fetch/eviction of a *different* cache, only the same one."""
    if pool_dir is None or budget is None:
        return
    usage, candidates = await asyncio.to_thread(_gather_candidates)
    if usage <= HIGH * budget:
        return
    target = LOW * budget
    by_cache: dict[str, list[tuple[float, int]]] = {}
    for at, cpath, nchunk in candidates:
        by_cache.setdefault(cpath, []).append((at, nchunk))
    for cpath, chunks in by_cache.items():
        if usage <= target:
            break
        async with cache_lock(cpath):
            usage = await asyncio.to_thread(_evict_from_cache, cpath, chunks, target, usage)


def _gather_candidates():
    """Read-only scan across the whole pool: total usage, plus (atime,
    cache_path, nchunk) for every filled chunk, to be sorted and grouped by
    the caller. No lock held -- blosc2's own locking (every peer-cache handle
    opens with locking=True) makes concurrent reads safe against mutation
    elsewhere in the pool."""
    usage = _usage()
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
    candidates.sort()  # oldest first
    return usage, candidates


def _evict_from_cache(cpath, chunks, target, usage):
    """Evict `chunks` (a cache's candidates, as (atime, nchunk) pairs, oldest
    first) until pool usage drops to `target` or the candidates run out.
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
        before = _usage()  # ponytail: O(pool) stat per eviction; batch later
        sc.update_chunk(nchunk, _uninit_chunk(sc, nchunk))
        usage = _usage()
        logger.info("evicted chunk %d of %s (freed %d bytes)", nchunk, cpath, before - usage)
    return usage
