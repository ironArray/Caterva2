# Using the client APIs

To follow these instructions, make sure that you have installed ``caterva2`` and ``blosc2``.

## The client API

Let's try Caterva2's client API using the demo at `https://cat2.cloud/demo` (you may specify a different one via the `urlbase` argument).  First import the necessary packages in your Python interpreter:

```python
import caterva2 as cat2
```

One must access to a server, either local or remote, to use the client API. Here we're going to use the demo server, available at `https://cat2.cloud/demo`. One may obtain (read-only) access without authentication to the `@public` root:

```python
client = cat2.Client("https://cat2.cloud/demo")
client.get_roots()
# {'@public': {'name': '@public', 'http': '', 'subscribed': True}}
```
or, if one has created a user, using the user credentials:

```python
client = cat2.Client("https://cat2.cloud/demo", ("user@example.com", "password1"))
client.get_roots()
# {'@public': {'name': '@public', 'http': '', 'subscribed': True},
# '@personal': {'name': '@personal', 'http': '', 'subscribed': True},
# '@shared': {'name': '@shared', 'http': '', 'subscribed': True}}
```
which gives (read/write) access to three roots (`@public`, `@personal` and `@shared`).
We may get a list of datasets in the `@public` root for `client` via `client.get_list("@public")`:

```python
datasets = client.get_list("@public")
print(datasets)
# ['examples/README.md', 'examples/Wutujing-River.jpg',..., 'examples/tomo-guess-test.b2nd']
```
We can also introduce a local variable pointing to the dataset hosted on the server - without actually downloading any data - via the following command:

```python
client.get("@public/examples/tomo-guess-test.b2nd")
# <Dataset: @public/examples/tomo-guess-test.b2nd>
```
Note how we identify the dataset by using a slash `/` to concatenate the root name with the dataset name in that root (which may contain slashes itself). We may access metadata about the dataset via:
```python
metadata = client.get_info("@public/examples/tomo-guess-test.b2nd")
```
The `metadata` dictionary contains assorted dataset attributes:

```python
print(metadata)
# {'shape': [10, 100, 100], 'chunks': [10, 100, 100], 'blocks': [2, 100, 100], 'dtype': 'uint16',
# 'schunk': {...}, 'mtime': '2025-04-06T22:00:03.912156Z'}
```
So `@public/examples/tomo-guess-test.b2nd` is a `(10,100,100)` dataset of 16-bit unsigned integers. The `schunk` field contains information about the compression of the data. We can get the whole dataset (or just a part of it) and decompress it to a `numpy` array like so:

```python
myslice = client.fetch(
    "@public/examples/tomo-guess-test.b2nd", slice_=(slice(5), slice(10, 12), slice(3))
)
print(myslice)
# array([51003, 51103], dtype=uint16)
```
Finally, one may download and save a file locally, or upload a local file to the server, using the `download` and `upload`commands:

```python
client.download(
    "@public/examples/tomo-guess-test.b2nd", "mylocalfile.b2nd"
)  # saves local file as mylocalfile.b2nd
client.upload(
    "mylocalfile.b2nd", "@public/uploadedfile.b2nd"
)  # uploads local file to @public/uploadedfile.b2nd
```

## The object-oriented client API

The top level client API is simple but not very pythonic.  Fortunately, Caterva2 also provides a light and concise object-oriented client API (fully described in [](ref-API-Root), [](ref-API-File) and  [](ref-API-Dataset)), similar to that of h5py.

First, let's create a `caterva2.Root` instance defining a local variable pointing to the root, using the `client` object we created above, which has already been authenticated. We can then print out the list of datasets in the root using `.file_list`:

```python
myroot = client.get("@public")
print(myroot.file_list)
# ['examples/README.md', 'examples/Wutujing-River.jpg',..., 'examples/tomo-guess-test.b2nd']
```

