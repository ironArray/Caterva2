.PHONY: install assets lite-dev lite-test bro pub-dir pub-color pub-gris sub

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


# Installs our jupyterlite fork from a local copy, for development purposes
lite-dev:
	# 1. install our fork of jupyterlite
	# Before doing this you must run "make build" in our jupyterlite fork
	${BIN}/pip install --force-reinstall ../jupyterlite/dist/*.whl

	# 2. Clean static files and build them again
	rm .jupyterlite.doit.db caterva2/services/static/jupyterlite -rf
	${BIN}/jupyter lite build --output-dir caterva2/services/static/jupyterlite

# Installs our jupyterlite fork from github, useful to test before deployment
lite-test:
	# 1. install our fork of jupyterlite
	rm downloads -rf
	gh run -R ironArray/jupyterlite download -n "caterva2 dist" --dir downloads
	${BIN}/pip install --force-reinstall downloads/*.whl

	# 2. Clean static files and build them again
	rm .jupyterlite.doit.db caterva2/services/static/jupyterlite -rf
	${BIN}/jupyter lite build --output-dir caterva2/services/static/jupyterlite


# To run the different services, for convenience
bro:
	${BIN}/python3 -m caterva2.services.bro

pub-dir:
	${BIN}/python3 -m caterva2.services.pub --id dir

pub-color:
	${BIN}/python3 -m caterva2.services.pub --id color

pub-gris:
	${BIN}/python3 -m caterva2.services.pub --id gris

sub:
	BLOSC_TRACE=1 ${BIN}/python3 -m caterva2.services.sub
