# Caterva2: On-demand access to Blosc2/HDF5 data repositories

## What is it?

Caterva2 is a service meant for serving [Blosc2][] and [HDF5][] datasets among authenticated users, work groups, or the general public.  There are several interfaces to Caterva2, including a Web GUI, a REST API, a Python API, and a command-line client.

<img src="./doc/_static/caterva2-block-diagram.png" alt="Figure: Caterva2 block diagram" width="100%"/>

It can be used either remotely or locally, as a simple way to access datasets in a directory hierarchy, or to share them with other users in the same network.

<img src="./doc/_static/caterva2-data-sharing.png" alt="Figure: How data can be shared" width="100%"/>

The Python API is the recommended way for building your own Caterva2 clients, whereas the Web client provides a more user-friendly interface for browsing and accessing datasets.

<img src="./doc/_static/web-data-view.png" alt="Figure: Web data browser and viewer" width="100%"/>

<img src="./doc/_static/web-tomo-view.png" alt="Figure: Web viewer for tomography" width="100%"/>


[Blosc2]: https://www.blosc.org/pages/blosc-in-depth/
    "What Is Blosc? (Blosc blog)"

[HDF5]: https://www.hdfgroup.org/solutions/hdf5/
    "HDF5 (HDF Group)"

## Components of Caterva2

A Caterva2 deployment includes:

- One **broker** service to enable the communication between publishers and subscribers.
- Several **publishers**, each one providing subscribers with access to one root and the datasets that it contains. The root may be a native Caterva2 directory with Blosc2 and plain files, or an HDF5 file (support for other formats may be added).
- Several **subscribers**, each one tracking changes in multiple roots and datasets from publishers, and caching them locally for efficient reuse.
- Several **clients**, each one asking a subscriber to track roots and datasets, and provide access to their data and metadata.

Publishers and subscribers may be apart, in different networks with limited or expensive connectivity between them, while subscribers and clients will usually be close enough to have fast and cheap connectivity (e.g. a local network).

The Caterva2 package includes all the aforementioned components, although its main role is to provide a simple and lightweight library to build your own Caterva2 clients.

## Installation

You may install Caterva2 in several ways:

- Pre-built wheel from PyPI:

  ```sh
  python -m pip install caterva2
  ```

- Wheel built from source code:

  ```sh
  git clone https://github.com/ironArray/Caterva2
  cd Caterva2
  python -m build
  python -m pip install dist/caterva2-*.whl
  ```

- Developer setup:

  ```sh
  git clone https://github.com/ironArray/Caterva2
  cd Caterva2
  python -m pip install -e .
  ```

In any case, if you intend to run Caterva2 services, client programs, or the test suite, you need to enable the proper extra features by appending `[feature1,feature2...]` to the last argument of `pip` commands above.  The following extras are supported:

- `services` for running all Caterva2 services (broker, publisher, subscriber)
- `base-services` for running the Caterva2 broker or publisher services (lighter, less dependencies)
- `subscriber` for running the Caterva2 subscriber service specifically (heavier, more dependencies)
- `clients` to use Caterva2 client programs (command-line or terminal)
- `hdf5` to enable serving HDF5 files as Caterva2 roots at the publisher
- `blosc2-plugins` to enable extra Blosc2 features like Btune or JPEG 2000 support
- `plugins` to enable Web client features like the tomography display
- `tools` for additional utilities like `cat2import` and `cat2export` (see below)
- `tests` if you want to run the Caterva2 test suite

### Testing

After installing with the `[tests]` extra, you can quickly check that the package is sane by running the test suite (that comes with the package):

```sh
python -m caterva2.tests -v
```

You may also run tests from source code:

```sh
cd Caterva2
python -m pytest -v
```

Tests will use a copy of Caterva2's `root-example` directory.  After they finish, state files will be left under the `_caterva2_tests` directory for inspection (it will be re-created when tests are run again).

In case you want to run the tests with your own running daemons, you can do:

```shell
env CATERVA2_USE_EXTERNAL=1 python -m caterva2.tests -v
```

Neither `root-example` nor `_caterva2_tests` will be used in this case.

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
  needs

Then fire up the broker, start publishing a root named `foo` with `root-example` datasets, and create a subscriber:

```sh
cat2bro &  # broker
cat2pub foo root-example &  # publisher
CATERVA2_SECRET=c2sikrit cat2sub &  # subscriber
```

(To stop them later on, bring each one to the foreground with `fg` and press Ctrl+C.)

### Subscriber only mode

It's also possible to run only the subscriber. For this copy
`caterva2-standalone.sample.toml` to `caterva2.toml`.

Then only run the subscriber:

```sh
CATERVA2_SECRET=c2sikrit cat2sub &  # subscriber
```

### Using a configuration file

All the services mentioned above (and clients, to some limited extent) may get their configuration from a `caterva2.toml` file at the current directory (or an alternative file given with the `--conf` option).  Caterva2 source code includes a fully documented `caterva2.sample.toml` file (see also [caterva2.toml](caterva2.toml) in Caterva2 tutorials).

### User authentication

The Caterva2 subscriber includes some support for authenticating users.  To enable it, run the subscriber with the environment variable `CATERVA2_SECRET` set to some non-empty, secure string that will be used for various user management operations.  After that, accessing the subscriber's Web client will only be possible after logging in with an email address and a password.  New accounts may be registered, but their addresses are not verified.  Password recovery does not work either.

To create a user, you can use the `cat2adduser` command line client. For example:

```sh
cat2adduser user@example.com foobar11
```

To tell the command line client to authenticate against a subscriber, add the `--username` and `--password` options:

```sh
cat2cli --user "user@example.com" --pass "foobar11" info foo/README.md
```

### The command line client

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

Let's ask our local subscriber to subscribe to the `@shared` root:

```sh
cat2cli --username user@example.com --password foobar11 subscribe @shared  # -> Ok
```

Now, one can list the datasets in the `@shared` root:

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

### Docs

Go to the [Caterva2 documentation](https://ironarray.io/caterva2-doc/index.html) for more information on how to use Caterva2, including tutorials, API references, and examples.

That's all folks!
