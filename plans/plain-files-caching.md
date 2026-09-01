# Whole-file caching for peer paths (plain files)

**Status (2026-07-14): IMPLEMENTED** as designed below (c2cache/provider.py
`_download_plain_file` + whole-file candidates in peercache eviction; tests
in caterva2/tests/test_peers.py). Not implemented from the test list:
counting B's hits via an access log/proxy (cache use is proven by the
offline-serve test instead) and an observable single-flight assertion (the
shared `cache_lock` gives it by construction). Streaming tee, range
requests, and whole-`.h5` caching remain out of scope as recorded.

## Today

`C2CacheProvider.download` (shipped 2026-07-11) is a pure streaming relay
of B's `api/download`: verbatim headers, no local copy. N readers at site A
hit B N times for the same report. Everything else about the peer data
plane (datasets) already caches; whole files are the one uncached path.

On B, non-native files (PDF, CSV, TXT, …) are auto-compressed to `.b2`
SChunk frames on ingestion (`compress_file` in `get_abspath`) and
`api/download` returns either decompressed bytes or, under
`Accept-Encoding: blosc2`, the compressed frame. The one exception:
`.h5`/`.hdf5` are treated as blosc2-native (`HDF5_SUFFIXES` is excluded
from the compression step) and stored/served unencoded.

## Design decision: whole-file entries, not sparse frames

The pitch line says "reuses the same sparse chunk cache"; that is true of
the *pool* (location, budget, locking, offline semantics) but NOT of the
entry format. Plain files get **whole-file cache entries**, not sparse
chunk-filled frames, because:

- The access pattern is whole-file (`api/download`); a partial PDF is
  useless, and `slice_fully_cached` discipline would refuse to serve it
  anyway.
- The refetch unit is the whole file (one download), so eviction should be
  whole-file too — evicting single chunks of a file frees bytes without
  reducing future refetch cost. LRU per *file* matches the cost model the
  same way LRU per *chunk* does for arrays.
- It sidesteps the known SChunk gap in `peercache._uninit_chunk` (its
  `ponytail:` comment: full-chunksize UNINIT specials assume padded NDArray
  chunks; an SChunk's trailing chunk is unpadded). No specials needed at
  all: absence of the file IS the uncached state.

So the entry is: a genuine `.b2` blosc2 frame — the same artifact B keeps
on disk for the file — plus a small JSON sidecar of the relay metadata.
Keeping B's own `.b2` format/extension (rather than an opaque download
blob) means the cache is a faithful stand-in for B's storage if B is gone.

## Mechanics

### Cache entry layout

Same pool, same hashing, new suffix (so the eviction scan can tell entries
apart without opening them):

```
pool_dir/<peer>/<sha256(peer_id:key)[:32]>.b2        # blosc2 frame, as B stores it
pool_dir/<peer>/<...>.b2.json                        # sidecar
```

(No clash with dataset entries: those are `<hash>.b2nd`, and `*.b2` globs
don't match them.)

Sidecar: `{"mtime": ..., "content_type": ..., "content_disposition": ...}`
— everything needed to replay the response and to validate freshness,
written atomically (tmp + `os.replace`, the `touch()` pattern). No
`content_encoding` field: every file in scope is stored by B as a `.b2`
frame (see scope guard), so every entry is one too.

### Fill (miss path), in `C2CacheProvider.download`

1. Compute the entry path; take `peercache.cache_lock(entry)` — the same
   asyncio lock gives single-flight: concurrent first downloads of one
   file make one upstream request; waiters find the entry present.
2. `_info(key)` for the remote mtime (already the freshness token of the
   dataset cache). Compare with the sidecar; match ⇒ serve from cache.
3. On miss/stale: GET B's `api/download` with `Accept-Encoding: blosc2`
   (upstream negotiation is now fixed, independent of what A's client
   asked — the compressed frame is both the cheapest transfer and the
   right artifact to store). Stream to a tmp file, fsync, rename, write
   the sidecar. **Fill-then-serve**: the first reader's first byte waits
   for the full download. `ponytail:` known ceiling — a streaming tee
   (serve while filling) is the upgrade path if giant files make this
   annoying; reports/CSVs won't.

### Serve (hit path)

- Client sent `Accept-Encoding: blosc2` ⇒ stream the `.b2` file verbatim
  with `Content-Encoding: blosc2`.
- Otherwise ⇒ decompress (`blosc2.schunk_from_cframe(...)[:]` — same as
  B's own `get_file_content`) and stream.
- Relay `Content-Disposition`/`Content-Type` from the sidecar.

### Offline

`_info` failing with `OFFLINE_ERRORS` + entry present ⇒ serve the cached
copy (mark peer offline as usual, skip the freshness check — same
serve-stale posture as the dataset cache). No entry ⇒ 503. This upgrades
the pitch's offline story to cover plain files.

### Eviction / budget

- `peercache._usage` already counts the new files (it stats everything in
  the pool) — quota pressure is correct on day one.
- `_gather_candidates` learns a second entry kind: for each `*.b2` file,
  one candidate `(atime, path, WHOLE_FILE)` where atime comes from a
  1-element `.atime.npy` sidecar stamped on every serve (same
  `touch`-style atomic write). Eviction of such a candidate = unlink body
  + sidecars, under the entry's `cache_lock`.
- Per-peer quotas and the pool-wide pass apply unchanged (the entries live
  under `pool_dir/<peer>`).

### Scope guard: which paths use this

Only the whole-file `download` endpoint for **non-native plain files** —
the ones B stores as `.b2` (PDF, CSV, TXT, …). Everything else keeps
today's behavior:

- Datasets (`.b2nd`, `.b2z` members, HDF5 leaves) keep the chunk cache
  and their `api/fetch` path untouched; `api/download` of a *dataset*
  keeps the plain relay (downloading whole datasets through A is rare;
  revisit only if customers do it).
- Whole-`.h5`/`.hdf5` container downloads also keep the plain relay,
  **excluded** from the whole-file cache: B stores them unencoded, so a
  cached copy would have to be either a raw `.h5` (a second entry format)
  or an A-side `.b2` recompression (breaking the mirror-of-B principle),
  and pinning multi-GB containers in the pool is the wrong trade anyway.
  Their *leaves* already get chunk-granular sparse caching via
  `api/fetch`, which is the access pattern that matters for big HDF5; if
  customers hammer whole-`.h5` downloads, the honest upgrade is range
  requests, not whole-file caching.

Deciding is one `_info` look plus the key's suffix: dataset infos carry
`shape`/`schunk` (skip), bare `.h5`/`.hdf5` keys are containers (skip),
everything else is a cacheable plain file.

## Out of scope

- Streaming tee (serve while filling) — recorded upgrade path.
- HTTP range requests on cached files.
- Chunk-granular partial caching of `.b2` frames — would need B-side
  `api/chunk` on `.b2` paths and buys nothing for whole-file access.
- Whole-`.h5` download caching (kept as plain relay; see scope guard).
- `api/preview` for peer paths.
- Any change on B.

## Tests

- Two-server e2e: first download fills the cache (one hit on B — count via
  B's access log or a proxy counter), second download serves locally
  (kill B, download again, bytes identical; both encodings).
- Freshness: touch/replace the file on B (mtime bump) ⇒ next download
  refetches.
- Scope guard: a whole-`.h5` download still relays (no `.b2` entry
  appears in the pool) and a dataset download still relays.
- Concurrent first downloads: single upstream fetch (single-flight lock),
  all clients get correct bytes.
- Eviction: tiny quota ⇒ `.b2` entries evicted whole (body + sidecars
  gone), datasets and files compete in one LRU order.
