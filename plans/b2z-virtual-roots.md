# Virtual roots for .b2z TreeStore containers in the web UI

Status: implemented + reviewed (2026-07-02). Server-side verified via curl
and by `caterva2/tests/test_treestore.py` (11 passing: single-row rendering,
virtual-root leaf expansion, bogus-container safety). Browser click-through
by the author confirmed mount/unmount/highlight behavior. A high-effort code
review ran afterwards; its findings are folded in under "Review fixes" below.

## Context

The `new-table` branch added virtual paths into `.b2z` TreeStore containers
(e.g. `@personal/tree-store.b2z/level1/ctable`), which work for the CLI
(`info`, `list`, `fetch`). But the Datasets web UI currently **auto-expands
every TreeStore** into one row per leaf (`htmx_path_list`, server.py:1713-1722),
flooding the dataset list.

Agreed design:

- A `.b2z` TreeStore shows as a **single row** in the dataset list, with a
  **mount** button.
- Mounting turns it into a **virtual root**: it appears in the roots sidebar
  next to `@personal`/`@shared`/`@public`, with its own checkbox and an ✕
  unmount control. Checking it lists only that container's leaves.
- Mount state lives **client-side in localStorage** (list of container paths
  like `@personal/tree-store.b2z`). No server DB, no per-user prefs, no
  mount/unmount API endpoints.

## Mechanism: localStorage ↔ htmx

- localStorage key `caterva2:mounted` = JSON array of container paths.
- One `htmx:configRequest` listener (inline in `home.html`) appends repeated
  `mounted=<path>` query params to any request targeting `htmx/root-list`.
  This covers both the initial `hx-trigger="load"` fetch on
  `#root-list-wrapper` (home.html:77) and JS-triggered refreshes.
- The server renders mounted entries as ordinary `name="roots"` checkboxes
  inside the existing form in `root_list.html`; that form already serializes
  every `roots` checkbox to `/htmx/path-list/` on `change` (and includes
  `#query-search`), so everything downstream (path list, URL push, search)
  works unchanged.

## Files to modify

- `caterva2/services/server.py` — `htmx_root_list` (~1623), `htmx_path_list` (~1670-1725)
- `caterva2/services/templates/root_list.html`
- `caterva2/services/templates/home.html` (extend inline script at ~line 93)
- `caterva2/services/templates/path_list.html`

Reused, not modified: `srv_utils.treestore_leaves`, `srv_utils.split_container_path`,
`srv_utils.BLOSC2_CONTAINER_SUFFIXES`, `get_rootdir_or_none`. No changes to
`src/main.js` (no vite rebuild needed — small JS goes inline in templates).

## Step 1 — server.py: `htmx_root_list` accepts `mounted`

Add `mounted: list[str] = fastapi.Query([])`. Pass the filtered, deduped list
to the template as `context["mounted"]`:

- Drop entries whose first segment isn't `@personal`/`@shared`/`@public`,
  and drop `@personal`/`@shared` entries when `user` is None (stale
  localStorage from a logged-out session).
- No filesystem existence check — a stale mount just renders a checkbox that
  lists nothing (Step 4 suppresses `FileNotFoundError`).

## Step 2 — root_list.html: render mounted roots

