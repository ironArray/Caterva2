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
    def __init__(self, args, conf):
        super().__init__()

        url = args.url or conf.get(".url", "http://localhost:8000")
        username = args.username or conf.get(".username")
        password = args.password or conf.get(".password")

        self.root = args.root
        auth_cookie = None
        if username and password:
            user_auth = {"username": username, "password": password}
            auth_cookie = api_utils.get_auth_cookie(url, user_auth)
        self.data = api.get_list(args.root, url, auth_cookie=auth_cookie)

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
    # Load configuration (args)
    parser = utils.get_client_parser()
    parser.add_argument("--root", default="foo")

    args = parser.parse_args()
    conf = utils.get_client_conf(args.conf)
    utils.config_log(args, conf)

    # Start client
    app = TreeApp(args, conf)
    app.run()


if __name__ == "__main__":
    main()
