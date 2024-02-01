![Figure: Caterva2 logo](./doc/_static/logo-caterva-small.png)

# Caterva2 - On demand access to remote Blosc2 data repositories

## What is it?

Caterva2 is a distributed system written in Python meant for sharing [Blosc2][] datasets among different hosts by using a [publish–subscribe][] messaging pattern.  Here, publishers categorize datasets into root groups that are announced to the broker and propagated to subscribers.  Also, every subscriber exposes a REST interface that allows clients to access the datasets.

[Blosc2]: https://www.blosc.org/pages/blosc-in-depth/
    "What Is Blosc? (Blosc blog)"

[publish–subscribe]: https://en.wikipedia.org/wiki/Publish–subscribe_pattern
    "Publish–subscribe pattern (Wikipedia)"

Caterva2 enables on-demand data access with local caching and re-publishing, which can be particularly useful for the efficient sharing of remote datasets locally, thus optimizing communication and storage resources within work groups.

## Components of Caterva2

A Caterva2 deployment includes:

- One **broker** service to enable the communication between publishers and subscribers.
- Several **publishers**, each one providing subscribers with access to one root and the datasets that it contains.
- Several **subscribers**, each one tracking changes in multiple roots and datasets from publishers, and caching them locally for efficient reuse.
- Several **clients**, each one asking a subscriber to track roots and datasets, and provide access to their data and metadata.

Publishers and subscribers may be apart, in different networks with limited or expensive connectivity between them, while subscribers and clients will usually be close enough to have fast and cheap connectivity (e.g. a local network).  Such a setup ensures that:

- Data can be efficiently distributed among different machines or networks.
- Data is only requested from their sources on demand.
- Data is cached when possible, close to interested parties.

The Caterva2 package includes all the aforementioned components, although its main role is to provide a very simple and lightweight library to build your own Caterva2 clients.

## Use with caution

Currently, this project is in early alpha stage, and it is not meant for production use yet.  In case you are interested in Caterva2, please contact us at <contact@blosc.org>.

## Installation

You may install Caterva2 in several ways, listed below.  In any case, if you intend to run Caterva2 services, client programs, or the test suite, you need to enable the proper extra features by appending `[feature1,feature2...]` to the last argument of `pip` commands below.  For instance, to enable all extra features append `[services,clients,tests]`.

- Pre-built wheel from PyPI:

  ```sh
  python -m pip install caterva2
  ```

- Wheel built from source code:

  ```sh
  git clone https://github.com/Blosc/Caterva2
  cd Caterva2
  python -m build
  python -m pip install dist/caterva2-*.whl
  ```

- Developer setup:

  ```sh
  git clone https://github.com/Blosc/Caterva2
  cd Caterva2
  python -m pip install -e .
  ```

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

The publisher run by tests will use the files under Caterva2's `root-example` directory.  After tests finish, state files will be left under the `_caterva2_tests` directory in case you want to inspect them (it will be removed and re-created when tests are run again).

In case you want to run the tests with your own running daemons, you can do:

```shell
env CATERVA2_USE_EXTERNAL=1 python -m caterva2.tests -v
```

Neither `root-example` nor `_caterva2_tests` will be used in this case.

## Quick start

First, create a virtual environment and install Caterva2 with the `[services,clients]` extras (see above).  Then start the broker:

```sh
cat2bro &
```

For the purpose of this quick start, let's use the datasets within the `root-example` folder:

```sh
ls -R root-example/
```

```
README.md         dir1/             dir2/             ds-1d.b2nd        ds-hello.b2frame

root-example//dir1:
ds-2d.b2nd  ds-3d.b2nd

root-example//dir2:
ds-4d.b2nd
```

Start publishing `root-example` datasets:

```sh
cat2pub foo root-example &
```

Now, let's create a subscriber:

```sh
cat2sub &
```

### The command line client

Now that we have the services running, we can start using a script (called `cat2cli`) that talks
to the subscriber. Now, in another shell, let's list all the available roots in the system:

```sh
cat2cli roots
```

```
foo
```

We only have a root called `foo` (the one we started publishing). If other publishers were running,
we would see them listed here too.

Let's ask our local subscriber to subscribe to the `foo` root:

```sh
cat2cli subscribe foo
```

Now, one can list the datasets in the `foo` root:

```sh
cat2cli list foo
```

```
foo/ds-hello.b2frame
foo/README.md
foo/ds-1d.b2nd
foo/dir2/ds-4d.b2nd
foo/dir1/ds-3d.b2nd
foo/dir1/ds-2d.b2nd
```

We can see how the client has subscribed successfully, and the datasets appear listed in the subscriptions.

Let's ask the subscriber more info about the `foo/dir2/ds-4d.b2nd` dataset:

```sh
cat2cli info foo/dir2/ds-4d.b2nd
```

```
{
    'dtype': 'complex128',
    'ndim': 4,
    'shape': [2, 3, 4, 5],
    'ext_shape': [2, 3, 4, 5],
    'chunks': [2, 3, 4, 5],
    'ext_chunks': [2, 3, 4, 5],
    'blocks': [2, 3, 4, 5],
    'blocksize': 1920,
    'chunksize': 1920,
    'schunk': {
        'blocksize': 1920,
        'cbytes': 0,
        'chunkshape': 120,
        'chunksize': 1920,
        'contiguous': True,
        'cparams': {'codec': 5, 'typesize': 16},
        'cratio': 0.0,
        'nbytes': 1920,
        'typesize': 16,
        'urlpath': '/Users/faltet/blosc/Caterva2/_caterva2/sub/cache/foo/dir2/ds-4d.b2nd',
        'nchunks': 1
    },
    'size': 1920
}
```

Also, we can ask for the url of a root:

```sh
cat2cli url foo
```

```
http://localhost:8001
```

Let's print data from a specified dataset:

```sh
cat2cli show foo/ds-hello.b2frame[:12]
```

```
Hello world!
```

It allows printing slices instead of the whole dataset too:

```sh
cat2cli show foo/dir2/ds-4d.b2nd[1,2,3]
```

```
[115.+115.j 116.+116.j 117.+117.j 118.+118.j 119.+119.j]
```

Finally, we can tell the subscriber to download the dataset:

```sh
cat2cli download foo/dir2/ds-4d.b2nd
```

```
Dataset saved to foo/dir2/ds-4d.b2nd
```

### Using a configuration file

All the services mentioned above (and clients, to some limited extent) may get their configuration from a `caterva2.toml` file at the current directory (though an alternative file may be given with the `--conf` option).  Please see the `caterva2.sample.toml` file for more information.

## Tools

Caterva2 includes a simple script to export the full group and dataset hierarchy in an HDF5 file to a new Caterva2 root directory.  You may use it like:

```sh
cat2import existing-hdf5-file.h5 new-caterva2-root
```

The tool is still pretty limited in its supported input and generated output, please invoke it with `--help` for more information (see also [cat2import](cat2import) in Caterva2 utilities documentation).

That's all folks!