Indexing the `Root` instance with the name of the dataset results in a `Dataset` instance (or `File`, as we'll see below).  The instance offers easy access to its metadata via the `meta` attribute:

```python
ds = myroot["examples/tomo-guess-test.b2nd"]
ds.meta
# {'shape': [10, 100, 100], 'chunks': [10, 100, 100], 'blocks': [2, 100, 100], 'dtype': 'uint16',
# 'schunk': {...}, 'mtime': '2025-04-06T22:00:03.912156Z'}
```

Getting data from the dataset is very concise, as `caterva2.Dataset` instances support slicing notation, so this expression:

```python
myslice = ds[5, 10:12, 3]
# array([51003, 51103], dtype=uint16)
```
results in the same slice as the (much more verbose) `client.fetch()` call in the previous section.
Alternatively, to avoid decompressing the data, one may use the `.slice` command in the following way:

```python
ds.slice((slice(5), slice(10, 12), slice(3)))
# <blosc2.ndarray.NDArray at 0x7fba88180950>
```
returning a `blosc2.NDArray` or `blosc2.SChunk` depending on the original file type.
Finally, you may download and upload files in the following way:

```python
ds.download("mylocalfile.b2nd")  # saves local file as mylocalfile.b2nd
myroot.upload(
    "mylocalfile.b2nd", "uploadedfile.b2nd"
)  # uploads local file to @public/uploadedfile.b2nd
```

### On datasets and files

The type of instance that you get from indexing a `Root` depends on the kind of the named dataset: for datasets whose name ends in `.b2nd` (`n`-dimensional `blosc2.NDArray`) or `.b2frame` (byte string in a `blosc2` frame) you'll get a `Dataset`, while otherwise you'll get a `File` (non-``blosc2`` data).  Both classes support the same indexing operations:

```python
print(type(ds[0:2, 4:8]))  # -> <class 'numpy.ndarray'>
print(type(ds.slice((slice(0, 2), slice(4, 8)))))  # -> <class 'blosc2.ndarray.NDArray'>
print(type(myroot["examples/ds-hello.b2frame"][:10]))  # -> <class 'bytes'>
print(
    type(myroot["examples/ds-hello.b2frame"].slice(slice(0, 10)))
)  # -> <class 'blosc2.schunk.SChunk'>
print(type(myroot["examples/README.md"][:10]))  # -> <class 'bytes'>
print(
    type(myroot["examples/README.md"].slice(slice(0, 10)))
)  # -> <class 'blosc2.schunk.SChunk'>
```

In addition, `Dataset` supports direct access to `dtype`, `blocks`, `chunks` and `shape` attributes via the dot notation (`File' instances do not possess these attributes):
```python
ds.shape  # -> (10, 100, 100)
```
### Evaluating expressions
Caterva2 also allows you to create so-called "lazy expressions" (`blosc2.LazyExpr` instances), which represent computations on array datasets ("operands") accessible via the server.  These expressions are stored in the user's own personal space (`@personal`), and are evaluated on the server when you access them.  The result of the expression is a new dataset, which is also stored in the user's personal space.  The operands are not copied, but rather referenced by the expression, so they are not duplicated in the server's storage.

Lazy expressions are very cheap to create as, on creation, they merely check the metadata of the involved operands to determine if the expression is valid.  The result is not computed on creation of the `LazyExpr``, and rather only executes server-side when the data itself is accessed (e.g. via fetch or download operations). In addition, if only a portion of the data is requested, the expression is only computed for the relevant slice.

This code creates a lazy expression named `plusone` from the 2D dataset used above and stores it in the `@personal` root.

```python
client.lazyexpr(
    "plusone", "x + 1", {"x": "@public/examples/tomo-guess-test.b2nd"}
)  # -> PurePosixPath('@personal/plusone.b2nd')
```
The path of the new dataset is returned.  Now you can access it as a normal dataset, via either the `Client` or object-oriented interfaces discussed above.

```python
client.fetch("@personal/plusone.b2nd", slice_=(slice(0, 2), slice(4, 8)))
```
