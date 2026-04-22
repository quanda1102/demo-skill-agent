#!/usr/bin/env python3
"""
URL to Markdown Converter

Fetches HTML from a URL and converts it to clean markdown.
Uses stdlib only: urllib.request, html.parser, re
"""

import sys
import os
import json
import re
import urllib.request
import urllib.error
from html.parser import HTMLParser
from pathlib import Path


class MarkdownConverter(HTMLParser):
    """Convert HTML to markdown preserving structure."""
    
    def __init__(self):
        super().__init__()
        self.result = []
        self.tag_stack = []
        self.link_refs = {}
        self._in_code_block = False
        self._code_block_lang = ""
        self._in_pre = False
        self._in_anchor = False
        self._current_href = ""
        self._current_alt = ""
        self._link_counter = 0
    
    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        if tag == 'h1':
            self.result.append("\n## ")
        elif tag == 'h2':
            self.result.append("\n### ")
        elif tag == 'h3':
            self.result.append("\n#### ")
        elif tag == 'h4':
            self.result.append("\n##### ")
        elif tag == 'h5':
            self.result.append("\n###### ")
        elif tag == 'h6':
            self.result.append("\n###### ")
        elif tag == 'p':
            self.result.append("\n\n")
        elif tag == 'br':
            self.result.append("  \n")
        elif tag == 'hr':
            self.result.append("\n---\n")
        elif tag == 'a':
            self._in_anchor = True
            self._current_href = attrs_dict.get('href', '')
        elif tag == 'img':
            src = attrs_dict.get('src', '')
            alt = attrs_dict.get('alt', '')
            if src:
                self.result.append(f"![{alt}]({src})")
        elif tag == 'code':
            if self._in_pre:
                self.result.append("`")
            else:
                self.result.append("`")
        elif tag == 'pre':
            self._in_pre = True
            self.result.append("\n```\n")
        elif tag == 'blockquote':
            self.result.append("\n> ")
        elif tag == 'ul':
            self.result.append("\n")
        elif tag == 'ol':
            self.result.append("\n")
        elif tag == 'li':
            if self.tag_stack and self.tag_stack[-1] == 'ol':
                self.result.append("\n1. ")
            else:
                self.result.append("\n- ")
        elif tag == 'strong' or tag == 'b':
            self.result.append("**")
        elif tag == 'em' or tag == 'i':
            self.result.append("*")
        elif tag == 'del' or tag == 's':
            self.result.append("~~")
        elif tag == 'table':
            self.result.append("\n")
        elif tag == 'tr':
            self.result.append("|")
        elif tag == 'th' or tag == 'td':
            self.result.append(" | ")
        
        self.tag_stack.append(tag)
    
    def handle_endtag(self, tag):
        if self.tag_stack and self.tag_stack[-1] == tag:
            self.tag_stack.pop()
        
        if tag == 'h1':
            self.result.append("\n")
        elif tag == 'h2' or tag == 'h3' or tag == 'h4' or tag == 'h5' or tag == 'h6':
            self.result.append("\n")
        elif tag == 'a':
            href = self._current_href
            text = ''.join(self.result).split('\n')[-1].split('>')[-1].split('<')[0] if self.result else ''
            if href and not href.startswith('#'):
                self.result.append(f"]({href})")
            self._in_anchor = False
            self._current_href = ""
        elif tag == 'code':
            self.result.append("`")
        elif tag == 'pre':
            self._in_pre = False
            self.result.append("\n```\n")
        elif tag == 'blockquote':
            self.result.append("\n")
        elif tag == 'strong' or tag == 'b':
            self.result.append("**")
        elif tag == 'em' or tag == 'i':
            self.result.append("*")
        elif tag == 'del' or tag == 's':
            self.result.append("~~")
        elif tag == 'tr':
            self.result.append("|\n")
    
    def handle_data(self, data):
        if self._in_anchor and self._current_href:
            # Check if this looks like a link text (not a URL)
            if not (data.startswith('http://') or data.startswith('https://')):
                self.result.append(data)
        else:
            self.result.append(data)
    
    def get_markdown(self):
        return ''.join(self.result).strip()


def fetch_html(url):
    """Fetch HTML content from URL."""
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; URL-to-Markdown Bot)'}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status != 200:
                print(f"Error: HTTP {response.status} response", file=sys.stderr)
                sys.exit(1)
            content = response.read().decode('utf-8', errors='replace')
            if not content.strip():
                print("Warning: Empty HTML response received", file=sys.stderr)
                sys.exit(1)
            return content
    except urllib.error.URLError as e:
        print(f"Error fetching URL: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def convert_to_markdown(html_content):
    """Convert HTML to markdown."""
    # Remove script and style tags to avoid unwanted content
    html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove HTML comments
    html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)
    
    # Decode common HTML entities
    html_content = html_content.replace('&nbsp;', ' ')
    html_content = html_content.replace('&amp;', '&')
    html_content = html_content.replace('&lt;', '<')
    html_content = html_content.replace('&gt;', '>')
    html_content = html_content.replace('&quot;', '"')
    html_content = html_content.replace('&#39;', "'")
    
    converter = MarkdownConverter()
    try:
        converter.feed(html_content)
        return converter.get_markdown()
    except Exception as e:
        print(f"Error converting HTML: {e}", file=sys.stderr)
        sys.exit(1)


def save_markdown(content, output_path):
    """Save markdown content to file."""
    try:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return str(path)
    except Exception as e:
        print(f"Error saving file: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    url = None
    output_path = "./output.md"
    
    # Check for command line arguments first
    if len(sys.argv) > 1:
        url = sys.argv[1]
    if len(sys.argv) > 2:
        output_path = sys.argv[2]
    
    # Check stdin for JSON input
    if not url:
        try:
            stdin_data = sys.stdin.read().strip()
            if stdin_data:
                data = json.loads(stdin_data)
                url = data.get('url')
                output_path = data.get('output_path', output_path)
        except json.JSONDecodeError:
            pass
    
    # Validate URL
    if not url:
        print("Error: URL is required", file=sys.stderr)
        sys.exit(1)
    
    # Basic URL validation
    if not (url.startswith('http://') or url.startswith('https://')):
        print("Error: Invalid URL format", file=sys.stderr)
        sys.exit(1)
    
    # Fetch and convert
    html_content = fetch_html(url)
    markdown_content = convert_to_markdown(html_content)
    
    # Save and report
    saved_path = save_markdown(markdown_content, output_path)
    print(f"Saved: {saved_path}")


if __name__ == "__main__":
    main()