.PHONY: install bro pub-dir pub-color pub-gris sub

BIN = ./venv/bin

install:
	python -m venv venv
	${BIN}/pip install -U pip
	${BIN}/pip install -e .
	${BIN}/pip install -e .[services,clients]
	${BIN}/pip install -e .[tests]

bro:
	${BIN}/python -m caterva2.services.bro --statedir=var/bro

pub-dir:
	${BIN}/python -m caterva2.services.pub --statedir=var/pub-dir foo root-example --http=localhost:8010

pub-color:
	${BIN}/python -m caterva2.services.pub --statedir=var/pub-color color numbers-default.h5 --http=localhost:8011

pub-gris:
	${BIN}/python -m caterva2.services.pub --statedir=var/pub-gris gris numbers-10x-gris-3d.h5 --http=localhost:8012

sub:
	${BIN}/python -m caterva2.services.sub --statedir=var/sub
