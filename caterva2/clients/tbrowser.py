###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

# Minimal example on browsing a tree of datasets/files

import pathlib

from textual.app import App, ComposeResult
from textual.widgets import Tree

# Project
from caterva2 import api, api_utils, utils


class TreeApp(App):

    def __init__(self, args):
        super().__init__()
        self.root = args.root
        auth_cookie = None
        if args.username and args.password:
            user_auth = {"username": args.username, "password": args.password}
            auth_cookie = api_utils.get_auth_cookie(args.urlbase, user_auth)
        api.subscribe(args.root, args.urlbase, auth_cookie=auth_cookie)
        self.data = api.get_list(args.root, args.urlbase,
                                 auth_cookie=auth_cookie)

    def compose(self) -> ComposeResult:
        path = self.root / pathlib.Path(self.data[0])
        root, _ = path.parts
        tree: Tree[dict] = Tree(root)
        tree.root.expand()
        datasets = tree.root.add("Datasets", expand=True)
        files = tree.root.add("Files", expand=True)
        for dataset in self.data:
            path = self.root / pathlib.Path(dataset)
            _, *parts = path.parts
            if dataset.endswith((".b2nd", ".b2frame")):
                datasets.add_leaf("/".join(parts))
            else:
                files.add_leaf("/".join(parts))
        yield tree


def main():
    conf = utils.get_conf()
    parser = utils.get_parser()
    parser.add_argument('--subscriber',
                        dest='urlbase', type=utils.urlbase_type,
                        default=conf.get('subscriber.url',
                                         api.sub_urlbase_default))
    parser.add_argument('--username', default=conf.get('client.username'))
    parser.add_argument('--password', default=conf.get('client.password'))
    parser.add_argument('--root', default='foo')

    # Go
    args = utils.run_parser(parser)
    app = TreeApp(args)
    app.run()


if __name__ == "__main__":
    main()
