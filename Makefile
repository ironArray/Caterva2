.PHONY: install bro pub-dir pub-color pub-gris sub

PYTHON = python3
BIN = ./venv/bin

#URL = git+https://github.com/Blosc/python-blosc2.git@main\#egg=blosc2

install:
	${PYTHON} -m venv venv
	${BIN}/pip install -U pip
	#${BIN}/pip install $(URL)
	${BIN}/pip install -e .
	${BIN}/pip install -e .[services,hdf5,plugins,blosc2-plugins]
	${BIN}/pip install -e .[clients]
	${BIN}/pip install -e .[tests]

bro:
	${BIN}/python3 -m caterva2.services.bro

pub:
	${BIN}/python3 -m caterva2.services.pub

pub-dir:
	${BIN}/python3 -m caterva2.services.pub --id dir

pub-color:
	${BIN}/python3 -m caterva2.services.pub --id color

pub-gris:
	${BIN}/python3 -m caterva2.services.pub --id gris

sub:
	BLOSC_TRACE=1 ${BIN}/python3 -m caterva2.services.sub


tests:
	CATERVA2_CONFIG=caterva2/tests/caterva2-nologin.toml pytest
	pytest --conf caterva2/tests/caterva2-nologin.toml

	pytest --conf caterva2/tests/caterva2-login.toml