Inside the existing `<form>`, after the `@public` include (inline markup; do
not generalize `includes/root.html` — it carries upload/drag-drop baggage
virtual roots don't need):

```jinja
{% for name in mounted %}
<div class="d-flex gap-1 justify-content-between">
    <div class="form-check">
        <input class="form-check-input" type="checkbox" name="roots"
               value="{{ name }}" {{ "checked" if name in checked }}
               id="id_root_mounted_{{ loop.index }}">
        <label class="form-check-label" for="id_root_mounted_{{ loop.index }}"
               title="{{ name }}">{{ name.split('/')[-1] }}</label>
    </div>
    <i class="fa-solid fa-xmark" role="button" title="Unmount"
       onclick="unmountRoot('{{ name }}')"></i>
</div>
{% endfor %}
```

## Step 3 — home.html: mount/unmount JS

Extend the existing inline `<script>` (home.html:93, already has Jinja access
to `url()`):

```js
const ROOT_LIST_URL = "{{ url('htmx/root-list/') }}";
const MOUNT_KEY = 'caterva2:mounted';
const getMounts = () => JSON.parse(localStorage.getItem(MOUNT_KEY) || '[]');

htmx.on('htmx:configRequest', (evt) => {
    if (evt.detail.path.includes('htmx/root-list'))
        evt.detail.parameters['mounted'] = getMounts();
});

function refreshRoots() {
    const checked = [...document.querySelectorAll('input[name=roots]:checked')]
        .map(e => e.value);
    const qs = new URLSearchParams(checked.map(r => ['roots', r]));
    return htmx.ajax('GET', `${ROOT_LIST_URL}?${qs}`, '#root-list-wrapper');
}
window.mountRoot = (path) => {
    const m = getMounts();
    if (!m.includes(path)) { m.push(path); localStorage.setItem(MOUNT_KEY, JSON.stringify(m)); }
    refreshRoots();
};
window.unmountRoot = (path) => {
    localStorage.setItem(MOUNT_KEY, JSON.stringify(getMounts().filter(p => p !== path)));
    refreshRoots().then(() =>
        htmx.trigger(document.querySelector('#root-list-wrapper form'), 'change'));
};
```

The `change` trigger after unmount re-runs the path list without the removed
root (its checkbox is gone from the form). Mounting needs no path-list refresh
(leaves appear only once the new checkbox is checked).

## Step 4 — server.py: `htmx_path_list` — single row + virtual-root branch

a) `add_dataset(path, abspath, mountable=False)` — add the kwarg, store
`"mountable": mountable` in the dataset dict.

b) Replace the auto-expansion block (lines 1713-1722): a TreeStore `.b2z`
becomes one mountable row instead of N leaf rows:

```python
if relpath.suffix == ".b2z":
    tree = blosc2.open(abspath)
    if isinstance(tree, blosc2.TreeStore):
        if search in path:
            add_dataset(path, abspath, mountable=True)
        continue
```

A non-TreeStore `.b2z` falls through to the plain-row path — no mount button.
Detection reuses the `blosc2.open` the walk already pays for.

c) Before (or after) the `filter_roots` loop, handle virtual roots. Classic
loop needs no change: `get_rootdir_or_none("@personal/x.b2z", user)` returns
None, so `filter_roots` already skips them (verified).

```python
for root in roots:
    proot = pathlib.PurePosixPath(root)
    if proot.suffix not in srv_utils.BLOSC2_CONTAINER_SUFFIXES:
        continue  # classic roots handled by filter_roots above
    rootdir = get_rootdir_or_none(proot.parts[0], user)  # access check, reused
    if rootdir is None:
        continue
    abspath = (rootdir / pathlib.Path(*proot.parts[1:])).resolve()
    if rootdir.resolve() not in abspath.parents:  # path traversal guard
        continue
    with contextlib.suppress(FileNotFoundError):
        tree = blosc2.open(abspath)
        if isinstance(tree, blosc2.TreeStore):
            for key in srv_utils.treestore_leaves(tree):
                leaf_path = f"{root}{key}"
                if search in leaf_path:
                    add_dataset(leaf_path, abspath)
```

Leaf rows produce virtual paths (`@personal/tree.b2z/level1/ctable`), which
`htmx_path_info` / fetch / display already resolve via `split_container_path`.

## Step 5 — path_list.html: mount button

Inside the dataset loop, after the size span:

```jinja
{% if dataset.mountable %}
<span class="input-group-text" role="button" title="Mount as root"
      onclick="mountRoot('{{ dataset.path }}')">
    <i class="fa-solid fa-plug"></i>
</span>
{% endif %}
```

## Deliberately NOT done (minimal diff)

