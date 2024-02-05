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

A relevant generic option (besides `--help` itself) is `--host`, which overrides the subscriber address used by default.  It should have the `HOST:PORT` format (with IPv6 addresses between square brackets), for example `sub.edu.example.org:3126`.

`cat2cli` may use a TOML configuration file (`caterva2.toml` in the current directory unless overridden with the `--conf` option).  Currently, it may only get the subscriber address from there (`http` setting in `[subscriber]` section).  Command-line options override settings read from the configuration file.

A relevant command option is `--help`, which shows the options and arguments accepted by a particular command.  Another one is `--json`, which forces the output of commands that accept it to be in JSON format, as that may be more amenable for parsing by other programs.

For a short tutorial on `cat2cli`, see [](Using-the-command-line-client).
