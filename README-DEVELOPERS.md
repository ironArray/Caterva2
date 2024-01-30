# Notes for developers

For the time being, the code is not very well documented, so this need to be fixed asap.

Also, for running the tests, one needs to run manually the broker, publisher and subscriber.
There is a `caterva2.tests.services` script that does this.

## Running the tests

Testing needs Caterva2 to be installed with the `tests` extra:

```sh
pip install -e .[tests]
```

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

## Install wheels

For installing the wheels, you can run:

```shell
hatch install
```

Then, you can run the tests:

```shell
cd ..   # to avoid using the source code
python -m caterva2.tests -v
```

Please note that the services should be not running at this point.  In case you want to check against
the current services, you can do:

```shell
env CATERVA2_USE_EXTERNAL=1 python -m caterva2.tests -v
```

## Create docs

For creating the docs, once you have installed Caterva2 you can run:

```shell
pip install -r doc/requirements.txt
sphinx-build doc doc/html
```

and the docs will appear in `doc/html`.
