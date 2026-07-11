# Peer CTable cache: remaining checks & deferred work

Left over after `plans/caterva3-remote-peer-simplified.md` was fully
implemented (2026-07-11). None of these block anything; the plan itself is
closed.

## Checks

- [ ] **Browser click-through of the mount/unmount UX** (plan Verification
  §4, the only step not human-verified). Every htmx response is
  test-covered (`test_peer_container_mount_ui`, `test_ctable_web_view`) and
  the pages were exercised over HTTP, but nobody has clicked the plug icon
  in a real browser: mount a peer TreeStore, browse its leaves, open the
  nested CTable, page through rows, unmount.
- [ ] **blosc2 4.8.1 release sanity** — caterva2's floor is already
  `>=4.8.1` and code comments point at it; when the release ships, a full
  `pytest caterva2/tests` against the released wheel is the whole ritual.
  (Note: `4.8.1.dev0 < 4.8.1` per PEP 440, so don't `pip install -e .`
  caterva2 in the pre-release window without `--no-deps`.)

## Known non-blocking flake

- [x] **One request timeout marks the whole peer offline** → sporadic 503
  on concurrent first-touch fetches (seen ~once per 50 suite runs, e.g.
  `test_ctable_nested_in_tree_fetch`). Fixed (2026-07-11) with the
  retry-once option: `remote.afetch_retry_once` wraps every data-path
  `Proxy.afetch` (NDArray fetch, view prefetch, CTable slice); already
  fetched chunks stay in the sparse cache, so the retry only refetches
  what's missing, and a second failure still marks the peer offline.
  Covered by `test_afetch_retry_once`.

## Deferred by choice (plan "Out of scope")

- Whole-file `api/download` for peer paths (stays 404).
- DictStore recognition server-side (flat `.b2z` stores stay opaque leaves).
- Per-peer `cache_quota` enforcement (parsed, unused).
- Auth, dynamic mounts, batch `api/chunks`.

## Long-term direction (recorded in the plan)

sort_by/group_by/indexing over remote tables = blosc2-side
`RemoteTableStorage` at the `TableStorage` seam (per-column
`Proxy(C2Array)`), needing per-column server addressability. Independent of
the relay cache shipped here — see the plan's "Long-term direction" section.
