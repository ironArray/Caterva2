###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import functools
import json
import pathlib
import re

# Requirements
import httpx
import rich

# Project
from caterva2 import api_utils
from caterva2 import utils
import caterva2 as cat2


def handle_errors(func):
    def wrapper(*args):
        try:
            func(*args)
        except httpx.HTTPStatusError as error:
            response = error.response
            try:
                error = response.json()['detail']
            except json.decoder.JSONDecodeError:
                error = response.text
            print('Error:', error)

    return wrapper


def with_auth_cookie(func):
    @functools.wraps(func)
    def wrapper(args):
        auth_cookie = None
        if args.username and args.password:
            user_auth = dict(username=args.username, password=args.password)
            auth_cookie = api_utils.get_auth_cookie(args.urlbase, user_auth)
        return func(args, auth_cookie=auth_cookie)
    return wrapper


def dataset_with_slice(path):
    match = re.match('(.*)\\[(.*)]', path)
    if match is None:
        params = {}
    else:
        path, slice_ = match.groups()
        params = {'slice_': slice_}

    return pathlib.Path(path), params


@handle_errors
@with_auth_cookie
def cmd_roots(args, auth_cookie):
    data = cat2.get_roots(args.urlbase, auth_cookie=auth_cookie)
    if args.json:
        print(json.dumps(data))
        return

    for name, root in data.items():
        if root['subscribed'] is True:
            print(f'{name} (subscribed)')
        else:
            print(name)


@handle_errors
@with_auth_cookie
def cmd_subscribe(args, auth_cookie):
    data = cat2.subscribe(args.root, args.urlbase, auth_cookie=auth_cookie)
    if args.json:
        print(json.dumps(data))
        return

    print(data)


@handle_errors
@with_auth_cookie
def cmd_list(args, auth_cookie):
    data = cat2.get_list(args.root, args.urlbase, auth_cookie=auth_cookie)
    if args.json:
        print(json.dumps(data))
        return

    for item in data:
        print(f'{args.root}/{item}')


@handle_errors
@with_auth_cookie
def cmd_url(args, auth_cookie):
    data = api_utils.get_download_url(args.dataset, args.urlbase)
    if args.json:
        print(json.dumps(data))
        return

    print(data)


@handle_errors
@with_auth_cookie
def cmd_info(args, auth_cookie):
    print(f"Getting info for {args.dataset}")
    data = cat2.get_info(args.dataset, args.urlbase, auth_cookie=auth_cookie)

    # Print
    if args.json:
        print(json.dumps(data))
        return

    rich.print(data)


@handle_errors
@with_auth_cookie
def cmd_show(args, auth_cookie):
    path, params = args.dataset
    slice_ = params.get('slice_', None)
    data = cat2.fetch(path, args.urlbase, slice_=slice_,
                      auth_cookie=auth_cookie)

    # Display
    if isinstance(data, bytes):
        try:
            print(data.decode())
        except UnicodeDecodeError:
            print('Binary data')
    else:
        print(data)
        # TODO: make rich optional in command line
        # rich.print(data)


@handle_errors
@with_auth_cookie
def cmd_download(args, auth_cookie):
    path = cat2.download(args.dataset, args.urlbase, auth_cookie=auth_cookie)

    print(f'Dataset saved to {path}')


def main():
    conf = utils.get_conf()
    parser = utils.get_parser()
    parser.add_argument('--subscriber',
                        dest='urlbase', type=utils.urlbase_type,
                        default=conf.get('subscriber.url',
                                         cat2.sub_urlbase_default))
    parser.add_argument('--username', default=conf.get('client.username'))
    parser.add_argument('--password', default=conf.get('client.password'))
    subparsers = parser.add_subparsers(required=True)

    # roots
    help = 'List all the available roots in a broker.'
    subparser = subparsers.add_parser('roots', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.set_defaults(func=cmd_roots)

    # subscribe
    help = 'Request access to the datasets in a root.'
    subparser = subparsers.add_parser('subscribe', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('root')
    subparser.set_defaults(func=cmd_subscribe)

    # list
    help = 'List all the available datasets in a root. Needs to be subscribed to the root.'
    subparser = subparsers.add_parser('list', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('root')
    subparser.set_defaults(func=cmd_list)

    # url
    help = 'URL from where a dataset can be downloaded.'
    subparser = subparsers.add_parser('url', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('dataset', type=str)
    subparser.set_defaults(func=cmd_url)

    # info
    help = 'Get metadata about a dataset.'
    subparser = subparsers.add_parser('info', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('dataset', type=str)
    subparser.set_defaults(func=cmd_info)

    # show
    help = 'Display a dataset'
    subparser = subparsers.add_parser('show', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('dataset', type=dataset_with_slice)
    subparser.set_defaults(func=cmd_show)

    # download
    help = 'Download a dataset and save it in the local system'
    subparser = subparsers.add_parser('download', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('dataset', type=str)
    subparser.add_argument('output_dir', nargs='?', default='.', type=pathlib.Path)
    subparser.set_defaults(func=cmd_download)

    # Go
    args = utils.run_parser(parser)
    args.func(args)


if __name__ == '__main__':
    main()
