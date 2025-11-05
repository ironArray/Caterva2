###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import contextlib
import json
import os
import pathlib
import random
import re
import shlex
import string
import subprocess
import sys
import webbrowser

import blosc2

# Requirements
import httpx
import numpy as np
from rich.console import Console
from rich.syntax import Syntax

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


def dataset_with_slice(path):
    match = re.match("(.*)\\[(.*)]", path)
    if match is None:
        params = {}
    else:
        path, slice_ = match.groups()
        params = {"slice_": slice_}

    return pathlib.Path(path), params


@handle_errors
def cmd_roots(client, args, url):
    data = client.get_roots()
    if args.json:
        print(json.dumps(data))
        return

    for name in data:
        print(name)


@handle_errors
def cmd_list(client, args, url):
    data = client.get_list(args.root)
    if args.json:
        print(json.dumps(data))
        return

    for item in data:
        print(f"{item}")


# New helpers for tree command
def build_tree(paths):
    """Builds a nested dict representing directories and files from a list of paths."""
    tree = {}
    for p in paths:
        parts = p.strip("/").split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        last = parts[-1]
        # files are represented by None, directories by dict
        if last in node:
            # If an entry exists as a dict (dir) and we are inserting a file, keep dir.
            if node[last] is None:
                node[last] = None
        else:
            node[last] = None
    return tree


def _print_tree_node(node, prefix=""):
    """Recursively prints a node (dict)."""
    # Sort for deterministic output; directories and files mixed lexicographically.
    items = sorted(node.items(), key=lambda kv: kv[0])
    for idx, (name, child) in enumerate(items):
        is_last = idx == len(items) - 1
        connector = "└──" if is_last else "├──"
        print(f"{prefix}{connector} {name}")
        if isinstance(child, dict):
            extension = "    " if is_last else "│   "
            _print_tree_node(child, prefix + extension)


@handle_errors
def cmd_tree(client, args, url):
    """
    Print a hierarchical tree of datasets/files in the specified root/path.
    """
    data = client.get_list(args.root)
    if args.json:
        print(json.dumps(data))
        return

    # Build a nested representation and print it
    tree = build_tree(data)
    # Print top-level entries without a leading root label (similar to unix `tree .`)
    _print_tree_node(tree)


# url command (returns download URL)
@handle_errors
def cmd_url(client, args, url):
    data = api_utils.get_download_url(args.dataset, url)
    if args.json:
        print(json.dumps(data))
        return
    print(data)


# handle command (returns handle URL meant for browser exploration)
@handle_errors
def cmd_handle(client, args, url):
    data = api_utils.get_handle_url(args.dataset, url)
    if args.json:
        print(json.dumps(data))
        return
    print(data)


# browse command (opens local browser at the handle URL)
@handle_errors
def cmd_browse(client, args, url):
    url = api_utils.get_handle_url(args.dataset, url)
    url += "?fullscreen=1"
    # Try to open in a new browser tab; still print the URL for logging
    try:
        webbrowser.open(url, new=2)
        print(f"Opened browser at: {url}")
    except Exception:
        # Fallback: at least print the URL if opening fails
        print(url)


@handle_errors
def cmd_info(client, args, url):
    print(f"Getting info for {args.dataset}")
    data = client.get_info(args.dataset)
    if args.json:
        print(json.dumps(data))
        return

    # Helpers
    def _human_bytes(n):
        if n is None:
            return "N/A"
        n = float(n)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
            if n < 1024.0 or unit == "PiB":
                return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} {unit}"
            n /= 1024.0
        return None

    def _codec_name(cid):
        try:
            return blosc2.Codec(cid).name
        except Exception:
            return f"id({cid})"

    def _filter_names(fl):
        names = []
        for f in fl or []:
            if f == 0:
                continue
            try:
                names.append(blosc2.Filter(f).name)
            except Exception:
                names.append(f"f{f}")
        return names

    # Extract fields
    schunk = data.get("schunk") or data
    cparams = schunk.get("cparams")
    shape = data.get("shape")
    nchunks = data.get("nchunks")
    chunks = data.get("chunks")
    chunksize = data.get("chunksize")
    blocks = data.get("blocks")
    blocksize = cparams.get("blocksize")
    dtype = data.get("dtype")
    typesize = cparams.get("typesize")
    nbytes = schunk.get("nbytes") or data.get("nbytes")
    cbytes = schunk.get("cbytes") or data.get("cbytes")
    mtime = data.get("mtime")
    codec = cparams.get("codec")
    clevel = cparams.get("clevel")
    filters = cparams.get("filters")
    # Pretty print
    # print("Dataset:")
    print(f"nchunks  : {nchunks}") if shape is None else print(f"shape : {shape}")
    print(f"chunksize: {_human_bytes(chunksize)}") if chunks is None else print(f"chunks: {chunks}")
    print(f"blocksize: {_human_bytes(blocksize)}") if blocks is None else print(f"blocks: {blocks}")
    print(f"typesize : {_human_bytes(typesize)}") if dtype is None else print(f"dtype : {dtype}")
    print(f"nbytes: {_human_bytes(nbytes)}")
    print(f"cbytes: {_human_bytes(cbytes)}")
    print(f"ratio : {nbytes / cbytes:.2f}x" if nbytes and cbytes else "  ratio : N/A")
    print(f"mtime : {mtime}") if mtime is not None else print("mtime : None")
    print("cparams:")
    print(f"  codec  : {_codec_name(codec)} ({codec})")
    print(f"  clevel : {clevel}")
    if filters is not None:
        fnames = _filter_names(filters)
        print(f"  filters: [{', '.join(fnames)}]")
    else:
        print("  filters: None")


