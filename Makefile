.PHONY: install bro pub sub tests-start tests-run test-stop

BIN = ./venv/bin

install:
	python -m venv venv
	${BIN}/pip install -U pip
	${BIN}/pip install -r requirements.txt
	${BIN}/pip install -r requirements-test.txt
	mkdir -p data

bro:
	${BIN}/python src/bro.py #--loglevel=INFO

pub:
	${BIN}/python src/pub.py foo data #--loglevel=INFO

sub:
	${BIN}/python src/sub.py --loglevel=INFO
