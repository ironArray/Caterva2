import argparse
import pathlib
import re
import sys

import watchfiles

import caterva2 as cat2


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
    parser = argparse.ArgumentParser(description="Watch a directory and sync changes to a Caterva2 server")
    parser.add_argument("directory", type=str, help="Local directory path to watch for changes")
    parser.add_argument("url", type=str, help="Caterva2 server base URL")
    parser.add_argument(
        "path", type=validate_path, help="Remote path (format: @personal|@shared|@public/path/to/dir)"
    )
    parser.add_argument("username", type=str, help="Username for Caterva2 authentication")
    parser.add_argument("password", type=str, help="Password for Caterva2 authentication")

    args = parser.parse_args()

    # Initialize Caterva2 client
    client = cat2.Client(args.url, (args.username, args.password))
    local_dir = pathlib.Path(args.directory).absolute()  # Ensure we have absolute path

    # Initial synchronization
    print("Performing initial synchronization...")
    sync_initial_state(local_dir, args.path, client)

    # Start watching for changes
    print(f"\nWatching directory {args.directory} for changes...")
    print(f"Remote destination: {args.path} on {args.url}")
    print("Press Ctrl+C to stop\n")

    try:
        for changes in watchfiles.watch(args.directory):
            for change_type, file_path in changes:
                if not file_path.endswith("/"):  # Skip directory changes
                    try:
                        # Convert to Path object and handle both absolute and relative paths
                        file_path_obj = pathlib.Path(file_path)
                        if not file_path_obj.is_absolute():
                            file_path_obj = (local_dir / file_path_obj).resolve()

                        # Get relative path from watched directory
                        rel_path = str(file_path_obj.relative_to(local_dir))
                        remote_full_path = f"{args.path}/{rel_path}"

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
