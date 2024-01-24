# Tutorials

## Launching Caterva2 services

To do anything useful with Caterva2, you need at least a running broker, publisher (with some datasets) and subscriber.  For the following tutorials we'll run our own services in the local machine, with the publisher serving some example files included in the Caterva2 package.

First of all, you need to install Caterva2 with the `services` extra:

```sh
python -m pip install caterva2[services]
```

The easiest way to run a set of services with example datasets is to launch the services used by tests:

```sh
python -m caterva2.tests.services
```

This will run a broker, a publisher and a subscriber listening for HTTP requests on `localhost:8000`, `localhost:8001` and `localhost:8002` respectively.  They will put their private files under the `_caterva2` directory, respectively in `bro`, `pub` and `sub`.  Moreover, the publisher will be serving a root called `foo`, whose datasets sit in `_caterva2/data`.  You may want to browse that directory.

Since this terminal will be used by services to output their logs, you will need to run other commands in other terminals.  When you want to stop the services, go back to their terminal and press Ctrl+C (this should work for any service in sections below).

## Using the plain client API

Let's try Caterva2's plain client API.  After starting Caterva2 services (see above), run your Python interpreter and enter:

```python
import caterva2

roots = caterva2.get_roots()
```

We just connected to the default subscriber at `localhost:8002` (you may specify a different one as an argument) and asked about all roots known by the broker.  If you print `roots` you'll see a dictionary with a `foo` entry:

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

If the dataset is big and well compressed, and Blosc2 is available at the client, including the `prefer_schunk=True` argument may save resources when transferring data between subscriber and client.

Finally, you may want to save the whole dataset locally:

```python
caterva2.download('foo/dir1/ds-2d.b2nd')
```

The call downloads the dataset as a file and returns its local path `PosixPath('foo/dir1/ds-2d.b2nd')`, which should be similar to the dataset name.

## Using the object-oriented client API

The plain client API is simple but not very pythonic.  Fortunately, Caterva2 also provides a light and concise object-oriented client API (similar to that of h5py).

First, let's create a `caterva2.Root` instance for the `foo` root (using the default subscriber -- remember to start your Caterva2 services first):

```python
foo = caterva2.Root('foo')
```

This also takes care of subscribing to `foo` if it hasn't been done yet.  To get the list of datasets in the root, just access `foo.node_list`:

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

## Using the command-line client

For quick queries to a subscriber or for use in shell scripts, Caterva2 ships the `cat2cli` program.  To use it, you need to install Caterva2 with the `clients` extra:

```sh
python -m pip install caterva2[clients]
```

To ask the default subscriber (use the `--host` option for a different one) about all roots known by the broker, use the `roots` command:

```sh
cat2cli roots  # -> foo (subscribed)
```

Though it reports `foo` as subscribed (from previous sections), you may still use `subscribe` to subscribe to it (again):

```sh
cat2cli subscribe foo  # -> Ok
```

Now, to get the list of datasets in the `foo` root, use the `list` command:

```sh
cat2cli list foo  # -> foo/ds-1d.b2nd foo/ds-hello.b2frame ...
```

Please note that dataset names are already prefixed with the root name, so that you may copy them into other `cat2cli` commands, for instance `info` to get the metadata of a dataset:

```sh
cat2cli info foo/dir1/ds-2d.b2nd
```

This shows the familiar metadata, in JSON format:

```json
{
    'dtype': 'uint16',
    'ndim': 2,
    'shape': [10, 20],
    // ...
    'size': 400
}
```

In fact, the `--json` option after most commands forces JSON output, in case you need it.

To show a (part of) a dataset, you may use the `show` command, which accepts a Python-like slice after the dataset name (without spaces).  For the same slice of `ds-2d.b2nd` from previous examples, you may run:

```sh
cat2cli show --json foo/dir1/ds-2d.b2nd[0:2,4:8]
```

And you'll get a textual representation of the slice:

```
[[ 4  5  6  7]
 [24 25 26 27]]
```

