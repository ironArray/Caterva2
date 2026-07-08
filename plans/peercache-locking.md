# Peer cache: replace the global io_lock with per-cache locks + blosc2 frame locking

**Status (2026-07-08): implemented.** `locking=True` on every peer-cache
handle, the `cache_lock()` per-cache registry, the call-site updates, and the
tests below have all landed. The one item still open is bumping
`pyproject.toml`'s `blosc2>=` floor to the first release with `locking`
support — blocked on that python-blosc2 release actually shipping (see the
note at the end of "Changes" §5).

## Context

The peer chunk cache (`c2cache/`) serializes **all** cache IO — open/create,
fetch, read, evict, across every cache in the pool — through one global
`asyncio.Lock` (`peercache.io_lock`, `c2cache/peercache.py:30`). It exists
because sparse-frame handles used to go incoherent under concurrent mutation
("Error while getting the lazychunk"). That root cause is now fixed upstream:

- c-blosc2 re-syncs stale handles automatically (`frame_refresh_if_stale`),
  and offers opt-in cross-process/handle locking via a `.b2lock` sidecar
  (shared for reads, exclusive for mutations, crash-safe, exact staleness
  detection via a generation counter).
- python-blosc2 exposes it as `locking=True` on `blosc2.open()` /
  `SChunk()` / NDArray constructors (see
  `python-blosc2/plans/file-locking.md`). Advisory: **every** handle on a
  frame must enable it.

Goal: relax the global lock to per-cache granularity so concurrent fetches of
*different* datasets no longer serialize, while keeping today's correctness
guarantees.

## Why blosc2 locking alone is NOT enough

blosc2's lock is per *operation*. The endpoints need multi-operation critical
sections: `fetch()` holds the lock across open + `proxy.afetch` + slice read +
`touch()` (`c2cache/provider.py:137`). The eviction policy makes this
load-bearing: chunks with no recorded atime sort as **oldest**
(`peercache.py:103`, `at = 0.0`), and `touch()` only runs *after* the read —
so a just-fetched, not-yet-touched chunk is the evictor's first candidate.
Without a section spanning fetch→read→touch, an eviction landing in between
makes the read silently return UNINIT zeros. So:

- **blosc2 `locking=True`** provides frame-level integrity (no torn reads, no
  clobbered index rewrites) against *any* concurrent handle — worker threads
  today, other OS processes tomorrow — and lets the "corrupt handle" class of
  failures disappear.
- **Per-cache `asyncio.Lock`s** provide the semantic atomicity of
  fetch→read→touch vs evict, scoped to one cache instead of the whole pool.

## Changes

### 1. `locking=True` on every peer-cache frame handle (`c2cache/`)

All five sites (advisory locking: miss one and the frame is unprotected):

- `remote.py:124` — validity-check open in `open_cached_proxy`:
  `blosc2.open(str(cpath), mode="a", locking=True)`
- `remote.py:137-144` — cache creation: `blosc2.empty(..., contiguous=False,
  mode="w", locking=True)`
- `remote.py:244` — `get_cached_only` offline open
- `peercache.py:97` — evictor's candidate-gathering open
- `peercache.py:112` — evictor's eviction open

During implementation, grep `c2cache/` and the tests for any other
`blosc2.open`/`blosc2.empty` touching `pool_dir` paths, and verify that
`blosc2.Proxy` operates through the passed `_cache` handle without re-opening
the urlpath internally (if it re-opens anywhere, that site needs the flag too).

### 2. Per-cache lock registry (`c2cache/peercache.py`)

Replace `io_lock` with (same pattern as the per-path `locks` dict already used
in `caterva2/services/server.py:69`):

```python
_locks: dict[str, asyncio.Lock] = {}


def cache_lock(cpath) -> asyncio.Lock:
    """Serialize fetch->read->touch vs eviction, per cache frame."""
    return _locks.setdefault(str(cpath), asyncio.Lock())
```

Bounded by the number of caches in the pool; never pruned (ponytail: fine).
The key is the cache path, computable up-front via `remote.cache_path(peer_id,
path)` (`remote.py:106`) — callers can take the lock *before* opening.

### 3. Call-site updates (`c2cache/provider.py`)

- `fetch()` online path (`provider.py:137`): `async with io_lock` →
  `async with peercache.cache_lock(cpath)` (compute `cpath` first). Body
  unchanged: open + afetch + read + touch stays one atomic unit per cache.
