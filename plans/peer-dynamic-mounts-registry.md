# Dynamic peer mounts + discovery via a shared registry URL

**Status (2026-07-11): PLANNED** (design only, nothing implemented). Follows
the peer-mount MVP (`plans/caterva3-remote-peer-mounts.md`) and the CTable
relay cache (`plans/caterva3-remote-peer-simplified.md`), whose "out of
scope" list deferred *auth, dynamic mounts* — this plan designs the
dynamic-mounts half (M0–M2), plus the peer-auth half for federating
`@shared` (M3), and states which auth questions remain genuinely open.

## Today

Peers are static `[[server.peer]]` TOML entries, ingested once at startup
(`PeerRegistry.load` → `handshake_all` in `C2CacheProvider.startup`).
Changing the peer set means editing the config and restarting the server.
Everything downstream already handles peers *appearing and disappearing at
runtime* in the liveness sense (offline marking, lazy re-probe, serve-stale
catalog); what's missing is only runtime *membership* changes.

## Design summary (the ladder)

1. **M0 — prep: key the cache pool by `peer_id`, not `peer.name`.**
2. **M1 — config reload:** re-read `[[server.peer]]` on demand, diff into
   the live registry. This alone is "dynamic mounts" for one server.
3. **M2 — discovery: a shared registry URL.** A static, version-controlled
   `peers.toml` document fetched over HTTPS and fed through the same loader.
   The SSoT is a *file*, not a service.
4. **M3 — peer auth: mount a peer's `@shared`** via one service account per
   peer relationship (`root`/`username`/`password_file` in the peer entry,
   visibility gate on A). Designed below; independent of M1/M2.
5. **Deferred (needs user-facing auth on the mount action):** a
   `POST/DELETE /api/peers` mount API and mDNS candidate discovery in the
   panel.
6. **Rejected:** gossip / transitive peer exchange. External roots are
   deliberately non-transitive; transitivity brings mount cycles (the
   self-mount guard only catches direct ones), A-caches-B's-cache-of-C
   amplification, and an unanswerable trust story. If federation ever needs
   to scale, the registry URL covers it without the graph problems.

## M0 — cache pool keyed by peer_id

`RemotePeerAdapter.cache_path` hashes `peer_id:remote_path` for the file
name but places it under `pool_dir / peer.name`. A peer re-added under a
different name (exactly what dynamic membership makes routine) silently
orphans its whole cache subtree even though the content hash still matches.

Change: place caches under `pool_dir / peer.peer_id` (available whenever the
data plane runs — it requires a completed handshake). `peer.name` stays
display/routing-only. Also make the per-peer quota pass resolve
`pool_dir/<peer_id>` (the `peercache.peer_quotas` dict gets keyed by peer_id
too). One-time migration is not worth code: old `pool_dir/<name>` subtrees
become orphans and the pool-wide budget pass already reclaims orphaned bytes
under pressure; note it in the release notes and move on.

## M1 — config reload

Trigger: `SIGHUP` handler *and* a small `POST provider/c2cache/reload`
route on the existing panel router (same code path; the panel gets a
"Reload peers" button). No new config format, no persistence machinery —
the TOML stays the single source of truth, which is also why this needs
**no authentication**: whoever can edit the config could already mount
anything.

Mechanics in `PeerRegistry` (new method `reload(peer_confs)`):

- **Added entries** → `load`-validate, then `_handshake` in a thread (the
  existing parallel-probe pattern).
- **Removed entries** → drop from `self.peers`. In-flight requests hold
  their own `Peer`/adapter references and finish harmlessly; subsequent
  requests 404 at `owns()`. Cache stays on disk (cheap re-add; pool-wide
  budget reclaims it eventually — same policy as unmount-keeps-cache).
- **Changed urlbase** → treat as remove+add (re-handshake; peer_id pinning
  below decides whether it is still the "same" peer).
- After the diff, refresh `peercache.peer_quotas` from the registry (it is
  a plain module dict; this is the one startup snapshot that must become
  live).

Concurrency: registry mutation happens on the event loop (the reload
endpoint), handshakes in threads only *write back* per-peer fields exactly
as `_handshake` does today. The `peers` dict swap should be atomic
(build-new-then-assign) so `roots()`/`get_known` never see a half-diff.