@handle_errors
def cmd_show(client, args, url):
    path, params = args.dataset
    slice_ = params.get("slice_", None)
    data = client.fetch(path, slice_=slice_)

    # JSON output requested -> convert numpy arrays to lists
    if getattr(args, "json", False):
        try:
            # If numpy array, convert to list; bytes handled below
            if isinstance(data, np.ndarray):
                print(json.dumps(data.tolist()))
            else:
                print(json.dumps(data))
        except TypeError:
            # Fallback for non-serializable objects
            print(json.dumps(str(data)))
        return

    # Display bytes as decoded text if possible, otherwise indicate binary data
    if isinstance(data, bytes):
        try:
            print(data.decode())
        except UnicodeDecodeError:
            print("Binary data")
        return

    # If numpy array, produce full (non-truncated) string, colorize and use pager if needed
    if isinstance(data, np.ndarray):
        # Determine whether color is enabled (global flag)
        color_enabled = not getattr(args, "nocolor", False)

        # Produce non-truncated representation but limit line width to ~80 cols so the output
        # is readable. Use a large threshold to avoid numpy eliding values.
        try:
            # Console used for sizing and non-forced rendering
            console = Console(color_system="truecolor" if color_enabled else None)
            # Prefer a hard 80-column target, but avoid wider-than-terminal lines:
            target_width = 80
            try:
                term_width = console.size.width or target_width
            except Exception:
                term_width = target_width
            wrap_width = min(target_width, term_width)

            array_str = np.array2string(
                data,
                threshold=sys.maxsize,
                max_line_width=wrap_width,
            )
        except Exception:
            # Fallback to repr if array2string fails for any reason
            console = Console(color_system="truecolor" if color_enabled else None)
            array_str = repr(data)

        # Re-wrap long lines at comma+space token boundaries so we never split a numeric token.
        def _wrap_preserving_tokens(s: str, width: int) -> str:
            out_lines = []
            for orig in s.splitlines():
                if len(orig) <= width:
                    out_lines.append(orig)
                    continue
                # Split at ', ' so numeric tokens remain whole
                tokens = orig.split(", ")
                line = ""
                for i, tok in enumerate(tokens):
                    sep = ", " if i < len(tokens) - 1 else ""
                    piece = tok + sep
                    if not line:
                        line = piece
                    elif len(line) + len(piece) <= width:
                        line += piece
                    else:
                        out_lines.append(line)
                        line = piece
                if line:
                    out_lines.append(line)
            return "\n".join(out_lines)

        wrapped = _wrap_preserving_tokens(array_str, wrap_width)

        # Colorize and page
        use_syntax = False
        syntax = None
        if color_enabled:
            try:
                syntax = Syntax(wrapped, "python", theme="monokai", word_wrap=False)
                use_syntax = True
            except Exception:
                syntax = None
                use_syntax = False

        # Decide whether to page: compare lines to terminal height
        lines = wrapped.count("\n") + 1
        term_height = console.size.height or 24
        if lines > term_height:
            pager_cmd = os.environ.get("PAGER", "less -R")

            # Preferred approach: run the pager subprocess and stream output to its stdin.
            try:
                args_split = shlex.split(pager_cmd)
                proc = subprocess.Popen(args_split, stdin=subprocess.PIPE, text=True)
                try:
                    if color_enabled:
                        # Use a Console that writes ANSI escapes into the pager stdin.
                        pager_console = Console(
                            file=proc.stdin, force_terminal=True, color_system="truecolor"
                        )
                        if use_syntax:
                            pager_console.print(syntax)
                        else:
                            pager_console.print(wrapped)
                    else:
                        # Plain text: write directly to pager stdin to avoid ANSI sequences.
                        proc.stdin.write(wrapped)
                finally:
                    with contextlib.suppress(Exception):
                        proc.stdin.close()
                    proc.wait()
            except Exception:
                # Fallback: try console.pager() while ensuring PAGER env var keeps -R option
                prev_pager = os.environ.get("PAGER")
                os.environ["PAGER"] = pager_cmd
                try:
                    with console.pager():
                        if color_enabled and use_syntax:
                            console.print(syntax)
                        else:
                            console.print(wrapped)
                finally:
                    if prev_pager is None:
                        del os.environ["PAGER"]
                    else:
                        os.environ["PAGER"] = prev_pager
        else:
            if color_enabled and use_syntax:
                console.print(syntax)
            else:
                console.print(wrapped)
        return

    # For other objects, fallback to plain print
    print(data)


