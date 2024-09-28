from caterva2 import utils


def main():
    conf = utils.get_conf('subscriber', allow_id=True)
    _stdir = '_caterva2/sub' + (f'.{conf.id}' if conf.id else '')
    parser = utils.get_parser(statedir=conf.get('.statedir', _stdir), id=conf.id)
    parser.add_argument('username')
    parser.add_argument('password', nargs='?')
    parser.add_argument('--superuser', action='store_true', default=False)
    args = utils.run_parser(parser)

    statedir = args.statedir.resolve()
    user = utils.create_user(args.username, args.password, args.superuser, state_dir=statedir)
    print('Password:', user.password)


if __name__ == '__main__':
    main()
