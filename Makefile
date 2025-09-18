.PHONY: install assets lite-build lite-dev lite-test run

VENV = ./venv
BIN = $(VENV)/bin

install:
	python3 -m venv venv
	${BIN}/pip install -U pip
	${BIN}/pip install -e .
	${BIN}/pip install -e .[services,hdf5,plugins,blosc2-plugins]
	${BIN}/pip install -e .[clients]
	${BIN}/pip install -e .[tests]
	${BIN}/pip install pre-commit

assets:
	rm caterva2/services/static/build/*
	npm run build
	git add caterva2/services/static/build/


lite-build:
	rm .jupyterlite.doit.db caterva2/services/static/jupyterlite -rf
	${BIN}/jupyter lite build --output-dir caterva2/services/static/jupyterlite

# Installs our jupyterlite fork from a local copy, for development purposes
# Before doing this you must run "make build" in our jupyterlite fork
lite-dev:
	${BIN}/pip uninstall jupyterlite jupyterlite-core -y
	${BIN}/pip install ../jupyterlite/dist/jupyterlite_core-*.whl
	${BIN}/pip install ../jupyterlite/dist/jupyterlite-*.whl
	$(MAKE) lite-build

# Installs our jupyterlite fork from github, useful to test before deployment
lite-test:
	rm downloads -rf
	gh run -R ironArray/jupyterlite download -n "caterva2 dist" --dir downloads
	${BIN}/pip install --force-reinstall downloads/*.whl
	$(MAKE) lite-build


# To run the server, for convenience
run:
	${BIN}/python3 -m caterva2.services.server
