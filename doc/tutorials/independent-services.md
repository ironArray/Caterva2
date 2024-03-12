(Running-independent-Caterva2-services)=
# Running independent Caterva2 services

The services that we used til now are enough for testing, but not for a real deployment.  For instance, they only listen to local connections, and they use example data and fixed directories.

In this section we'll setup a more realistic deployment for a fictional organization:

- A broker at host `broker.example.org`.
- Two publishers at host `pub.lab.example.org` at a data collection laboratory, serving a different root each.
- A subscriber at host `sub.edu.example.org` at a research & education branch.
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

## Broker

Our example broker shall listen on port 3104 of host `broker.example.org`.  At that host, it may be run like this:

```sh
cat2bro --http *:3104
```

The broker will create a `_caterva2/bro` directory for its state files and listen in all network interfaces.  Let's restrict that to just the public interface, and set the directory to `cat2-bro`.  Stop the broker with Ctrl+C and run this (using the host name of your machine or `localhost`):

```sh
cat2bro --http broker.example.org:3104 --statedir ./cat2-bro
```

(The ``./`` is not needed, but it shows that the `--statedir` option allows both relative and absolute paths, not necessarily under the current directory.)

Let's put those options in the `caterva2.toml` configuration file:

```toml
[broker]
http = "broker.example.org:3104"
statedir = "./cat2-bro"
```

You may now stop the broker and run it with just:

```sh
cat2bro
```

## Publishers

Here we will setup at the `pub.lab.example.org` host two publishers, each serving one of the roots which we shall name `foo` and `bar`.  We'll create their respective Caterva2 directories with the (arbitrary but meaningful) names `foo-root` and `bar-root`, with simple text files inside:

```sh
mkdir foo-root
echo "This is the foo root." > foo-root/readme.txt
mkdir bar-root
echo "This is the bar root." > bar-root/readme.txt
```

Here we want to run both publishers from the same directory to keep things at hand.  To be able to share a common configuration file, we shall give different identifiers to the publishers (`foo` and `bar` for simplicity).  With that, we may have a `caterva2.toml` file like this:

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

## Subscriber

The subscriber at host `sub.edu.example.org` shall cache data from remote publishers for fast access from the research & education local network.

Subscribers also support arbitrary identifiers, but our setup won't use them as there will only be one subscriber at the host.  Use this configuration in the `caterva2.toml` file at the subscriber host:

```toml
[subscriber]
http = "sub.edu.example.org:3126"
statedir = "./cat2-sub"

[broker]
http = "broker.example.org:3104"
```

By now, everything should look familiar to you (including the custom port and state directory, and the broker address).  Please note that subscribers are configured with a broker address instead of publishers': a subscriber gets publisher addresses from their common broker as needed.

To start the subscriber, just run:

```sh
cat2sub
```

## Client setup

Clients at the example workstation need to know the address of the subscriber that they will use.

The command-line client `cat2cli` provides the `--host` option for that.  Running this at the workstation:

```sh
cat2cli --host sub.edu.example.org:3126 roots
```

Will retrieve the list of known roots from the subscriber that we set up above.  In fact, `cat2cli` also supports `caterva2.toml`, and this configuration in the current directory:

```toml
[subscriber]
http = "sub.edu.example.org:3126"
```

Should allow you to run the previous command just like this:

```sh
cat2cli roots
```

When using the programmatic API, you need to provide the subscriber address explicitly:

```python
roots = caterva2.get_roots(host='sub.edu.example.org:3126')
foo = caterva2.Root('foo', host='sub.edu.example.org:3126')
```

Since parsing TOML is very easy with Python, your API client may just access the needed configuration like this:

``` python
from tomllib import load as toml_load  # "from tomli" on Python < 3.11
with open('caterva2.toml', 'rb') as conf_file:
    conf = toml_load(conf_file)
foo = caterva2.Root('foo', host=conf['subscriber']['http'])
```
