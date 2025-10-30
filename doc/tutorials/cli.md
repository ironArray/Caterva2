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

To start the server use the following command:
```sh
CATERVA2_SECRET=c2sikrit cat2-server &
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

First let's generate a file and save it locally. Run the following script in the terminal to save a `b2nd` file to your current directory.
```
python -c "import blosc2; blosc2.arange(0, 1000, 1, blocks = (10,), chunks=(100,), urlpath='ds-1d.b2nd')"
```

Let's upload the file to the `@personal` root:

```sh
cat2-client --username user@example.com --password foobar11 upload ds-1d.b2nd @personal/ds-1d.b2nd
```

```
Dataset stored in @personal/ds-1d.b2nd
```

Now, one may list the datasets in the `@personal` root and see that the uploaded file appears

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
cbytes: 3.86 KiB
ratio : 2.02x
mtime : 2025-10-30T11:16:17.012415Z
cparams:
  codec  : ZSTD (5)
  clevel : 5
  filters: [SHUFFLE]
```

As you see, this command returns digested information of the dataset's metadata.

You can also see the contents of the dataset from the terminal.

```sh
cat2-client --username user@example.com --password foobar11 show @personal/ds-1d.b2nd
```

When the dataset is small, the contents are printed to the screen, otherwise a pager is used.

If you want to use a browser to view the contents of the dataset you can do that too. First authenticate yourself via the browser window accessible via the url associated with your server instance (probably something like `http://localhost:8000`). Then run the following command in the terminal to visualise the dataset directly.

```shell
cat2-client browse @personal/ds-1d.b2nd
```

You do need to authenticate with the server first; after that, this command will open a new tab in your default browser with the contents of the dataset.

There are more commands available in the `cat2-client` client; ask for help with:

```sh
cat2-client --help
```
