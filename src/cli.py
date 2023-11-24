###############################################################################
# Caterva - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import json

# Project
import utils


def list_cmd(args):
    data = utils.get(f'http://{args.host}/api/list')
    if args.json:
        print(json.dumps(data))
        return

    for dataset in data:
        print(dataset)

def follow_cmd(args):
    data = utils.post(f'http://{args.host}/api/follow', args.datasets)
    print(json.dumps(data))

def info_cmd(args):
    data = utils.get(f'http://{args.host}/api/info')
    if args.json:
        print(json.dumps(data))
        return

    for path in sorted(data.keys()):
        info = data[path]
        follow = info['follow']
        mtime = info['mtime']
        downloaded = bool(mtime)
#       if mtime:
#           mtime = str(datetime.datetime.fromtimestamp(mtime))
        print(f'{path} {downloaded=} {follow=}')

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
    help = 'List the datasets available in the network'
    subparser = subparsers.add_parser('list', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.set_defaults(func=list_cmd)

    # Follow
    help = 'Follow changes to the given dataset'
    subparser = subparsers.add_parser('follow', help=help)
    subparser.add_argument('datasets', action='append', default=[])
    subparser.set_defaults(func=follow_cmd)

    # Following
    help = 'List the datasets being followed by the subscriber'
    subparser = subparsers.add_parser('info', help=help)
    subparser.add_argument('--json', action='store_true')
    subparser.set_defaults(func=info_cmd)

    # Unfollow
    help = 'Stop following changes to the given dataset'
    subparser = subparsers.add_parser('unfollow', help=help)
    subparser.add_argument('datasets', action='append', default=[])
    subparser.set_defaults(func=unfollow_cmd)

    # Download
    help = 'Tell the subscriber to download the given dataset'
    subparser = subparsers.add_parser('download', help=help)
    subparser.add_argument('datasets', action='append', default=[])
    subparser.set_defaults(func=download_cmd)

    # Go
    args = utils.run_parser(parser)
    args.func(args)