@handle_errors
def cmd_move(client, args, url):
    moved = client.move(args.dataset, args.dest)
    print(f"Dataset {args.dataset} moved to {moved}")


@handle_errors
def cmd_copy(client, args, url):
    copied = client.copy(args.dataset, args.dest)
    print(f"Dataset {args.dataset} copied to {copied}")


@handle_errors
def cmd_download(client, args, url):
    path = client.download(args.dataset, args.localpath)
    print(f"Dataset saved to {path}")


@handle_errors
def cmd_upload(client, args, url):
    path = client.upload(args.localpath, args.dataset)
    print(f"Dataset stored in {path}")


@handle_errors
def cmd_remove(client, args, url):
    removed = client.remove(args.dataset)
    print(f"Dataset (or directory contents) removed: {removed}")


@handle_errors
def cmd_adduser(client, args, url):
    newpass = args.newpass or "".join(random.choice(string.ascii_letters) for _ in range(8))
    message = client.adduser(
        args.newuser,
        newpass,
        args.superuser,
    )
    print(message)


@handle_errors
def cmd_deluser(client, args, url):
    message = client.deluser(args.user)
    print(message)


@handle_errors
def cmd_listusers(client, args, url):
    data = client.listusers(args.user)
    if args.json:
        print(json.dumps(data))
        return

    for user in data:
        print(user)


def main():
    # Build the parser
    parser = utils.get_client_parser()

    # Make --json a global flag so it applies to all commands that support JSON output
    parser.add_argument("--json", action="store_true", help="Output JSON when supported by the command")
    subparsers = parser.add_subparsers(required=True)

    # roots
    help = "List all the available roots."
    subparser = subparsers.add_parser("roots", help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.set_defaults(func=cmd_roots)

    # list
    help = "List all the available datasets in a root."
    subparser = subparsers.add_parser("list", aliases=["ls"], help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("root")
    subparser.set_defaults(func=cmd_list)

    # tree (new)
    help = "Show a tree view of datasets/files in a root (similar to unix tree)."
    subparser = subparsers.add_parser("tree", aliases=["tr"], help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("root")
    subparser.set_defaults(func=cmd_tree)

    # copy
    help = "Copy a dataset to a different root."
    subparser = subparsers.add_parser("copy", aliases=["cp"], help=help)
    subparser.add_argument("dataset", type=pathlib.Path)
    subparser.add_argument("dest", type=pathlib.Path)
    subparser.set_defaults(func=cmd_copy)

    # move
    help = "Move a dataset to a different root."
    subparser = subparsers.add_parser("move", aliases=["mv"], help=help)
    subparser.add_argument("dataset", type=pathlib.Path)
    subparser.add_argument("dest", type=pathlib.Path)
    subparser.set_defaults(func=cmd_move)

    # remove
    help = "Remove a dataset from the server."
    subparser = subparsers.add_parser("remove", aliases=["rm"], help=help)
    subparser.add_argument("dataset", type=pathlib.Path)
    subparser.set_defaults(func=cmd_remove)

    # url
    help = "URL from where a dataset can be downloaded."
    subparser = subparsers.add_parser("url", help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("dataset", type=str)
    subparser.set_defaults(func=cmd_url)

    # handle
    help = "Handle URL (resource handle) for a dataset (returns a URL for browser exploration)."
    subparser = subparsers.add_parser("handle", help=help)
    subparser.add_argument("--json", action="store_true")
    subparser.add_argument("dataset", type=str)
    subparser.set_defaults(func=cmd_handle)

    # browse
    help = "Open a local web browser at the dataset handle URL."
    subparser = subparsers.add_parser("browse", help=help)
    subparser.add_argument("dataset", type=str)
    subparser.set_defaults(func=cmd_browse)

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
    subparser.add_argument("--nocolor", action="store_true", help="Disable ANSI color output for show")
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
    help = "Upload a local dataset to server."
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
    args = parser.parse_args()
    conf = utils.get_client_conf(args.conf, args.server)
    utils.config_log(args, conf)

    url = args.url or conf.get(".url", "http://localhost:8000")
    username = args.username or conf.get(".username")
    password = args.password or conf.get(".password")
    client = cat2.Client(url, (username, password))
    args.func(client, args, url)


if __name__ == "__main__":
    main()
