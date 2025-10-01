# Utilities

Caterva2 comes with several command-line utilities for different tasks.

## Main Commands

The main utilities follow a new unified naming scheme:

- **cat2-client**: Query a server from terminal
- **cat2-agent**: Watch a directory and sync changes to a Caterva2 server
- **cat2-server**: Launch the server with fine-tuned behavior
- **cat2-admin**: Server administration commands (new)

## Additional Utilities

- **cat2import**: Import data from HDF5 to Caterva2
- **cat2export**: Export data from Caterva2 to HDF5
- **cat2tbrowser**: Terminal-based browser for datasets

## Legacy Commands

The following legacy command is still available but deprecated:
- `cat2adduser` → use `cat2-admin adduser` instead

**Breaking Change**: Legacy configuration section `[subscriber]` has been removed. Use `[server]` instead.

```{toctree}
---
maxdepth: 2
---

cat2-client
cat2-admin
cat2import
cat2export
```
