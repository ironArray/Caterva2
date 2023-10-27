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

    # Follow
    subparser = subparsers.add_parser('unfollow')
    subparser.add_argument('topics', action='append', default=[])
    subparser.set_defaults(func=unfollow_cmd)

    # Go
    args = utils.run_parser(parser)
    args.func(args)
