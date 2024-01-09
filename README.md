# Caterva2 - On demand access to remote Blosc2 data repositories

![Figure: Caterva2 sequence diagram](./doc/_static/logo-caterva-small.png)

Caterva2 is a distributed system written in Python and meant for sharing Blosc2 datasets among different hosts by using a [publish–subscribe](https://en.wikipedia.org/wiki/Publish–subscribe_pattern) messaging pattern.  Here, publishers categorize datasets into root groups that are announced to the broker.  Also, every publisher exposes a REST interface that allows subscribers/clients to access the datasets.

Subscribers can access datasets on-demand and cache them locally. Additionally, cached data from a subscriber can be republished by another publisher. This is particularly useful for accessing remote datasets and sharing them within a local network, thereby optimizing communication and storage resources within work groups.


## Components of Caterva2

There are 4 elements:

- The broker. Enables communication between publishers and subscribers.
- The publisher(s). Makes datasets available to subscribers.
- The subscriber(s). Follows changes and allows the download of datasets from publishers.
- The client(s). A command line interface for the user to access the datasets, it connects
  to a subscriber.

These components have a number of requirements, which are all in the `pyproject.toml`
file, so just create a virtual environment and install:

```sh
pip install -e .[services,clients]
```

## Quick start

Start the broker:

```sh
python -m caterva2.services.bro
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

Start publishing `root-example` in another shell:

```sh
python -m caterva2.services.pub foo root-example
```

Now, let's create a subscriber (in yet another shell):

```sh
python -m caterva2.services.sub
```

### The command line client

Finally, we can use a python script (called `cli.py`) that talks to the subscriber.
It can list all the available datasets:

```sh
python -m caterva2.clients.cli roots
```

```
foo
```

Ask the subscriber to subscribe to changes in the `foo` root:

```sh
python -m caterva2.clients.cli subscribe foo
```

Now, one can list the datasets in the `foo` root:

```sh
python -m caterva2.clients.cli list foo
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
python -m caterva2.clients.cli info foo/dir2/ds-4d.b2nd
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
python -m caterva2.clients.cli url foo
```

```
http://localhost:8001
```

Let's print data from a specified dataset:

```sh
python -m caterva2.clients.cli show foo/ds-hello.b2frame
```

```
b'Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!Hello world!'
```

It allows printing slices instead of the whole dataset too:

```sh
python -m caterva2.clients.cli show foo/dir2/ds-4d.b2nd[:1]
```

```
[[[[ 0. +0.j  1. +1.j  2. +2.j  3. +3.j  4. +4.j]
   [ 5. +5.j  6. +6.j  7. +7.j  8. +8.j  9. +9.j]
   [10.+10.j 11.+11.j 12.+12.j 13.+13.j 14.+14.j]
   [15.+15.j 16.+16.j 17.+17.j 18.+18.j 19.+19.j]]

  [[20.+20.j 21.+21.j 22.+22.j 23.+23.j 24.+24.j]
   [25.+25.j 26.+26.j 27.+27.j 28.+28.j 29.+29.j]
   [30.+30.j 31.+31.j 32.+32.j 33.+33.j 34.+34.j]
   [35.+35.j 36.+36.j 37.+37.j 38.+38.j 39.+39.j]]

  [[40.+40.j 41.+41.j 42.+42.j 43.+43.j 44.+44.j]
   [45.+45.j 46.+46.j 47.+47.j 48.+48.j 49.+49.j]
   [50.+50.j 51.+51.j 52.+52.j 53.+53.j 54.+54.j]
   [55.+55.j 56.+56.j 57.+57.j 58.+58.j 59.+59.j]]]]
```

Finally, we can tell the subscriber to download the dataset:

```sh
python -m caterva2.clients.cli download foo/dir2/ds-4d.b2nd
```

```
Dataset saved to /.../foo/dir2/ds-4d.b2nd
```

## Tests

The tests can be run as follows:

```sh
pytest -v
```

Also, the tests comes with the package, so you can always run them as:

```sh
python -c "import caterva2 as cat2; cat2.test(verbose=True)"
```

The test publisher will use the files under `root-example`.  After tests finish, state files will be left under `_caterva2_tests` in case you want to inspect them.


## Use with caution

Currently, this project is just a proof of concept.  It is not meant for production use yet.
In case you are interested in Caterva2, please contact us at contact@blosc.org.

That's all folks!