## M2 — registry URL discovery

### The registry document

A static TOML file, hosted on whatever already exists — an object-store
bucket, a raw file in a git repo, plain nginx. Explicitly **not** a registry
service, and **not** a file inside some hub peer's `@public` (that entangles
federation membership with one peer's uptime and makes its operator the
implicit admin).

Same schema as local config, plus a pinned peer_id:

```toml
# peers.toml — the federation SSoT. Write access to this file IS the
# mount-control trust boundary; host it somewhere with audit history (git).
[[peer]]
name = "labb"
urlbase = "https://labb.example.org:8002"
peer_id = "b2f1..."          # pinned: handshake mismatch => refuse mount
cache_quota = "2G"           # optional, same as local entries
```

### Client side

- New config key: `[server] peer_registry = "https://…/peers.toml"`.
- Fetch on startup and every `REGISTRY_TTL` (5 min — this is membership,
  not liveness; liveness stays with handshake/re-probe).
- Parse, then feed through the exact `PeerRegistry.load`/`reload` path from
  M1 — registry discovery *is* config reload with a remote file. Local
  `[[server.peer]]` entries win on name collision (local intent beats
  remote).
- **Serve-stale:** persist the last-good registry copy in
  `statedir` (`registry-cache.toml`); a fetch failure logs and keeps the
  current peer set. Registry downtime must never unmount anything — the
  same pattern `peer.catalog` uses, one level up.
- **peer_id pinning:** entries with `peer_id` set are refused (logged, peer
  disabled) when the handshake returns a different id. This is the cheap
  integrity check that survives DNS hijack of a listed urlbase; it reuses
  the id the handshake already fetches.

## What this does and does not do about auth

Kept out of the code, but the reasoning is part of the design:

- **Mount control plane: solved without auth.** There is no runtime mount
  endpoint in M1/M2; mounts originate only from files. "Who can cause a
  mount" = "who can write the config or the registry file" — an ops
  boundary. This kills the SSRF concern that made "dynamic mounts" and
  "auth" one bundled deferral.
- **Data plane: unchanged, and fine *only* while the `@public`-only
  invariant holds.** B answers anyone who can reach it, peer or not; the
  registry constrains who A mounts, not who B serves. That is coherent
  because everything shared is public by declaration. Federating a private
  area reopens auth knowingly — that is M3 below.
- **Transport:** across anything wider than a lab LAN, registry *and*
  peer urlbases should be `https://`, or the pinning protects the handshake
  while chunks travel in the clear.

## M3 — peer auth: mounting a peer's `@shared` (designed, deferred)

Goal: A mounts B's `@shared` (the only private area that makes sense to
federate — a service account's `@personal` is semantically its own), caches
it, and serves it to A's users under a local visibility rule. Estimated at
roughly a week; the client plumbing already exists on both ends:
`caterva2.Client(urlbase, auth=(user, pass))` logs in and carries the
cookie, and `blosc2.C2Array(..., auth_token=...)` threads a token through
every request including `aget_chunk` (the path `RemoteSource` subclasses).
B needs **zero new code**: its existing REST auth already guards `@shared`;
A shows up as a normal logged-in user.

### Model: one service account per peer relationship

B's admin creates a regular user for A; A's peer entry says which remote
root to mount and authenticates as that user:

```toml
[[server.peer]]
name = "labb-shared"
urlbase = "https://labb:8002"
root = "@shared"                 # default stays @public
username = "peer-site-a@labb"
password_file = "/etc/caterva2/secrets/labb"   # 0600, one line
visibility = "authenticated"     # who on A may browse this mount
```

**Explicitly rejected: per-user credential passthrough** (A forwarding each
local user's own B login). N×M credential management, and it breaks the
product: the cache pool is shared across A's users by design (first reader
pays the WAN, colleagues read locally); per-user authorization views of B
would force per-user caches — no sharing. The service-account model keeps
the trust statement honest: "site A, collectively, may read B's `@shared`;
A's admin decides which local users that extends to."

### Secrets: `password_file`, inline allowed with a nag

