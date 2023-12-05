# Caterva2 - On demand access to remote Blosc2 data repositories

Caterva2 is a distributed system written in Python and meant for sharing Blosc2 datasets among different hosts by using a [publish–subscribe](https://en.wikipedia.org/wiki/Publish–subscribe_pattern) messaging pattern.  Here, publishers categorize datasets into root groups that are announced to the broker.  Also, every publisher exposes a REST interface that allows subscribers/clients to access the datasets.

Subscribers can access datasets on-demand and cache them locally. Additionally, cached data from a subscriber can be republished by another publisher. This is particularly useful for accessing remote datasets and sharing them within a local network, thereby optimizing communication and storage resources within work groups.


## Components of Caterva2

There are 4 elements:

- The broker. Enables communication between publishers and subscribers.
- The publisher(s). Makes datasets available to subscribers.
- The subscriber(s). Follows changes and allows the download of datasets from publishers.
- The client(s). A command line interface for the user to access the datasets, it connects
  to a subscriber.

These components have a number of requirements, which are all in the `requirements.txt`
file, so just create a virtual environment and install:

```bash
pip install -r requirements.txt
```

## Quick start

Start the broker:

```bash
python src/bro.py
```

For the purpose of this quick start, let's use the datasets within the `root-example` folder:

```bash
ls -R root-example/
```

```
dir1/        dir2/        ds-1d.b2nd   ds-hello.b2

root-example/dir1:
ds-2d.b2nd  ds-3d.b2nd

root-example/dir2:
ds-4d.b2nd
```

Start publishing `root-example` in another shell:

```bash
python src/pub.py foo root-example
```

Now, let's create a subscriber (in yet another shell):

```bash
python src/sub.py
```

### The command line client

Finally, we can use a python script (called `cli.py`) that talks to the subscriber.
It can list all the available datasets:

```bash
python src/cli.py roots
```

```
foo
```

Ask the subscriber to subscribe to changes in the `foo` root:

```bash
python src/cli.py subscribe foo
```

Now, one can list the datasets in the `foo` root:

```bash
python src/cli.py list foo
```

```
ds-hello.b2
ds-1d.b2nd
dir2/ds-4d.b2nd
dir1/ds-3d.b2nd
dir1/ds-2d.b2nd
```

We can see how the client has subscribed successfully, and the datasets appear listed in the subscriptions.

Let's ask the subscriber more info about the `foo/dir2/ds-4d.b2nd` dataset:

```bash
python src/cli.py info foo/dir2/ds-4d.b2nd
```

```
{'dtype': 'complex128', 'ndim': 4, 'shape': [2, 3, 4, 5], 'ext_shape': [2, 3, 4, 5], 'chunks': [2, 3, 4, 5], 'ext_chunks': [2, 3, 4, 5], 'blocks': [2, 3, 4, 5], 'blocksize': 1920, 'chunksize': 1920, 'schunk': {'blocksize': 1920, 'cbytes': 0, 'chunkshape': 120, 'chunksize': 1920, 'contiguous': True, 'cparams': {'codec': 5, 'typesize': 16}, 'cratio': 0.0, 'nbytes': 1920, 'typesize': 16, 'urlpath': '/Users/faltet/blosc/Caterva2/var/sub/cache/foo/dir2/ds-4d.b2nd', 'nchunks': 1}, 'size': 1920}
```

Also, get we can ask the url of a root:

```bash
python src/cli.py url foo
```

```
http://localhost:8001
```

[TODO] Allow to specify a path to a dataset in the url command.

Finally, tell the subscriber to download the dataset:

```bash
python src/cli.py download foo/dir2/ds-4d.b2nd
```

[TODO] Show where the dataset has been downloaded.

## Tests

To run the test suite first some more requirements must be installed:

```bash
pip install -r requirements-test.txt
```

And then the tests can be run:

```bash
pytest -v
```


## Use with caution

Currently, this project is just a proof of concept.  It is not meant for production use yet.
In case you are interested in Caterva2, please contact us at contact@blosc.org.

That's all folks!
