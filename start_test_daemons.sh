#!/bin/sh
export PYTHONPATH=.
python src/bro.py &
sleep 1
python src/pub.py foo root-example &
sleep 1
python src/sub.py &
