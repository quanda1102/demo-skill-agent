#!/usr/bin/env python3
"""Word counter: reads text from stdin and prints the word count."""

import sys


def count_words(text: str) -> int:
    """Count whitespace-separated words in text, filtering empty tokens."""
    tokens = text.split()
    return len(tokens)


def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("Usage: python scripts/run.py")
        print("Reads text from stdin and prints the word count as an integer.")
        print("Exit code 1 if input is empty; otherwise exit code 0.")
        sys.exit(0)

    text = sys.stdin.read()

    if not text or text.strip() == "":
        sys.exit(1)

    count = count_words(text)
    print(count)


if __name__ == "__main__":
    main()