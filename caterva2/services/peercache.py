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

_lock = asyncio.Lock()  # ponytail: one global evictor lock; fine for MVP
pool_dir: pathlib.Path | None = None  # set at startup
budget: int | None = None  # bytes, set at startup


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
    below LOW * budget. Called after each remote fetch."""
    if pool_dir is None or budget is None:
        return
    async with _lock:
        await asyncio.to_thread(_evict_sync)


def _evict_sync():
    usage = _usage()
    if usage <= HIGH * budget:
        return
    # gather (atime, cache_path, nchunk) for all filled chunks in the pool
    candidates = []
    for cdir in pool_dir.glob("*/*.b2nd"):
        atimes = _load_atimes(_atime_file(cdir))
        try:
            arr = blosc2.open(str(cdir), mode="a")
        except Exception:
            continue  # corrupt cache: skip (never crash the fetch path)
        sc = getattr(arr, "schunk", arr)
        for info in sc.iterchunks_info():
            if info.special == blosc2.SpecialValue.NOT_SPECIAL:
                at = atimes[info.nchunk] if atimes is not None and info.nchunk < len(atimes) else 0.0
                candidates.append((at, str(cdir), info.nchunk))
    candidates.sort()  # oldest first
    target = LOW * budget
    open_caches = {}
    for _, cpath, nchunk in candidates:
        if usage <= target:
            break
        if cpath not in open_caches:
            open_caches[cpath] = blosc2.open(cpath, mode="a")
        sc = getattr(open_caches[cpath], "schunk", open_caches[cpath])
        before = _usage()  # ponytail: O(pool) stat per eviction; batch later
        sc.update_chunk(nchunk, _uninit_chunk(sc, nchunk))
        usage = _usage()
        logger.info("evicted chunk %d of %s (freed %d bytes)", nchunk, cpath, before - usage)
