(cat2cli)=
# `cat2cli` -- Command-line Caterva2 client

This program allows interacting with a Caterva2 subscriber from the command line, in interactive shell sessions or invoked by other programs.  To use it, the `clients` extra needs to be installed:

```sh
python -m pip install caterva2[clients]
```

Running `cat2cli --help` should provide a list of supported commands that may be invoked like this:

```
cat2cli [GENERIC_OPTION...] COMMAND [COMMAND_OPTION...] COMMAND_ARGUMENTS...
```

Another relevant generic option besides `--help` is `--subscriber`, which overrides the base of subscriber URLs used by default.  It should be a HTTP(S) URL, for example `http://sub.edu.example.org:3126`.  Finally, the generic options `--username` and `--password` may be used in case your subscriber requires user authentication.

`--help` is also available as a command option which shows the options and arguments accepted by that command (e.g. `cat2cli roots --help`).  Another command option is `--json`, which forces the output of commands that accept it to be in JSON format, as that may be more amenable for parsing by other programs.

`cat2cli` may use a TOML configuration file (`caterva2.toml` in the current directory unless overridden with the generic `--conf` option).  It may get the subscriber address from there (`urlbase` or `http` settings in `[subscriber]` section), as well as user authentication options (in the `[client]` section).  Command-line options override settings read from the configuration file.

For a short tutorial on `cat2cli`, see [](Using-the-command-line-client).
