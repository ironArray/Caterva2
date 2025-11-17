# Notes for developers

## Preliminaries

We are using Ruff both as code formatter and as a linter.  This is automatically enforced
if you activate these as plugins for [pre-commit](https://pre-commit.com).  You can activate
the pre-commit actions by following the [instructions](https://pre-commit.com/#installation).
As the config files are already there, this essentially boils down to:

``` bash
  python -m pip install pre-commit
  pre-commit install
```

Also, for running the tests, one needs to run manually the server.
There is a `caterva2.tests.services` script that does this.

## Build CSS and JS

If you modify the CSS or JS (in the `src/` directory) you will need to setup a development
environment, you need the following software installed:

- nodejs
- jq

Then install the nodejs modules:

```shell
npm install
```

Now every time you change the CSS or JS files in the `src/` directory, you have to rebuild
the assets:

```shell
make assets
```

## Running the tests

Testing needs Caterva2 to be installed with the `tests` extra:

```sh
pip install -e ".[tests]"
```

### With managed daemons

This will start the daemons, run the tests, and shut the daemons down:

```shell
pytest
```

State files will be left in `_caterva2_tests`.

There is also a suite that tests authentication; you can run it through:

```shell
CATERVA2_SECRET=c2sikrit pytest
```

To simulate a pyodide envirnment, set the `USE_REQUESTS` environment variable:

```sh
USE_REQUESTS=1 CATERVA2_SECRET=c2sikrit pytest
```
```

## Build wheels

We are using [hatch](https://hatch.pypa.io) as the build system, so for building wheels and
package sources you can run:

```shell
hatch build
```

## Install wheels

For installing the wheels, you can run:

```shell
pip install dist/name_of_wheel_file.whl
```

Then, you can run the tests:

```shell
cd ..   # to avoid using the source code
python -m caterva2.tests -v
```

Please note that the services should be not running at this point.

## Create docs

For creating the docs, once you have installed Caterva2 you can run:

```shell
pip install -r doc/requirements.txt
sphinx-build doc doc/html
```

and the docs will appear in `doc/html`.
