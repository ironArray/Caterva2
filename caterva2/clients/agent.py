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

    # Get local files
    local_files = set()
    for item in local_dir.glob("*"):
        if item.is_file():
            local_files.add(item.name)

    # Upload missing files
    for filename in local_files - remote_files:
        try:
            file_path = str(local_dir / filename)
            client.upload(file_path, f"{remote_path}/{filename}")
            print(f"↑ Initial upload: {filename}")
        except Exception as e:
            print(f"! Error uploading {filename}: {e}")

    # Remove extra remote files
    for filename in remote_files - local_files:
        try:
            client.remove(f"{remote_path}/{filename}")
            print(f"↓ Initial removal: {filename}")
        except Exception as e:
            print(f"! Error removing {filename}: {e}")


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
    local_dir = pathlib.Path(args.directory)

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
                    filename = pathlib.Path(file_path).name
                    remote_path = f"{args.path}/{filename}"

                    try:
                        if change_type in {watchfiles.Change.added, watchfiles.Change.modified}:
                            client.upload(file_path, remote_path)
                            print(f"✓ Uploaded {filename}")
                        elif change_type == watchfiles.Change.deleted:
                            client.remove(remote_path)
                            print(f"x Removed {filename}")
                        else:
                            print(f"? Unknown change type for {filename}")
                    except Exception as e:
                        print(f"! Error processing {filename}: {e}")
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        sys.exit(0)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
