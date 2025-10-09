(caterva2.toml)=
# The `caterva2.toml` configuration file

We've seen that the `cat2-client` program accepts some command-line options to tune its operation (check the `--help` option).  This is even more important for services as we shall see in following sections.  Thus, Caterva2 programs support getting some settings from a TOML configuration file.

## Configuration File Search Path

Caterva2 follows standard Unix/Linux conventions for locating configuration files. When looking for `caterva2.toml` (or `caterva2-server.toml`), the search order is:

1. **Command-line option**: `--conf <path>` (if explicitly specified)
2. **Current directory**: `./caterva2.toml`
3. **Home directory**: `~/.caterva2.toml`
4. **System-wide** (Unix/Linux only): `/etc/caterva2.toml`

The first existing file found in this order will be used. If no configuration file is found, the program will use default settings.

This approach is similar to how tools like `git`, `vim`, and `ssh` locate their configuration files, making it familiar to Unix/Linux users.

The configuration file may hold settings for different programs, with a separate section for each program.  Thus, a program may check the file for its own settings, but also for those of other programs which may be of use to itself.  This allows compact configurations in a single file.  For instance, below is a sample configuration file for the server program and some client app:

```toml
# Example configuration for caterva2 clients in general
[default]
url = "https://cat2.cloud/demo"
username = ""
password = ""

[localuser]
url = "http://localhost:8000"
username = "user@example.com"
password = "foobar11"
```

See [caterva2.sample.toml](https://github.com/ironArray/Caterva2/blob/main/caterva2.sample.toml) in Caterva2's source for all possible settings and their purpose.
