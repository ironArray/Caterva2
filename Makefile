.PHONY: install bro pub-dir pub-color pub-gris sub

PYTHON = python3
BIN = ./venv/bin

install:
	${PYTHON} -m venv venv
	${BIN}/pip install -U pip
	${BIN}/pip install -e .
	${BIN}/pip install -e .[services,hdf5,plugins,blosc2-plugins]
	${BIN}/pip install -e .[clients]
	${BIN}/pip install -e .[tests]

assets:
	rm caterva2/services/static/build/*
	npm run build
	git add caterva2/services/static/build/

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
