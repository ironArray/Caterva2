.PHONY: install bro pub sub tests-start tests-run test-stop

BIN = ./venv/bin

install:
	python -m venv venv
	${BIN}/pip install -U pip
	${BIN}/pip install -e .
	${BIN}/pip install -e .[test]
	mkdir -p data

bro:
	${BIN}/python src/bro.py --statedir=var/bro

pub:
	${BIN}/python src/pub.py --statedir=var/pub foo root-example
	#${BIN}/python src/pub.py --statedir=var/pub foo data

sub:
	${BIN}/python src/sub.py --statedir=var/sub
