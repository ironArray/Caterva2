import utils


if __name__ == '__main__':
    parser = utils.get_parser()
    parser.add_argument('--host', default='localhost:8002')
    parser.add_argument('--add', action='append', default=[])
    args = utils.run_parser(parser)

    # Subscribe to new channels
    topics = args.add
    response = utils.post(f'http://{args.host}/topics', topics)

#   # List
#   print('List of resources...')
#   json = utils.get(f'http://{args.host}/publishers')
#   print(json)
