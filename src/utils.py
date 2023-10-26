import argparse
import logging

# Requirements
import httpx


def socket(string):
    host, port = string.split(':')
    port = int(port)
    return (host, port)

def get_parser(broker=None, http=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--loglevel', default='warning')
    if broker:
        parser.add_argument('--broker', default=broker)
    if http:
        parser.add_argument('--http', default=http, type=socket)
    return parser

def run_parser(parser):
    args = parser.parse_args()

    # Logging
    loglevel = args.loglevel.upper()
    logging.basicConfig(level=loglevel)

    return args

def post(url, json):
    response = httpx.post(url, json=json)
    response.raise_for_status()
    json = response.json()
    return json

def get(url):
    response = httpx.get(url)
    response.raise_for_status()
    json = response.json()
    return json
