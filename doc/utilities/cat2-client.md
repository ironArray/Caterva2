(cat2-client)=
# `cat2-client` -- Command-line Caterva2 client

This program allows interacting with a Caterva2 server from the command line, in interactive shell sessions or invoked by other programs. To use it, the `clients` extra needs to be installed:

```sh
python -m pip install caterva2[clients]
```

Running `cat2-client --help` should provide a list of supported commands that may be invoked like this:

```
cat2-client [GENERIC_OPTION...] COMMAND [COMMAND_OPTION...] COMMAND_ARGUMENTS...
```

### Generic Options

These options can be used with any command:

-   `--url <URL>`: Overrides the base URL of the server to connect to (e.g., `http://sub.edu.example.org:3126`).
-   `--server <NAME>`: Selects the server to connect to by name, as defined in a section of the configuration file.
-   `--username <USER>` and `--password <PASS>`: Provide credentials for server authentication.
-   `--conf <PATH>`: Specifies the path to a TOML configuration file.

## Commands

`cat2-client` operates through a series of commands. You can get a list of all available commands by running:

```sh
cat2-client --help
```

Each command has its own set of options and arguments. To see the help for a specific command, use the `--help` option after the command name. For example:

```sh
cat2-client roots --help
```

A common option for many commands is `--json`, which forces the output to be in JSON format, making it easier to parse with other programs.

## Configuration

`cat2-client` can be configured using a TOML file, which is looked for as `caterva2.toml` in the current directory by default. The path can be overridden with the `--conf` generic option. Any command-line options provided will take precedence over settings from the configuration file.

For a short tutorial on `cat2-client`, see [](Using-the-command-line-client).

**Note**: This is the primary command-line client for Caterva2.
