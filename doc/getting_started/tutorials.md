# Tutorials

## Launching Caterva2 services

To do anything useful with Caterva2, you need at least a running broker, publisher (with some datasets) and subscriber.  For these tutorials we'll run our own services in the local machine, with the publisher serving some example files included in the Caterva2 package.

First of all, you need to install Caterva2 with the `services` extra:

```sh
python -m pip install caterva2[services]
```

The easiest way to run a set of services with example datasets is to launch the services used by tests:

```sh
python -m caterva2.tests.services
```

This will run a broker, a publisher and a subscriber listening for HTTP requests on `localhost:8000`, `localhost:8001` and `localhost:8002` respectively.  They will put their private files under the `_caterva2_tests` directory, respectively in `bro`, `pub` and `sub`.  Moreover, the publisher will be serving a root called `foo`, whose datasets sit in `_caterva2_tests/data`.  You may want to browse that directory.

Since this terminal will be used by services to output their logs, you will need to run other commands in other terminals.  When you want to stop the services, go back to their terminal and press Ctrl+C.

## Using the Caterva2 client API

Caterva2 offers a very simple API to build your clients, so let's try it.  After starting Caterva2 services (see above), run your Python interpreter and enter:

```python
import caterva2

roots = caterva2.get_roots()
```

We just used `caterva2.get_roots()` to connect to the default subscriber at `localhost:8002` (you may specify a different one as an argument) and ask about all roots known announced by publishers to the broker.  If you print `roots` you'll see a dictionary with a `foo` entry:

```python
{'foo': {'name': 'foo', 'http': 'localhost:8001', 'subscribed': None}}
```

Besides its name, it contains the address of the publisher providing it, and an indication that we're not subscribed to it.  Trying to get a list of datasets in that root with `caterva2.get_list('foo')` will fail with `404 Not Found`.  So let's try again by first subscribing to it:

```python
caterva2.subscribe('foo')
datasets = caterva2.get_list('foo')
```

If you print `datasets` you'll see a list of datasets in the `foo` root:

```python
['ds-1d.b2nd', 'ds-hello.b2frame', 'ds-1d-b.b2nd', 'README.md',
 'dir1/ds-3d.b2nd', 'dir1/ds-2d.b2nd', 'dir2/ds-4d.b2nd']
```

(If you repeat the call to `caterva2.get_roots()` you'll see that `foo` has `subscribed=True` now.)

We can get some information about a dataset without downloading it:

```python
metadata = caterva2.get_info('foo/dir1/ds-2d.b2nd')
```

Note how we identify the dataset by using a slash `/` to concatenate the root name with the dataset name in that root (which may contain slashes itself).  The `metadata` dictionary contains assorted attributes of the dataset:

```python
{'dtype': 'uint16',
 'ndim': 2,
 'shape': [10, 20],
 ...
 'schunk': {...
            'cparams': {'codec': 5, ...},
            ...
            'nbytes': 576,
            'typesize': 2,
            ...},
 ...
 'size': 400}
```

So `foo/dir1/ds-2d.b2nd` is a 10x20 dataset of 16-bit unsigned integers.  With `caterva2.fetch()` we can get the whole dataset as a NumPy array:

```python
caterva2.fetch('foo/dir1/ds-2d.b2nd')
```

Or just a part of it, passing a string representation of the slice that we would use between brackets as the `slice_` argument:

```python
caterva2.fetch('foo/dir1/ds-2d.b2nd', slice_='0:2, 4:8')
```

The latter returns just the requested slice:

```python
array([[ 4,  5,  6,  7],
       [24, 25, 26, 27]], dtype=uint16)
```

If the dataset is big and well compressed, and Blosc2 is available in the client device, you may include the `prefer_schunk=True` argument to save resources when transferring data between subscriber and client.

Finally, you may want to save the whole dataset locally:

```python
path = caterva2.download('foo/dir1/ds-2d.b2nd')
```

The returned `path` now contains the local path of the downloaded file: `PosixPath('foo/dir1/ds-2d.b2nd')` (which should be quite similar to the dataset name).
