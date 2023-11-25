# Caterva - On demand access to remote Blosc2 data repositories

Caterva is a distributed system written in Python and meant for sharing Blosc2 datasets among different hosts by using a [publish–subscribe](https://en.wikipedia.org/wiki/Publish–subscribe_pattern) messaging pattern.  Here, publishers categorize datasets into groups that are received by subscribers.

The goal is that subscribers/clients can access datasets on demand, and that publishers can share datasets without having to know who is going to use them.  At the same time, data cached by a subscriber can be re-shared again by another publisher. This is useful for e.g. accessing remote datasets and share them in a local network, where other subscribers/clients can be present, allowing to save communications and storage resources in work groups.

Overarching this is the broker, that provides the infrastructure allowing publishers and subscribers to communicate.  Publishers and subscribers are clients of the broker, and they communicate with it using the [Pub/Sub messaging pattern](https://en.wikipedia.org/wiki/Publish–subscribe_pattern).  Also, every publisher exposes a REST interface that allows subscribers/clients to access the datasets.

## Components of Caterva

There are 4 programs:

- The broker. Enables communication between publishers and subscribers.
- The publisher(s). Makes datasets available to subscribers.
- The subscriber(s). Follows changes and allows the download of datasets from publishers.
- The client(s). A command line interface for the user to access the datasets, it connects
  to a subscriber.

These programs have a number of requirements, which are all in the `requirements.txt`
file, so just create a virtual environment and install:

```bash
pip install -r requirements.txt
```

## Quick start

Start the broker:

```bash
python src/bro.py
```

Start publishing something in another shell:

```bash
mkdir data
# Copy some Blosc2 files to data/
python src/pub.py foo data
```

We have just created a data directory shared via the group `foo`, the datasets within are
automatically published. For the purpose of this quick start let's say there are 3
datasets within the `data` folder:

```bash
ls data/
precip.b2nd  temp.b2nd  wind.b2nd
```

Now, let's create a subscriber (in yet another shell):

```bash
python src/sub.py
```

### The command line client

Finally, we can use a python script (called `cli.py`) that talks to the subscriber.
It can list all the available datasets:

```bash
python src/cli.py list
```

```
["foo/precip.b2nd", "foo/temp.b2nd", "foo/wind.b2nd"]
```

Ask the subscriber to follow changes in a dataset:

```bash
python src/cli.py follow foo/precip.b2nd
python src/cli.py info
```

```
foo/precip.b2nd
```

We can see how the client has subscribed successfully, and the dataset appears listed in
the subscriptions.

Finally, tell the subscriber to download the dataset:

```bash
python src/cli.py download foo/precip.b2nd
```

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

Currently, this projects is just a proof of concept.  In the future, Caterva will offer the possibility to publish, and share Blosc2 datasets (and other formats) in different group classes, and accessible from other hosts via TCP/IP and REST interfaces, and using partial caching (with different levels of granularity) when possible.

In case you are interested in this project, please contact us at contact@blosc.org.

That's all folks!