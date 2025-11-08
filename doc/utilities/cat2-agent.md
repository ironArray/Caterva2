(cat2-agent)=
# `cat2-agent` -- Directory synchronization agent

This program watches a local directory and synchronizes its contents to a directory on a Caterva2 server. To use it, the `agent` extra needs to be installed:

```sh
python -m pip install caterva2[agent]
```

## Usage

Running `cat2-agent --help` provides information on its usage:

```
cat2-agent [GENERIC_OPTION...] LOCALDIR REMOTEPATH
```

### Arguments

-   `LOCALDIR`: The local directory path to watch for changes.
-   `REMOTEPATH`: The remote path on the server where contents will be synchronized. The path must start with a valid root (`@personal`, `@shared`, or `@public`) and have at least one subdirectory level (e.g., `@personal/my-sync-folder`).

The agent uses the same generic options (`--url`, `--username`, `--password`, `--conf`) and configuration file (`caterva2.toml`) as `cat2-client` for server connection settings.

## Behavior

On startup, `cat2-agent` performs an initial synchronization:
1.  It lists files in both the local directory and the remote path.
2.  Any local files not present on the remote server are uploaded.
3.  Any files on the remote server not present locally are removed.

After the initial sync, the agent continuously watches the local directory for file changes:
-   **Added/Modified Files**: When a file is created or modified locally, it is uploaded to the server.
-   **Deleted Files**: When a file is deleted locally, it is also removed from the server.
