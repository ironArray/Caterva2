(Using-the-command-line-client)=
# Using the command-line client

For quick queries to a subscriber or for use in shell scripts, Caterva2 ships the `cat2cli` program.  To use it, you need to install Caterva2 with the `clients` extra, as well as `subscriber` in order to be able to query something.

```sh
python -m pip install caterva2[clients,subscriber]
```

To create a user, you can use the `cat2adduser` command line client. For example:

```sh
cat2adduser user@example.com foobar11
```

Now that the services are running, we can use the `cat2cli` client to talk
to the subscriber. In another shell, let's list all the available roots in the system:

```sh
cat2cli --user "user@example.com" --pass "foobar11" roots
```

```
@public (subscribed)
@personal (subscribed)
@shared (subscribed)
```
First let's upload a file from the `root-example`folder to the `@personal` root:

```sh
cat2cli --username user@example.com --password foobar11 upload root-example/ds-1d.b2nd @personal/ds-1d.b2nd
```

Now, one can list the datasets in the `@personal` root and see that the uploaded file appears

```sh
cat2cli --username user@example.com --password foobar11 list @personal
>> ds-1d.b2nd
```

Let's ask the subscriber for more info about the dataset:

```sh
cat2cli --username user@example.com --password foobar11 info @personal/ds-1d.b2nd
```

```
Getting info for @personal/ds-1d.b2nd
{
    'shape': [1000],
    'chunks': [100],
    'blocks': [10],
    'dtype': 'int64',
    'schunk': {
        'cbytes': 5022,
        'chunkshape': 100,
        'chunksize': 800,
        'contiguous': True,
        'cparams': {'codec': 5, 'codec_meta': 0, 'clevel': 1, 'filters': [0, 0, 0, 0, 0, 1], 'filters_meta': [0, 0, 0, 0, 0, 0], 'typesize': 8, 'blocksize': 80, 'nthreads': 1, 'splitmode': 1, 'tuner': 0, 'use_dict': False, 'filters, meta': [[1, 0]]},
        'cratio': 1.5929908403026682,
        'nbytes': 8000,
        'urlpath': '/home/lshaw/Caterva2/_caterva2/sub/personal/2fa87091-84c6-44f9-a57e-7f04290630b1/ds-1d.b2nd',
        'vlmeta': {},
        'nchunks': 10,
        'mtime': None
    },
    'mtime': '2025-05-29T09:11:26.860956Z'
}
```

This command returns a JSON object with the dataset's metadata, including its shape, chunks, blocks, data type, and compression parameters. The `schunk` field contains information about the underlying Blosc2 super-chunk that stores the dataset's data.

There are more commands available in the `cat2cli` client; ask for help with:

```sh
cat2cli --help
```
