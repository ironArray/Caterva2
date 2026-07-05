# Manually exercising remote peer mounts with Apple `container`

How to reproduce, on one Mac, the two-server setup from
`plans/caterva3-remote-peer-mounts.md`: peer **B** runs in an Apple
`container` (plain container or `container machine`), peer **A** runs
natively on the Mac and mounts B's `@public` root over HTTP. Verified end
to end: a partial fetch of a 2-row slice out of a 10-chunk dataset issued
exactly 2 `api/chunk` GETs on B.

Requires: [Apple `container`](https://github.com/apple/container) (Apple
Silicon, macOS 26+ — the tool doesn't run anywhere else), and the `blosc2`
conda env locally (`/Users/faltet/miniforge3/envs/blosc2`) for peer A.

## 1. Build the image (once)

`docker/caterva2-server/{Dockerfile,seed.py,caterva2-server.toml}` build one
image usable two ways: as a disposable portable container, or booted as a
`container machine`.

```bash
container build -f docker/caterva2-server/Dockerfile -t caterva2-peer:latest .
```

Three choices baked in, worth knowing when touching the Dockerfile:

- **glibc base (`python:3.14-slim`), not Alpine** — blosc2 and hdf5plugin
  ship `manylinux` wheels but no `musllinux` ones, so Alpine always compiles
  both from source (~6 min); glibc downloads prebuilt binaries (~15s).
- **systemd baked in (~20MB)** so the image also has `/sbin/init`.
  `container machine create` boots the init system regardless of `CMD`;
  plain `container run` uses `CMD` (`cat2-server`) directly — one image,
  two modes below.
- **`/data` is `chmod a+rwX`** — plain `container run` executes as root,
  but `container machine run` maps to your non-root host user, and
  `server.py` creates `/data/{shared,personal,media}` on startup.

## 2a. Run as a portable container (fast, disposable)

```bash
container run -d --name peerb -p 8000:8000 caterva2-peer:latest
curl http://localhost:8000/api/roots
```

Export/reload elsewhere (standard OCI image — also runs under plain Docker):

```bash
container image save -o caterva2-peer.tar caterva2-peer:latest
# copy the file, then on the other machine:
container image load -i caterva2-peer.tar
container run -d --name peerb -p 8000:8000 caterva2-peer:latest
```

Swap in real data instead of the baked-in demo dataset with
`-v /path/to/data:/data/public` rather than rebuilding.

## 2b. Run as a `container machine` (interactive, live-editable)

Use this instead of 2a for a shell inside peer B, or to reinstall caterva2
from your live, locally-edited repo instead of the version baked in.

```bash
container machine create caterva2-peer:latest --name caterva-b --cpus 2 --memory 6G
container machine run -n caterva-b -d -- cat2-server --statedir /data --conf /data/caterva2-server.toml
```

caterva2 is already installed — no setup step needed just to run it. To
iterate on source instead, reinstall editable from the live repo (mounted
at `/Users/<user>`, **not** `/home/<user>` — a separate, empty native
Linux home; no `--break-system-packages` needed on this base, unlike
Alpine):

```bash
container machine run -n caterva-b --root \
  --env PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin -- \
  uv pip install --system -e '/Users/faltet/ironArray/caterva2[server,hdf5]'
```

Shell in with `container machine run -n caterva-b` (no trailing command).

## 3. Find B's IP

```bash
container machine ls   # (2b) NAME/IP/STATE, e.g. caterva-b 192.168.64.7
container ls           # (2a) ID/IMAGE/IP, e.g. peerb 192.168.64.11
curl http://<IP>:8000/api/roots
```

The IP changes across every restart — always re-check before pointing A at
it. `-p 8000:8000` (2a only) gives a stable `localhost:8000` regardless;
`container machine` has no `-p` equivalent.

## 4. Run peer A locally and mount B

`~/c2a/caterva2-server.toml`:
```toml
[server]
listen = "localhost:8010"
urlbase = "http://localhost:8010"
login = false

[[server.peer]]
name = "peerb"
urlbase = "http://<IP-from-step-3>:8000"
```

```bash
cd ~/c2a && /Users/faltet/miniforge3/envs/blosc2/bin/cat2-server \
  --statedir ~/c2a --conf ~/c2a/caterva2-server.toml > ~/c2a/server.log 2>&1 &
disown
```

The peer registry handshakes once at startup — if B's IP changed, edit the
toml and restart A (`pkill -f "cat2-server --statedir ~/c2a"`, then rerun
the command above), don't rely on a live reload.

## 5. Verify

```bash
curl http://localhost:8010/api/roots                               # -> @peerb listed
curl http://localhost:8010/api/list/@peerb                         # -> mc.b2nd
curl "http://localhost:8010/api/fetch/@peerb/mc.b2nd?slice_=2:4" -o /tmp/slice.b2nd
```

The query param is **`slice_`** (trailing underscore) — `?slice=...` is
silently ignored and fetches the whole dataset. A fresh 2-row slice out of
10 chunks should leave exactly 2 `.chunk` files under
`~/c2a/peercache/peerb/`, confirming chunk-granular partial fetch.

## Cleanup

```bash
pkill -f "cat2-server --statedir /Users/faltet/c2a"   # stop A

# 2a:
container stop peerb && container rm peerb
# 2b:
container machine stop caterva-b && container machine rm caterva-b
```
