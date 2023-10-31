##############################################################################
# PubSub for Blosc2 - Access Blosc2 (and others) format via a Pub/Sub protocol
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
##############################################################################

import utils


def list_cmd(args):
    json = utils.get(f'http://{args.host}/api/list')
    print(json)

def follow_cmd(args):
    json = utils.post(f'http://{args.host}/api/follow', args.topics)
    print(json)

def unfollow_cmd(args):
    json = utils.post(f'http://{args.host}/api/unfollow', args.topics)
    print(json)

def download_cmd(args):
    data = utils.get(f'http://{args.host}/api/{args.topic}/download')
    print(data)

if __name__ == '__main__':
    parser = utils.get_parser()
    parser.add_argument('--host', default='localhost:8002')
    subparsers = parser.add_subparsers(required=True)

    # List
    subparser = subparsers.add_parser('list')
    subparser.set_defaults(func=list_cmd)

    # Follow
    subparser = subparsers.add_parser('follow')
    subparser.add_argument('topics', action='append', default=[])
    subparser.set_defaults(func=follow_cmd)

    # Unfollow
    subparser = subparsers.add_parser('unfollow')
    subparser.add_argument('topics', action='append', default=[])
    subparser.set_defaults(func=unfollow_cmd)

    # Download
    subparser = subparsers.add_parser('download')
    subparser.add_argument('topic')
    subparser.set_defaults(func=download_cmd)

    # Go
    args = utils.run_parser(parser)
    args.func(args)
