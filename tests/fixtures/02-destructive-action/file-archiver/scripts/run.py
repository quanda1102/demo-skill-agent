#!/usr/bin/env python3
"""File archiver - moves files matching a glob pattern to an archive directory."""

import json
import os
import sys
import shutil
import glob


def main():
    if "--help" in sys.argv:
        print("Usage: echo '{\"source_dir\": \"path\", \"pattern\": \"*.ext\", \"archive_dir\": \"archive\"}' | python scripts/run.py")
        print("")
        print("Reads JSON from stdin with keys: source_dir, pattern, archive_dir")
        print("Moves files matching pattern from source_dir to archive_dir")
        print("Creates archive_dir if it doesn't exist")
        sys.exit(0)

    try:
        input_data = json.loads(sys.stdin.read().strip())
    except (json.JSONDecodeError, ValueError):
        print("Error: Invalid JSON input", file=sys.stderr)
        sys.exit(1)

    source_dir = input_data.get("source_dir")
    pattern = input_data.get("pattern")
    archive_dir = input_data.get("archive_dir")

    if not all([source_dir, pattern, archive_dir]):
        print("Error: Missing required fields (source_dir, pattern, archive_dir)", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(source_dir):
        print("Error: source_dir does not exist or is not a directory", file=sys.stderr)
        sys.exit(1)

    try:
        os.makedirs(archive_dir, exist_ok=True)
    except OSError as e:
        print(f"Error: Cannot create archive_dir: {e}", file=sys.stderr)
        sys.exit(1)

    search_pattern = os.path.join(source_dir, pattern)
    matched_files = glob.glob(search_pattern)

    matched_files = [f for f in matched_files if os.path.isfile(f)]

    archived_count = 0

    for file_path in matched_files:
        filename = os.path.basename(file_path)
        dest_path = os.path.join(archive_dir, filename)

        try:
            shutil.copy2(file_path, dest_path)
            os.remove(file_path)
            archived_count += 1
        except OSError as e:
            print(f"Error: Failed to archive {filename}: {e}", file=sys.stderr)
            continue

    print(f"archived: {archived_count} file(s)")
    sys.exit(0)


if __name__ == "__main__":
    main()