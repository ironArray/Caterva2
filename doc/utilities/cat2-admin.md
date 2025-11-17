(cat2-admin)=
# `cat2-admin` -- Caterva2 server administration

This program provides administration commands for managing a Caterva2 server. These commands are meant to be used on the same machine as the server and typically require access to the server's state directory.

To use `cat2-admin`, you need to install Caterva2 with the server extra:

```sh
python -m pip install caterva2[server]
```

## Usage

```
cat2-admin [GLOBAL_OPTIONS...] COMMAND [COMMAND_OPTIONS...] COMMAND_ARGUMENTS...
```

### Global Options

- `--statedir PATH`: Specify the state directory for the server (default: `_caterva2/state`)
- `--help`: Show help message and exit

## Commands

### `adduser` - Add a user to the server database

Add a new user to the server database.

```
cat2-admin adduser [OPTIONS] USERNAME [PASSWORD]
```

**Arguments:**
- `USERNAME`: Email address for the new user
- `PASSWORD`: Password for the new user (optional, will be generated if not provided)

**Options:**
- `--superuser`, `-S`: Make the user a superuser
- `--help`: Show help for this command

**Examples:**

```sh
# Add a regular user with auto-generated password
cat2-admin adduser alice@example.com

# Add a superuser with a specific password
cat2-admin adduser bob@example.com mypassword --superuser

# Add a user to a custom state directory
cat2-admin --statedir /custom/path adduser charlie
```

## Configuration

`cat2-admin` uses the same configuration system as other Caterva2 tools. It can read settings from a TOML configuration file; see [](../tutorials/configuration.md) for details on configuration file location and format.
