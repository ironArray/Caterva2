# Using the client APIs

To follow these instructions, make sure that you have started test Caterva2 services (see [](Launching-Caterva2-services)).

## The top level client API

Let's try Caterva2's top level client API (fully described in [](ref-API-top-level)) against the default subscriber at `http://localhost:8002` (you may specify a different one via the `urlbase` argument) .  Run your Python interpreter and enter:

```python
import caterva2

roots = caterva2.get_roots()
```

**Note:** If the subscriber requires user authentication (and you get a `401 Unauthorized` error), you may first get an authorization cookie with `caterva2.api_utils.get_auth_cookie()`, then pass the returned cookie to API functions as the `auth_cookie` keyword argument.  For instance:

```python
cookie = caterva2.api_utils.get_auth_cookie(
    'http://localhost:8002',
    {'username': 'user@example.com', 'password': 'foobar11'})
roots = caterva2.get_roots(auth_cookie=cookie)
```

We just connected to the subscriber and asked about all roots known by the broker.  If you print `roots` you'll see a dictionary with a `foo` entry:

```python
{'foo': {'name': 'foo', 'http': 'localhost:8001', 'subscribed': None}}
```

Besides its name, it contains the address of the publisher providing it, and an indication that we're not subscribed to it.  Getting a list of datasets in that root with `caterva2.get_list('foo')` will fail with `404 Not Found`.  So let's try again by first subscribing to it:

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

Note how we identify the dataset by using a slash `/` to concatenate the root name with the dataset name in that root (which may contain slashes itself).  The `metadata` dictionary contains assorted dataset attributes:

```python
{'dtype': 'uint16',
 'ndim': 2,
 'shape': [10, 20],
 # ...
 'schunk': {# ...
            'cparams': {'codec': 5, # ...
                       },
            # ...
           },
 # ...
 'size': 400}
```

So `foo/dir1/ds-2d.b2nd` is a 10x20 dataset of 16-bit unsigned integers.  With `caterva2.fetch()` we can get as a NumPy array the whole dataset or just a part of it (passing a string representation of the slice that we would use between brackets as the `slice_` argument):

```python
caterva2.fetch('foo/dir1/ds-2d.b2nd', slice_='0:2, 4:8')
```

This returns just the requested slice:

```python
array([[ 4,  5,  6,  7],
       [24, 25, 26, 27]], dtype=uint16)
```

Finally, you may want to save the whole dataset locally:

```python
caterva2.download('foo/dir1/ds-2d.b2nd')
```

The call downloads the dataset as a file and returns its local path `PosixPath('foo/dir1/ds-2d.b2nd')`, which should be similar to the dataset name.

### Evaluating expressions

The Caterva2 subscriber also allows you to create so-called "lazy expressions" (lazyexprs) where operands are the array datasets accessible via the subscriber.  These expressions get stored in the user's own personal space (an always-subscribed pseudo-root named `@personal`), thus working with them requires user authentication.

Lazy expressions are very cheap to create as that operation only requires knowing the metadata of the involved operands.  The resulting data is not computed on creation, it only takes place at the subscriber when you request access to the data itself (e.g. via fetch or download operations).

This code creates a lazyexpr named `plusone` from the 2D dataset used above (check the note further above on how to get the `auth_cookie`):

```python
caterva2.lazyexpr('plusone', 'x + 1', {'x': 'foo/dir1/ds-2d.b2nd'},
                  auth_cookie=...)
```

The path of the new dataset is returned: `@personal/plusone.b2nd`.  Now you can access it as a normal dataset, e.g.:

```python
caterva2.fetch('@personal/plusone.b2nd', slice_='0:2, 4:8',
               auth_cookie=...)
```

## The object-oriented client API

The top level client API is simple but not very pythonic.  Fortunately, Caterva2 also provides a light and concise object-oriented client API (fully described in [](ref-API-Root), [](ref-API-File) and  [](ref-API-Dataset)), similar to that of h5py.

First, let's create a `caterva2.Root` instance for the `foo` root (using the default subscriber -- remember to start your Caterva2 services first):

```python
foo = caterva2.Root('foo')
```

**Note:** If the subscriber requires user authentication, you may provide credentials to the `Root` constructor with the `user_auth` keyword argument, to get authorization for further access.  For instance:

```python
foo = caterva2.Root(
    'foo',
    user_auth={'username': 'user@example.com', 'password': 'foobar'})
```

This also takes care of subscribing to `foo` if it hasn't been done yet.  To get the list of datasets in the root, just access `foo.file_list`:

```python
['ds-1d.b2nd', 'ds-hello.b2frame', 'ds-1d-b.b2nd', 'README.md',
 'dir1/ds-3d.b2nd', 'dir1/ds-2d.b2nd', 'dir2/ds-4d.b2nd']
```

Indexing the `caterva2.Root` instance with the name of the dataset results in a `caterva2.Dataset` instance (or `caterva2.File`, as we'll see below).  The instance offers easy access to its metadata via the `meta` attribute:

```python
ds2d = foo['dir1/ds-2d.b2nd']
ds2d.meta
```

We get the dataset metadata:

```python
{'dtype': 'uint16',
 'ndim': 2,
 'shape': [10, 20],
 # ...
 'size': 400}
```

Getting data from the dataset is very concise, as `caterva2.Dataset` instances support slicing notation, so this expression:

```python
ds2d[0:2, 4:8]
```

Results in the same slice as the (much more verbose) `caterva2.fetch()` call in the previous section:

```python
array([[ 4,  5,  6,  7],
       [24, 25, 26, 27]], dtype=uint16)
```

Slicing like this automatically uses Blosc2 for the transfer when available.  Finally, you may download the whole dataset like this, which also returns the path of the resulting local file:

```python
ds2d.download()  # -> PosixPath('foo/dir1/ds-2d.b2nd')
```

### On datasets and files

The type of instance that you get from indexing a `caterva2.Root` instance depends on the kind of the named dataset: for datasets whose name ends in `.b2nd` (n-dimensional Blosc2 array) or `.b2frame` (byte string in a Blosc2 frame) you'll get a `caterva2.Dataset`, while otherwise you'll get a `caterva2.File` (non-Blosc2 data).  Both classes support the same operations, with slicing only supporting one dimension and always returning a byte string for Blosc2 frames and other files:

```python
type(ds2d[0:2, 4:8])  # -> <class 'numpy.ndarray'>
type(foo['ds-hello.b2frame'][:10])  # -> <class 'bytes'>
type(foo['README.md'][:10])  # -> <class 'bytes'>
```

