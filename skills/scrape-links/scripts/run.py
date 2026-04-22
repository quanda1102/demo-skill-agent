#!/usr/bin/env python3
"""
Extract all hyperlinks (href) from a given URL.
Reads HTML from file:// or HTTP/HTTPS, parses <a> tags, returns absolute URLs.
"""

import sys
import json
import os
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser


class LinkExtractor(HTMLParser):
    """HTML parser that extracts href attributes from anchor tags."""
    
    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.links = []
        self._seen = set()
    
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for attr_name, attr_value in attrs:
                if attr_name == 'href' and attr_value:
                    # Convert relative URLs to absolute
                    absolute = urljoin(self.base_url, attr_value)
                    # Deduplicate while preserving order
                    if absolute not in self._seen:
                        self._seen.add(absolute)
                        self.links.append(absolute)


def fetch_html(url):
    """Fetch HTML content from file:// or HTTP/HTTPS URL."""
    if url.startswith('file://'):
        # Extract local file path
        file_path = url[7:]  # Remove 'file://'
        # Handle Windows paths (file:///C:/...)
        if len(file_path) > 1 and file_path[1] == ':':
            pass  # Keep as-is for Windows
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            print("File not found", file=sys.stderr)
            sys.exit(1)
        except IOError as e:
            print(f"Error reading file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # HTTP/HTTPS fetch
        try:
            from urllib.request import urlopen
            with urlopen(url, timeout=10) as response:
                # Try to detect charset from Content-Type header
                content_type = response.headers.get('Content-Type', '')
                charset = 'utf-8'
                if 'charset=' in content_type:
                    charset = content_type.split('charset=')[-1].split(';')[0].strip()
                return response.read().decode(charset)
        except Exception as e:
            print(f"Error fetching URL: {e}", file=sys.stderr)
            sys.exit(1)


def extract_links(html_content, base_url):
    """Parse HTML and extract all href attributes as absolute URLs."""
    parser = LinkExtractor(base_url)
    try:
        parser.feed(html_content)
    except Exception as e:
        print(f"Error parsing HTML: {e}", file=sys.stderr)
        sys.exit(1)
    return parser.links


def main():
    # Read URL from stdin (test framework compatibility)
    url = sys.stdin.read().strip()
    
    if not url:
        print("Usage: python run.py <url>", file=sys.stderr)
        print("<url>: HTTP/HTTPS URL or file:// path to local HTML", file=sys.stderr)
        sys.exit(1)
    
    # Determine base URL for relative link resolution
    if url.startswith('file://'):
        # Use current directory as base for relative paths
        file_path = url[7:]
        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)
        base_url = 'file://' + file_path
    elif url.startswith('http://') or url.startswith('https://'):
        base_url = url
    else:
        # Treat as file path
        if os.path.isabs(url):
            base_url = 'file://' + url
        else:
            base_url = 'file://' + os.path.abspath(url)
        url = base_url
    
    # Fetch HTML content
    html_content = fetch_html(url)
    
    # Extract links
    links = extract_links(html_content, base_url)
    
    # Output as JSON
    print(json.dumps(links))


if __name__ == '__main__':
    main()