Data retrieved by `show` cannot be saved locally; for that you'll need to get the whole dataset with `download`:

```sh
cat2cli download foo/dir1/ds-2d.b2nd
```

Again, the program reports the path of the resulting local file.

## Running independent Caterva2 services

The services that we used til now are enough for testing, but not for a real deployment.  For instance, they only listen to local connections, and they use example data and fixed directories.

In this section we'll setup a more realistic deployment for a fictional organization:

- A broker in host `broker.example.org`.
- Two publishers in host `pub.lab.example.org` at a data collection laboratory, serving a different root each.
- A subscriber in host `sub.edu.example.org` at a research & education branch.
- A custom API client in a workstation at the latter branch.

The broker, publisher and subscriber hosts need a Caterva2 installation with the `services` extra:

```sh
python -m pip install caterva2[services]
```

The workstation should be fine with a plain installation, but we'll also install the `clients` extra to perform quick tests with `cat2cli`:

```sh
python -m pip install caterva2[clients]
```

(If you're going to try this tutorial on a single machine, just install `caterva2[services,clients]`.)

### Broker

Our example broker shall listen on port 3104 of host `broker.example.org`.  In that host, it may be run like this:

```sh
cat2bro --http *:3104
```

The broker will create a `_caterva2/bro` directory for its state files and listen in all network interfaces.  Let's restrict that to just the public interface, and set the directory to `cat2-bro`.  Stop the broker with Ctrl+C and run this (using the host name of your machine or `localhost`):

```sh
cat2bro --http broker.example.org:3104 --statedir ./cat2-bro
```

(The ``./`` is not needed, but it shows that the `--statedir` option allows both relative and absolute paths, not necessarily under the current directory.)

Caterva2 tools allow you to store settings in a TOML configuration file called `caterva2.toml` in the current directory.  Create that file and enter the equivalent configuration:

```toml
[broker]
http = "broker.example.org:3104"
statedir = "./cat2-bro"
```

You may now stop the broker and run it with just:

```sh
cat2bro
```

The configuration allows other settings like `loglevel`.  See [caterva2.sample.toml](https://github.com/Blosc/Caterva2/blob/main/caterva2.sample.toml) in Caterva2's source for all possible settings and their purpose.

### Publishers

Here we will setup in the `pub.lab.example.org` host two publishers, each serving one of the roots which we shall name `foo` and `bar`.  We'll create their respective directories with the (arbitrary but meaningful) names `foo-root` and `bar-root`, with simple text files inside:

```sh
mkdir foo-root
echo "This is the foo root." > foo-root/readme.txt
mkdir bar-root
echo "This is the bar root." > bar-root/readme.txt
```

Here we want to run both publishers from the same directory to keep things at hand.  Publishers also support a `caterva2.toml` configuration file, and we may use a `[publisher]` section in the file.  But which publisher would use it?  We can't share the same settings for both!

Fortunately we may add an arbitrary identifier to distinguish services of the same category.  With that, we may have a `caterva2.toml` file like this:

```toml
[publisher.foo]
http = "pub.lab.example.org:3115"
statedir = "./cat2-pub.foo"
name = "foo"
root = "./foo-root"

[publisher.bar]
http = "pub.lab.example.org:3116"
statedir = "./cat2-pub.bar"
name = "bar"
root = "./bar-root"
```

We also chose arbitrary ports and state directories like those we used with the broker.  Now we can run subscribers like this (in different shells, both from the directory where `caterva2.toml` is):

```sh
cat2pub --id foo
cat2pub --id bar
```

However they'll fail to connect to the broker (a "Connection refused" error).  You need to specify the correct broker's address, either with the `--broker` option or an `http` setting in the `[broker]` section of `caterva2.toml`.  Let's add this in there and run both publishers again:

```toml
[broker]
http = "broker.example.org:3104"
```

The publishers will now work and register their respective roots at the broker.

(Yes, if you're running the broker and publishers from the same directory of the same machine, the publishers will get the broker's address from its `caterva2.toml` configuration section.)

### Subscriber

TODO

### Client

TODO
