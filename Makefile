.PHONY: install bro pub sub tests-start tests-run test-stop

BIN = ./venv/bin

install:
	python -m venv venv
	${BIN}/pip install -U pip
	${BIN}/pip install -e .
	${BIN}/pip install -e .[services,clients]
	${BIN}/pip install -e .[test]

bro:
	${BIN}/python -m caterva2.services.bro --statedir=var/bro

pub:
	${BIN}/python -m caterva2.services.pub --statedir=var/pub foo root-example

pub2:
	${BIN}/python -m caterva2.services.pub --statedir=var/pub bar root-example

sub:
	${BIN}/python -m caterva2.services.sub --statedir=var/sub
