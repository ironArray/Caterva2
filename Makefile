.PHONY: install bro pub sub tests-start tests-run test-stop

BIN = ./venv/bin

install:
	python -m venv venv
	${BIN}/pip install -U pip
	${BIN}/pip install -r requirements.txt
	${BIN}/pip install -r requirements.d/test.txt
	mkdir -p data

bro:
	${BIN}/python src/bro.py --var=var/bro

pub:
	${BIN}/python src/pub.py --var=var/pub foo root-example

sub:
	${BIN}/python src/sub.py --var=var/sub
