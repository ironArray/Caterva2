PYTHON=./venv/bin/python

install:
	python -m venv venv
	./venv/bin/pip install -U pip
	./venv/bin/pip install -r requirements.txt
	mkdir -p data

bro:
	${PYTHON} src/bro.py #--loglevel=INFO

pub:
	${PYTHON} src/pub.py foo data #--loglevel=INFO

sub:
	${PYTHON} src/sub.py foo/a #--loglevel=INFO

cli:
	${PYTHON} src/cli.py
