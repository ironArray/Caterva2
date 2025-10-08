(Using-the-command-line-client)=
# Using the command-line client

For quick queries to a server or for use in shell scripts, Caterva2 ships the `cat2-client` program.  To use it, you need to install Caterva2 with the `clients` extra, as well as `server` in order to be able to query something.

```sh
python -m pip install caterva2[clients,server]
```

To create a user, you can use the `cat2-admin adduser` command. For example:

```sh
cat2-admin adduser user@example.com foobar11
```

Now that the services are running, we can use the `cat2-client` client to talk
to the server. In another shell, let's list all the available roots in the system:

```sh
cat2-client --user "user@example.com" --pass "foobar11" roots
```

```
@public
@personal
@shared
```

First let's upload a file from the `root-example`folder to the `@personal` root:

```sh
cat2-client --username user@example.com --password foobar11 upload root-example/ds-1d.b2nd @personal/ds-1d.b2nd
```

```
Dataset stored in @personal/ds-1d.b2nd
```

Now, one can list the datasets in the `@personal` root and see that the uploaded file appears

```sh
cat2-client --username user@example.com --password foobar11 list @personal
```

```
ds-1d.b2nd
```

Let's ask the server for more info about the dataset:

```sh
cat2-client --username user@example.com --password foobar11 info @personal/ds-1d.b2nd
```

```
Getting info for @personal/ds-1d.b2nd
shape : [1000]
chunks: [100]
blocks: [10]
dtype : int64
nbytes: 7.81 KiB
cbytes: 4.90 KiB
ratio : 1.59x
mtime : 2025-10-08T11:09:03.955154Z
cparams:
  codec  : ZSTD (5)
  clevel : 1
  filters: [SHUFFLE]
```

As you see, this command returns digested information of the dataset's metadata.

You can also see the contents of the dataset:

```sh
cat2-client --username user@example.com --password foobar11 show @personal/ds-1d.b2nd
```

When the dataset is small, the contents are printed to the screen, otherwise a pager is used.

If you want to use a browser to view the contents of the dataset:

```shell
cat2-client --username user@example.com --password foobar11 browse @personal/ds-1d.b2nd
```

Although you will need to authenticate with the server first; after that, this command will open a new tab in your default browser with the contents of the dataset.

There are more commands available in the `cat2-client` client; ask for help with:

```sh
cat2-client --help
```
