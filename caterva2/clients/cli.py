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
import random
import re
import string

# Requirements
import httpx
import rich

import caterva2 as cat2

# Project
from caterva2 import api_utils, utils


def handle_errors(func):
    def wrapper(*args):
        try:
            func(*args)
        except httpx.HTTPStatusError as error:
            response = error.response
            try:
                error = response.json()["detail"]
            except json.decoder.JSONDecodeError:
                error = response.text
            print("Error:", error)

    return wrapper


def with_auth_cookie(func):
    @functools.wraps(func)
    def wrapper(args):
        auth_cookie = None
        if args.username and args.password:
            user_auth = {"username": args.username, "password": args.password}
            auth_cookie = api_utils.get_auth_cookie(args.urlbase, user_auth)
        return func(args, auth_cookie=auth_cookie)

    return wrapper


def dataset_with_slice(path):
    match = re.match("(.*)\\[(.*)]", path)
    if match is None:
        params = {}
    else:
        path, slice_ = match.groups()
        params = {"slice_": slice_}

    return pathlib.Path(path), params


@handle_errors
@with_auth_cookie
def cmd_roots(args, auth_cookie):
    data = cat2.get_roots(args.urlbase, auth_cookie=auth_cookie)
    if args.json:
        print(json.dumps(data))
        return

    for name, root in data.items():
        if root["subscribed"] is True:
            print(f"{name} (subscribed)")
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
        print(f"{item}")


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
    slice_ = params.get("slice_", None)
    data = cat2.fetch(path, args.urlbase, slice_=slice_, auth_cookie=auth_cookie)

    # Display
    if isinstance(data, bytes):
        try:
            print(data.decode())
        except UnicodeDecodeError:
            print("Binary data")
    else:
        print(data)
        # TODO: make rich optional in command line
        # rich.print(data)


@handle_errors
@with_auth_cookie
def cmd_move(args, auth_cookie):
    moved = cat2.move(args.dataset, args.dest, args.urlbase, auth_cookie=auth_cookie)
    print(f"Dataset {args.dataset} moved to {moved}")


@handle_errors
@with_auth_cookie
def cmd_copy(args, auth_cookie):
    copied = cat2.copy(args.dataset, args.dest, args.urlbase, auth_cookie=auth_cookie)
    print(f"Dataset {args.dataset} copied to {copied}")


@handle_errors
@with_auth_cookie
def cmd_download(args, auth_cookie):
    path = cat2.download(args.dataset, args.localpath, args.urlbase, auth_cookie=auth_cookie)
    print(f"Dataset saved to {path}")


@handle_errors
@with_auth_cookie
def cmd_upload(args, auth_cookie):
    path = cat2.upload(args.localpath, args.dataset, args.urlbase, auth_cookie=auth_cookie)
    print(f"Dataset stored in {path}")


@handle_errors
@with_auth_cookie
def cmd_remove(args, auth_cookie):
    removed = cat2.remove(args.dataset, args.urlbase, auth_cookie=auth_cookie)
    print(f"Dataset (or directory contents) removed: {removed}")


@handle_errors
@with_auth_cookie
def cmd_adduser(args, auth_cookie):
    newpass = args.newpass or "".join(random.choice(string.ascii_letters) for _ in range(8))
    message = cat2.adduser(
        args.newuser,
        newpass,
        args.superuser,
        args.urlbase,
        auth_cookie=auth_cookie,
    )
    print(message)


@handle_errors
@with_auth_cookie
def cmd_deluser(args, auth_cookie):
    message = cat2.deluser(args.user, args.urlbase, auth_cookie=auth_cookie)
    print(message)


@handle_errors
@with_auth_cookie
def cmd_listusers(args, auth_cookie):
    data = cat2.listusers(args.user, args.urlbase, auth_cookie=auth_cookie)
    if args.json:
        print(json.dumps(data))
        return

    for user in data:
        print(user)


def main():
    conf = utils.get_conf()
    parser = utils.get_parser()
    parser.add_argument(
        "--subscriber",
        dest="urlbase",
        type=utils.urlbase_type,
        default=conf.get("subscriber.url", cat2.sub_urlbase_default),
    )
    parser.add_argument("--username", default=conf.get("client.username"))
    parser.add_argument("--password", default=conf.get("client.password"))
    subparsers = parser.add_subparsers(required=True)

    # roots
    help = "List all the available roots in a broker."
    subparser = subparsers.add_parser("roots", help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.set_defaults(func=cmd_roots)

    # subscribe
    help = "Request access to the datasets in a root."
    subparser = subparsers.add_parser("subscribe", help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("root")
    subparser.set_defaults(func=cmd_subscribe)

    # list
    help = "List all the available datasets in a root. Needs to be subscribed to the root."
    subparser = subparsers.add_parser("list", aliases=["ls"], help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("root")
    subparser.set_defaults(func=cmd_list)

    # copy
    help = "Copy a dataset to a different root."
    subparser = subparsers.add_parser("copy", aliases=["cp"], help=help)
    subparser.add_argument("dataset", type=pathlib.Path)
    subparser.add_argument("dest")
    subparser.set_defaults(func=cmd_copy)

    # move
    help = "Move a dataset to a different root."
    subparser = subparsers.add_parser("move", aliases=["mv"], help=help)
    subparser.add_argument("dataset", type=pathlib.Path)
    subparser.add_argument("dest")
    subparser.set_defaults(func=cmd_move)

    # remove
    help = "Remove a dataset from the subscriber."
    subparser = subparsers.add_parser("remove", aliases=["rm"], help=help)
    subparser.add_argument("dataset", type=pathlib.Path)
    subparser.set_defaults(func=cmd_remove)

    # url
    help = "URL from where a dataset can be downloaded."
    subparser = subparsers.add_parser("url", help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("dataset", type=str)
    subparser.set_defaults(func=cmd_url)

    # info
    help = "Get metadata about a dataset."
    subparser = subparsers.add_parser("info", help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("dataset", type=str)
    subparser.set_defaults(func=cmd_info)

    # show
    help = "Display a dataset."
    subparser = subparsers.add_parser("show", help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("dataset", type=dataset_with_slice)
    subparser.set_defaults(func=cmd_show)

    # download
    help = "Download a dataset and save it in the local system."
    subparser = subparsers.add_parser("download", help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("dataset", type=pathlib.Path)
    subparser.add_argument("localpath", nargs="?", default=None, type=pathlib.Path)
    subparser.set_defaults(func=cmd_download)

    # upload
    help = "Upload a local dataset to subscriber."
    subparser = subparsers.add_parser("upload", help=help)
    subparser.add_argument("localpath", type=pathlib.Path)
    subparser.add_argument("dataset", type=pathlib.Path)
    subparser.set_defaults(func=cmd_upload)

    # adduser
    help = "Add a new user."
    subparser = subparsers.add_parser("adduser", help=help)
    subparser.add_argument("--superuser", "-S", action="store_true", default=False)
    subparser.add_argument("newuser", type=str)
    subparser.add_argument("newpass", nargs="?")
    subparser.set_defaults(func=cmd_adduser)

    # deluser
    help = "Delete a user."
    subparser = subparsers.add_parser("deluser", help=help)
    subparser.add_argument("user", type=str)
    subparser.set_defaults(func=cmd_deluser)

    # listusers
    help = "List all users."
    subparser = subparsers.add_parser("listusers", aliases=["lsu"], help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("user", nargs="?")
    subparser.set_defaults(func=cmd_listusers)

    # Go
    args = utils.run_parser(parser)
    args.func(args)


if __name__ == "__main__":
    main()