- No mount/unmount endpoints, no DB, no server-side per-user prefs.
  localStorage is per-browser: two accounts on one browser share mounts —
  documented, not handled. Upgrade path: per-user store if it ever matters.
- Leaf `size` keeps the whole container's `st_size` (existing new-table
  behavior); `srv_utils.treestore_size` refinement is optional follow-up.
- `html_home` resets anon roots to `["@public"]`; a mounted `@public/...b2z`
  still shows in the sidebar for anon users but is unchecked after reload.
  Acceptable.
- The "add current path if not listed" fallback (server.py:1726-1744) doesn't
  resolve virtual leaf paths; only affects deep-linking while the virtual root
  is unchecked. Skip.

## Review fixes (applied after the initial implementation)

A high-effort code review (8 finder angles + verification) surfaced 6 real
issues, all now fixed:

1. **Stored XSS** — the mount/unmount handlers interpolated the dataset
   path/name into inline `onclick`/`onchange` JS *source*. Jinja autoescape
   does not protect inline event handlers (the browser HTML-entity-decodes
   the attribute before the JS parser runs), so a filename with a quote —
   uploadable to `@public`, which has no filename validation — could execute
   in another user's browser. Fixed by carrying the value in a safe
   `data-path`/`value` attribute and reading it at click time
   (`this.dataset.path`, `this.value`), matching the existing `loadDataset`
   pattern. Files: `path_list.html`, `root_list.html`.
2. **500 on a bad mount** — `roots` is untrusted (client localStorage). A
   mounted path pointing at a non-TreeStore file (`zipfile.BadZipFile`), a
   directory (`RuntimeError`), or through a regular file (`NotADirectoryError`
   from `stat()`) crashed the whole datasets list. The virtual-root loop now
   wraps `blosc2.open` in `try/except Exception` and skips such paths. The
   **classic walk loop** had the same unguarded `blosc2.open` (a corrupt
   `.b2z` in a root would 500 the listing) and was hardened identically —
   a corrupt container falls through to a plain row.
3. **Duplicate leaf rows** — `mountRoot` appended the container to the checked
   roots without dedup while the plug icon stayed clickable, so a repeat click
   double-listed every leaf. Fixed with `[...new Set(...)]`.
4. **Stale test** — `test_web_no_500` still asserted the removed
   auto-expansion. Replaced with single-row + virtual-root-expansion +
   bogus-container tests in `test_treestore.py`.
5. **Redundant I/O** — the container was `stat()`'d once per leaf; now once per
   mount, size reused for all leaves.
6. **Duplicated access rules** — `htmx_root_list` hardcoded the valid-prefix
   set; now uses `get_rootdir_or_none(prefix, user) is not None`.

Two review candidates were investigated and **refuted** (left as-is): the
`htmx.ajax('GET', url, '#selector')` string-target usage is valid in the
bundled htmx 2.0.1; and the server's "keep current path visible" fallback is
intentional for classic roots too, so the client-side
`highlightContainerIfCurrent` choreography works *with* it rather than around
a bug.

## Manual verification

1. Run the local server; log in as `user@example.com` / `foobar11`.
2. Check `@personal`: `tree-store.b2z` appears as ONE row with a plug icon
   (no leaf clutter). Clicking the row shows Meta info without a 500.
3. Click the plug: sidebar gains `tree-store.b2z` (full path in tooltip) with
   an ✕. Reload — it persists (localStorage → configRequest → root-list).
4. Check its checkbox: path list shows only that TreeStore's leaves. Click a
   leaf: info/display works. Filter box filters leaves.
5. Check `@personal` AND the virtual root: union renders; URL push contains
   `roots=@personal&roots=@personal%2Ftree-store.b2z`; reload restores both.
6. Click ✕: entry leaves the sidebar and its leaves vanish from the path list.
7. Stale mount: delete the `.b2z` from disk, reload — sidebar entry renders,
   checking it lists nothing, no error.
8. Log out: the `@personal` mount no longer renders in the sidebar.
9. A non-TreeStore `.b2z` shows as a plain row with no plug icon.