- `fetch()` offline path (`provider.py:149`) and `open_view()`
  (`provider.py:163`): same substitution.
- `ensure_budget()` stays called **outside** any cache lock (unchanged
  discipline, `provider.py:181`), but see next point for how it locks.

### 4. Evictor restructure (`peercache.py`)

`_evict_sync` currently does the whole pool in one thread call under the
global lock. New shape in `ensure_budget()`:

1. Gather candidates with **no asyncio lock at all** — it's read-only
   (`iterchunks_info`, atime loads) and the blosc2 shared locks make it safe
   against concurrent mutation.
2. Group candidates by cache; for each cache with evictions to do:
   `async with cache_lock(cpath): await asyncio.to_thread(_evict_from_cache,
   cpath, chunks_to_evict)` — re-checking `_usage()` between caches and
   stopping at `LOW * budget` as today.
3. A candidate may be stale by the time its cache is locked (already evicted,
   or refetched with a fresh atime). Evicting an already-special chunk is a
   harmless no-op-shaped write; optionally re-check `iterchunks_info` /
   atimes inside `_evict_from_cache` and skip fresh chunks (cheap and avoids
   evicting a chunk that was re-touched while we waited for the lock).

This means eviction of cache A no longer blocks fetches of caches B..Z — the
actual throughput win.

### 5. Comments, docs, cleanup

- Rewrite the `peercache.py:22-29` comment (and its `ponytail:` note, which
  this change fulfills): per-cache locks for fetch→read→touch atomicity;
  frame integrity delegated to blosc2 `locking=True`.
- Keep (still earning their keep):
  - rebuild-on-corrupt-open (`remote.py:127-134`) — guards against crashes
    mid-creation, which locking does not cover;
  - the silent-zeros guard `slice_fully_cached` (`remote.py:151`) — UNINIT
    read semantics are unchanged;
  - atomic atime replace (`touch()`, `peercache.py:58-62`).
- `pyproject.toml:42`: bump `blosc2>=` to the first release with `locking`
  support (during development, the local editable python-blosc2 serves).
  **Not done yet (2026-07-08): blocked.** No released python-blosc2 has
  `locking` yet (it landed after 4.7.0, in the still-unreleased 4.7.1).
  Caterva2's CI does `pip install -e '.[tests,hdf5]'` — a real PyPI
  resolution, not the local editable checkout — so bumping this floor to an
  unpublished version would break CI immediately. Bump it once python-blosc2
  4.7.1 (or whichever version ships `locking`) is actually released.
- Mention in `plans/c2cache-decoupling.md` that the io_lock section it
  documents is superseded (or leave a pointer to this plan).

## Tests (`caterva2/tests/test_peers.py`)

1. Existing `test_concurrent_requests_under_tiny_quota_dont_crash`
   (`test_peers.py:228`) must keep passing — it's the regression net for
   exactly this concurrency.
2. Strengthen it (or add a sibling): assert the *data* returned by concurrent
   fetches is correct, not just "no 500s" — with per-cache locks the
   fetch→read window must never serve UNINIT zeros for the requested slice.
3. New: concurrent fetches of **two different datasets** overlap in time
   (they no longer share a lock); a coarse timing or interleaving assertion
   is enough — or simply exercise it for correctness and rely on blosc2's own
   suites for the locking mechanics.
4. Sidecar hygiene: after cache creation with locking, `<cache>/.b2lock`
   exists inside the cache dir and disappears with cache removal (rmtree).

## Out of scope

- Multi-process Caterva2 deployments (gunicorn workers sharing a pool dir):
  the blosc2 layer already makes the frames safe for that, but the atime
  sidecars and the per-cache asyncio locks are process-local — going
  multi-process needs its own pass (atime coordination, budget accounting).
  This change removes the frame-integrity blocker, nothing more.
- Batching the O(pool) `_usage()` stat per eviction (`peercache.py:114`,
  existing ponytail note) — unrelated to locking.

## Verification

1. `pytest caterva2/tests/test_peers.py -v` (all peer tests, especially the
   tiny-quota concurrency one, several times in a row — it's the race
   detector).
2. Full suite: `pytest caterva2/tests`.
3. Manual: two-peer setup with a tiny quota, hammer `api/fetch` for several
   datasets concurrently; watch that fetches of different datasets no longer
   serialize (log timestamps) and that responses are correct.
