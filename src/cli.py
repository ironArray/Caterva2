###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import json

# Requirements
import httpx
import rich

# Project
import models
import utils



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


@handle_errors
def cmd_roots(args):
    data = utils.get(f'http://{args.host}/api/roots')
    if args.json:
        print(json.dumps(data))
        return

    for name, root in data.items():
        root = models.Root(**root)
        if root.subscribed:
            print(f'{name} (subscribed)')
        else:
            print(name)

@handle_errors
def cmd_subscribe(args):
    data = utils.post(f'http://{args.host}/api/subscribe/{args.root}')
    if args.json:
        print(json.dumps(data))
        return

    print(data)

@handle_errors
def cmd_list(args):
    data = utils.get(f'http://{args.host}/api/list/{args.root}')
    if args.json:
        print(json.dumps(data))
        return

    for item in data:
        print(f'{args.root}/{item}')

@handle_errors
def cmd_url(args):
    data = utils.get(f'http://{args.host}/api/url/{args.root}')
    if args.json:
        print(json.dumps(data))
        return

    print(data)

@handle_errors
def cmd_info(args):
    data = utils.get(f'http://{args.host}/api/info/{args.dataset}')
    if args.json:
        print(json.dumps(data))
        return

    rich.print(data)

@handle_errors
def cmd_get(args):
    data = utils.get(f'http://{args.host}/api/get/{args.dataset}')
    if args.json:
        print(json.dumps(data))
        return

    print(f'{data} %')

@handle_errors
def cmd_download(args):
    data = utils.get(f'http://{args.host}/api/get/{args.dataset}')
    if args.json:
        print(json.dumps(data))
        return

    print(data)

if __name__ == '__main__':
    parser = utils.get_parser()
    parser.add_argument('--host', default='localhost:8002')
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
    help = 'URL for the rest API that serves the root.'
    subparser = subparsers.add_parser('url', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('root')
    subparser.set_defaults(func=cmd_url)

    # info
    help = 'Get metadata about a dataset.'
    subparser = subparsers.add_parser('info', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('dataset')
    subparser.set_defaults(func=cmd_info)

    # get
    help = 'Tell the subscriber to fetch a dataset'
    subparser = subparsers.add_parser('get', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('dataset')
    subparser.set_defaults(func=cmd_get)

    # download
    help = 'Download a dataset and save it in the local system'
    subparser = subparsers.add_parser('download', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.add_argument('dataset')
    subparser.set_defaults(func=cmd_download)

    # Go
    args = utils.run_parser(parser)
    args.func(args)
