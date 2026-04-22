#!/usr/bin/env python3
"""Echo Tool: Reads stdin and prints it back to stdout exactly as received."""

import sys


def main():
    """Read all input from stdin and print it to stdout."""
    try:
        data = sys.stdin.read()
        sys.stdout.write(data)
        sys.stdout.flush()
    except BrokenPipeError:
        # Ignore broken pipe (e.g., piped to head)
        pass
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())