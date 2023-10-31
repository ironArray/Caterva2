# PubSub for Blosc2 - Access Blosc2 (and others) datasets via a Pub/Sub pattern

bpubsub is a distributed system meant for sharing Blosc2 datasets among different hosts by using a [publish–subscribe](https://en.wikipedia.org/wiki/Publish–subscribe_pattern) messaging pattern.  Here, publishers categorize datasets into groups that are received by subscribers.

## Quick start

Start the broker:

```bash
python src/bro.py
```
Start publishing something in another shell:

```bash
mkdir data
echo "Shared contents" > data/shared.txt
python src/pub.py foo data
```

We have just created a data directory shared via the group ``foo``, and dropped a `shared.txt` file which is automatically published.

Now, let's create a subscriber agent (in yet another shell):

```bash
python src/sub.py
```

Finally, we can use a python script (called `cli.py`) that directs the agent to subscribe to the ``foo`` group:

```bash
python src/cli.py follow foo
python src/cli.py list
```

```
['foo']
```

We can see how the client has subscribed successfully, and ``foo`` appear listed in the subscriptions.

Finally, let's download the text file that has been published:

```bash
python src/cli.py download foo/shared.txt
```

```
Shared contents
```

This is just the starting point.  In the future, bpubsub will offer the possibility to publish, and share Blosc2 datasets (and other formats) in different group classes, and accessible from other hosts via TCP/IP and REST interfaces, and using partial caching when possible.

In case you are interested in this project, please contact us at contact@blosc.org.

That's all folks!
