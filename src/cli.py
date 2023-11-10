###############################################################################
# PubSub for Blosc2 - Access Blosc2 (and others) datasets via a Pub/Sub pattern
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import json

import utils


def list_cmd(args):
    params = {}
    if args.all:
        params['all'] = True
    data = utils.get(f'http://{args.host}/api/list', params=params)
    print(json.dumps(data))

def follow_cmd(args):
    data = utils.post(f'http://{args.host}/api/follow', args.datasets)
    print(json.dumps(data))

def unfollow_cmd(args):
    data = utils.post(f'http://{args.host}/api/unfollow', args.datasets)
    print(json.dumps(data))

def download_cmd(args):
    data = utils.post(f'http://{args.host}/api/download', args.datasets)
    print(data)

if __name__ == '__main__':
    parser = utils.get_parser()
    parser.add_argument('--host', default='localhost:8002')
    subparsers = parser.add_subparsers(required=True)

    # List
    subparser = subparsers.add_parser('list')
    subparser.add_argument('-a', '--all', action='store_true')
    subparser.set_defaults(func=list_cmd)

    # Follow
    subparser = subparsers.add_parser('follow')
    subparser.add_argument('datasets', action='append', default=[])
    subparser.set_defaults(func=follow_cmd)

    # Unfollow
    subparser = subparsers.add_parser('unfollow')
    subparser.add_argument('datasets', action='append', default=[])
    subparser.set_defaults(func=unfollow_cmd)

    # Download
    subparser = subparsers.add_parser('download')
    subparser.add_argument('datasets', action='append', default=[])
    subparser.set_defaults(func=download_cmd)

    # Go
    args = utils.run_parser(parser)
    args.func(args)