- **`password_file` is the production mechanism.** A path composes with
  everything ops already has: plain 0600 files, systemd `LoadCredential=`,
  Docker/K8s secrets (files under `/run/secrets/`), SOPS/Vault agents (all
  ultimately materialize files). Avoids env-var pitfalls (leaks via
  `docker inspect`/child processes; dash-in-name issues).
- **Inline `password =` allowed for labs**, with a startup warning when the
  config file holding it is group/world-readable (the OpenSSH posture:
  convenience allowed, negligence flagged).
- **Not building:** env vars as primary mechanism, OS keyrings (headless
  servers), Vault/KMS client code (wrong altitude — anything that writes a
  file integrates via `password_file` with zero caterva2 code).
- **The registry never carries credentials** (world-readable by design);
  each site pairs its own secrets locally. A registry entry with
  `root = "@shared"` is meaningless anyway — whether *you* may mount it is
  between you and B's admin.
- **Never log the secret.** The 401-re-login path is the spot where a lazy
  `logger.warning(..., resp.text)` could someday echo a password; comment
  it at the call site when written.

### Wiring on A (~2–3 days)

- Un-hardcode `@public` (three spots: `RemotePeerAdapter._remote_path`,
  the catalog fetch in `peers.py`, docstrings) → per-peer `root`.
- Thread the credential through every HTTP touchpoint: `client_for` grows
  the auth tuple; `RemoteSource` passes `auth_token` up to `C2Array`; the
  four raw-httpx clients (CTable source, `api/fetch` fallback, passthrough
  arm, download relay) each add one headers argument.
- Include the remote root in the cache-path hash (`@shared/x` vs
  `@public/x` must not collide).
- Re-login-on-401 retry (fastapi-users JWTs expire).

### Visibility gate on A (~1–2 days, the one architecture decision)

The provider seam deliberately has no user concept. Keep it that way: gate
in `server.py` *before* provider dispatch — a mount with
`visibility = "authenticated"` 401s anonymous users and is filtered from
their root list. One helper at the half-dozen dispatch points. Per-user or
per-group allowlists can layer on later without touching providers. Note
the cache consequence and accept it: B's `@shared` chunks rest on A's disk
and are served (including offline) under A's rule, not B's.

### Later refinement (recorded, not built)

Long-lived revocable API tokens issued by B for service accounts, replacing
passwords — a B-side feature (fastapi-users JWTs currently expire), so
password + re-login is the pragmatic v1.

### Tests (M3)

- Two-server e2e with `login = true` on B: mount `root = "@shared"`, fetch
  through the cache, offline reads of cached ranges still gated by A's
  visibility rule.
- Anonymous vs logged-in visibility on A (root list filtering + 401).
- 401-relogin: expire/invalidate B's cookie mid-run, next fetch recovers.
- Inline-password startup warning on a world-readable config.

## Related future work: whole-file caching for peer paths

Adjacent peer data-plane item (not mounts/discovery): whole-file peer
downloads are today a pure pass-through relay; N readers at site A hit B N
times. Now designed in **`plans/plain-files-caching.md`** (whole-file cache
entries in the same pool — same budget/locking/offline semantics, but
whole-file rather than sparse-chunk entries, matching the whole-file
refetch cost model). Independent of every milestone above.

## Out of scope

- Mount API + panel add/remove forms, mDNS candidate discovery — one
  milestone, lands together with user-facing auth on the mount action
  (M3's service-account auth does not cover this).
- Gossip/transitive mounts — rejected, see ladder.
- Registry signing (Sigstore/minisign). HTTPS + write-access control +
  peer_id pinning is proportionate for now; revisit if the registry ever
  leaves trusted hosting.

## Tests

- M0: cache path uses peer_id; per-peer quota pass still scopes correctly
  (adapt `test_per_peer_quota_evicts_only_that_peer`).
- M1: reload adds a peer (roots gains `@new`), removes one (roots loses it,
  fetch 404s, in-flight request completes), refreshes `peer_quotas`.
- M2: registry fetch populates peers e2e (serve a `peers.toml` from a
  throwaway HTTP server); local entry wins name collision; fetch failure
  keeps the previous set (serve-stale); pinned peer_id mismatch disables
  the peer with a log line.
