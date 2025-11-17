###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import argparse
import pathlib
import re
import sys

import watchfiles

import caterva2 as cat2
from caterva2 import utils


def validate_path(path: str) -> str:
    """Validate path format: must have at least 2 levels starting with @personal, @shared, or @public"""
    pattern = r"^(@personal|@shared|@public)(\/[^\/]+)+$"
    if not re.match(pattern, path):
        raise argparse.ArgumentTypeError(
            "Path must have at least 2 levels starting with @personal, @shared, or @public"
        )
    return path


def sync_initial_state(local_dir: pathlib.Path, remote_path: str, client: cat2.Client):
    """Initial synchronization between local directory and remote server"""
    # Get existing remote files
    try:
        remote_files = set(client.get_list(remote_path))
    except Exception as e:
        print(f"! Error listing remote files: {e}")
        remote_files = set()

    # Get local files with relative paths
    local_files = set()
    for item in local_dir.rglob("*"):
        if item.is_file():
            rel_path = str(item.relative_to(local_dir))
            local_files.add(rel_path)

    # Upload missing files
    for file_rel_path in local_files - remote_files:
        try:
            file_path = str(local_dir / file_rel_path)
            client.upload(file_path, f"{remote_path}/{file_rel_path}")
            print(f"↑ Initial upload: {file_rel_path}")
        except Exception as e:
            print(f"! Error uploading {file_rel_path}: {e}")

    # Remove extra remote files
    for file_rel_path in remote_files - local_files:
        try:
            client.remove(f"{remote_path}/{file_rel_path}")
            print(f"↓ Initial removal: {file_rel_path}")
        except Exception as e:
            print(f"! Error removing {file_rel_path}: {e}")


def main():
    parser = utils.get_client_parser(description="Watch a directory and sync changes to a Caterva2 server")

    parser.add_argument("localdir", type=str, help="Local directory path to watch for changes")
    parser.add_argument(
        "remotepath", type=validate_path, help="Remote path (format: @personal|@shared|@public/path/to/dir)"
    )

    # Parse arguments
    args = parser.parse_args()
    conf = utils.get_client_conf(args.conf)
    utils.config_log(args, conf)

    url = args.url or conf.get(".url", "http://localhost:8000")
    username = args.username or conf.get(".username")
    password = args.password or conf.get(".password")

    if username is None:
        raise argparse.ArgumentTypeError(
            "Missing username, either pass --username or set it in caterva2.toml"
        )

    if password is None:
        raise argparse.ArgumentTypeError(
            "Missing password, either pass --password or set it in caterva2.toml"
        )

    # Initialize Caterva2 client
    client = cat2.Client(url, (username, password))
    local_dir = pathlib.Path(args.localdir).absolute()  # Ensure we have absolute path

    # Initial synchronization
    print("Performing initial synchronization...")
    sync_initial_state(local_dir, args.remotepath, client)

    # Start watching for changes
    print(f"\nWatching directory {args.localdir} for changes...")
    print(f"Remote destination: {args.remotepath} on {args.url}")
    print("Press Ctrl+C to stop\n")

    try:
        for changes in watchfiles.watch(args.localdir):
            for change_type, file_path in changes:
                if not file_path.endswith("/"):  # Skip directory changes
                    try:
                        # Convert to Path object and handle both absolute and relative paths
                        file_path_obj = pathlib.Path(file_path)
                        if not file_path_obj.is_absolute():
                            file_path_obj = (local_dir / file_path_obj).resolve()

                        # Get relative path from watched directory
                        rel_path = str(file_path_obj.relative_to(local_dir))
                        remote_full_path = f"{args.remotepath}/{rel_path}"

                        if change_type in {watchfiles.Change.added, watchfiles.Change.modified}:
                            client.upload(str(file_path_obj), remote_full_path)
                            print(f"✓ Uploaded {rel_path}")
                        elif change_type == watchfiles.Change.deleted:
                            client.remove(remote_full_path)
                            print(f"x Removed {rel_path}")
                        else:
                            print(f"? Unknown change type for {rel_path}")
                    except Exception as e:
                        print(f"! Error processing {file_path}: {e}")
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        sys.exit(0)


if __name__ == "__main__":
    main()
