# Notes for developers

For the time being, the code is not very well documented, so this need to be fixed asap.

Also, for running the tests, one needs to run manually the broker, publisher and subscriber.
There is a `caterva2.tests.services` script that does this.

## Running the tests

### With managed daemons

This will start the daemons, run the tests, and shut the daemons down:

```shell
pytest
```

State files will be left in `_caterva2_tests`.

### With external daemons

To have daemons running across several test runs (for faster testing), start the daemons:

```shell
python -m caterva2.tests.services &
```

or, if you prefer:

```shell
cat2bro &
cat2pub foo root-example &
cat2sub &
```

State files will be stored in dir `_caterva2/`.

Finally, in another shell (unless you like to hear the daemons chatting), run the tests:

```shell
env CATERVA2_USE_EXTERNAL=1 python -m pytest -s
```

For stopping the daemons, you will have to kill the `caterva2.tests.services` process.
If you started them manually, you will have to kill them manually too (sorry!).

## Build wheels

We are using [hatch](https://hatch.pypa.io) as the build system, so for building wheels and
package sources you can run:

```shell
hatch build
```
