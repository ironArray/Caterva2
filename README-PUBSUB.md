# The Publisher-Subscriber Model in Caterva2
**Note**: this functionality is in alpha stage; if you are interested in testing it, please contact the ironArray team.

Caterva2 offers the possibility of using the publisher-subscriber (pub-sub) message pattern. Under this framework, when a user uses a client (Web API, Python API or command line) to query datasets, the client will connect to a Caterva2 **subscriber** service, which in turn will communicate with the associated **publishers** to which it is subscribed, to retrieve the requested datasets. This subscriber/publisher interaction is mediated by a **broker** service.

In order to set up a Caterva2 deployment to enable the publisher-subscriber model on your system, you will thus need the following components:

- One **broker** service to enable the communication between publishers and subscribers.
- Several **publishers**, each one providing subscribers with access to one root and the datasets that it contains. The root may be a native Caterva2 directory with Blosc2 and plain files, or an HDF5 file (support for other formats may be added).
- Several **subscribers**, each one tracking changes in multiple roots and datasets from publishers, and caching them locally for efficient reuse.
- Several **clients**, each one asking a subscriber to track roots and datasets, and provide access to their data and metadata.

Publishers and subscribers may be far apart, in different networks with limited or expensive connectivity between them, while subscribers and clients will usually be close enough to have fast and cheap connectivity (e.g. a local network).

## Installation
To support this additional functionality, it is necessary to install Caterva2 with the `[services,clients]` extra features added to the last argument of `pip` commands detailed in the [README](https://github.com/ironArray/Caterva2?tab=readme-ov-file#installation). There are also additional options which are of interest if working under the pub-sub model.

- `hdf5` to enable serving HDF5 files as Caterva2 roots at the publisher
- `services` for running all Caterva2 services (broker, publisher, subscriber)
- `base-services` for running the Caterva2 broker or publisher services (lighter, less dependencies)

## Quick start

(Find more detailed step-by-step [tutorials](Tutorials) in Caterva2 documentation.)

For the purpose of this quick start, let's use the datasets within the `root-example` folder:

```sh
cd Caterva2
ls -F root-example/
```

```
README.md               dir2/                   ds-1d-fields.b2nd       ds-2d-fields.b2nd       ds-sc-attr.b2nd
dir1/                   ds-1d-b.b2nd            ds-1d.b2nd              ds-hello.b2frame
```

Now:

- create a virtual environment and install Caterva2 with the `[services,clients]` extras (see above).
- copy the configuration file `caterva2.sample.toml` to `caterva2.toml` and edit to your
  needs (see the fully documented `caterva2.sample.toml` file and [caterva2.toml](caterva2.toml) for help).

Then fire up the broker, start publishing a root named `foo` with `root-example` datasets, and create a subscriber:

```sh
cat2bro &  # broker
cat2pub foo root-example &  # publisher
CATERVA2_SECRET=c2sikrit cat2sub &  # subscriber
```
(To stop them later on, bring each one to the foreground with `fg` and press Ctrl+C.)

To create a user, you can use the `cat2adduser` command line client. For example:

```sh
cat2adduser user@example.com foobar11
```

We can then examine a file in the `foo` root, which is being published by the publisher:

```sh
cat2cli --user "user@example.com" --pass "foobar11" info foo/README.md
```

### Pub-sub in the command line client
Now that the services are running, we can use the `cat2cli` client to talk
to the subscriber. In another shell, let's list all the available roots in the system:

```sh
cat2cli roots
```

```
foo
```

We only have the `foo` root that we started publishing. If other publishers were running,
we would see them listed here too.

Let's ask our local subscriber to subscribe to the `foo` root:

```sh
cat2cli --username user@example.com --password foobar11 subscribe foo  # -> Ok
```

Now, one can list the datasets in the `foo` root:

```sh
cat2cli --username user@example.com --password foobar11 list foo
```

```
kevlar.h5
kevlar/!_attrs_.json
kevlar/entry/!_attrs_.json
kevlar/entry/data/!_attrs_.json
kevlar/entry/data/data.b2nd
```

Let's ask the subscriber for more info about the `foo/dir2/ds-4d.b2nd` dataset:

```sh
cat2cli --username user@example.com --password foobar11 info @shared/kevlar/entry/data/data.b2nd
```

```
{
    'shape': [1000, 2167, 2070],
    'chunks': [1, 2167, 2070],
    'blocks': [1, 11, 2070],
    'dtype': 'uint16',
    'schunk': {
        'cbytes': 0,
        'chunkshape': 4485690,
        'chunksize': 8971380,
        'contiguous': True,
        'cparams': {
            'codec': 5,
            'codec_meta': 0,
            'clevel': 1,
            'filters': [0, 0, 0, 0, 0, 1],
            'filters_meta': [0, 0, 0, 0, 0, 0],
            'typesize': 2,
            'blocksize': 45540,
            'nthreads': 1,
            'splitmode': 3,
            'tuner': 0,
            'use_dict': False,
            'filters, meta': [[1, 0]]
        },
        'cratio': 0.0,
        'nbytes': 8971380000,
        'urlpath': '/Users/faltet/blosc/Caterva2/_caterva2/sub/shared/kevlar/entry/data/data.b2nd',
        'vlmeta': {'_ftype': 'hdf5', '_dsetname': 'entry/data/data'},
        'nchunks': 1000,
        'mtime': None
    },
    'mtime': '2025-05-27T11:33:12.287605Z'
}
```

This command returns a JSON object with the dataset's metadata, including its shape, chunks, blocks, data type, and compression parameters. The `schunk` field contains information about the underlying Blosc2 super-chunk that stores the dataset's data.

There are more commands available in the `cat2cli` client; ask for help with:

```sh
cat2cli --help
```
