(Using-the-command-line-client)=
# Using the command-line client

For quick queries to a subscriber or for use in shell scripts, Caterva2 ships the `cat2cli` program.  To use it, you need to install Caterva2 with the `clients` extra:

```sh
python -m pip install caterva2[clients]
```

Start test Caterva2 services (see [](Launching-Caterva2-services)) first.  To ask the default subscriber about all roots known by the broker, use the `roots` command:

```sh
cat2cli roots  # -> foo (subscribed)
```

**Note:** To choose a different subscriber, you may use the `--host` command-line option. To learn about the options and arguments supported by *any* Caterva2 program, just invoke it with `--help`, e.g. `cat2cli --help`.  You may also use a configuration file (see [](caterva2.toml)).

Though the previous command reports `foo` as subscribed (from previous sections), you may still use `subscribe` to subscribe to it (again):

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

This shows the familiar metadata, in Python format:

```python
{'dtype': 'uint16',
 'ndim': 2,
 'shape': [10, 20],
 # ...
 'size': 400}
```

(Many `cat2cli` commands like `info` accept a `--json` option after them to force JSON output, in case you prefer it.)

To show a (part of) a dataset, you may use the `show` command, which accepts a Python-like slice after the dataset name (without spaces).  For the same slice of `ds-2d.b2nd` from previous examples, you may run:

```sh
cat2cli show foo/dir1/ds-2d.b2nd[0:2,4:8]
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
