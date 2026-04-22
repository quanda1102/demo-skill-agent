#!/usr/bin/env python3
"""Note Searcher - Search markdown files for keywords."""

import json
import os
import sys


def search_notes(directory: str, keyword: str) -> list[str]:
    """Search markdown files in directory for keyword."""
    matches = []
    keyword_lower = keyword.lower()

    for root, _, files in os.walk(directory):
        for filename in files:
            if filename.endswith(".md"):
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                        if keyword_lower in content.lower():
                            matches.append(filename)
                except (OSError, UnicodeDecodeError) as e:
                    print(f"Warning: Could not read {filepath}: {e}", file=sys.stderr)

    return sorted(matches)


def main():
    if "--help" in sys.argv:
        print("Usage: echo '{\"directory\": \"path\", \"keyword\": \"term\"}' | python run.py")
        print("Searches markdown files for a keyword and outputs matching filenames.")
        sys.exit(0)

    try:
        input_data = json.loads(sys.stdin.read().strip())
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    directory = input_data.get("directory")
    keyword = input_data.get("keyword")

    if not directory or not keyword:
        print("Error: Both 'directory' and 'keyword' are required.", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(directory):
        print(f"Error: Directory not found: {directory}", file=sys.stderr)
        sys.exit(1)

    matches = search_notes(directory, keyword)

    if matches:
        print("\n".join(matches))
    else:
        print("No matches found")


if __name__ == "__main__":
    main()