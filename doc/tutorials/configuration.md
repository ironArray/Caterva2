(caterva2.toml)=
# The `caterva2.toml` configuration file

We've seen that the `cat2-client` program accepts some command-line options to tune its operation (check the `--help` option).  This is even more important for services as we shall see in following sections.  Thus, Caterva2 programs support getting some settings from a TOML configuration file, by default `caterva2.toml` in the current directory (though you may override it with the `--conf` option).

The configuration file may hold settings for different programs, with a separate section for each program.  Thus, a program may check the file for its own settings, but also for those of other programs which may be of use to itself.  This allows compact configurations in a single file.  For instance, below is a sample configuration file for the server program and some client app:

```toml
# Example configuration for a standalone server
#
# It's possible to run only the server. Then the configuration has only a
# section for the server. And maybe another one for the client.

# The server section must define:
#
# - statedir: the directory where the server's data will be stored (default: _caterva2/sub)
# - http: where the server listens to (a unix socket or a host/port) (default: localhost:8002)
# - urlbase: the base url users will use to reach the server (default: http://localhost:8002)
# - quota: if defined, it will limit the disk usage (default: 0, no limit)
# - maxusers: if defined, it will limit the number of users (default: 0, no limit)
# - login: if true, users will need to authenticate (default: true)
# - register: if true, users will be able to register (default: false)
#
[server]
statedir = "_caterva2/state"
#http = "_caterva2/sub/uvicorn.socket"
http = "localhost:8000"
urlbase = "http://localhost:8000"
quota = "10G"
maxusers = 5
register = true  # allow users to register

# The client section defines the credentials for the client to authenticate
# against the server.
[client]
username = ""
password = ""
```

See [caterva2.sample.toml](https://github.com/ironArray/Caterva2/blob/main/caterva2.sample.toml) in Caterva2's source for all possible settings and their purpose.
