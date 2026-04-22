from __future__ import annotations

import unicodedata


def clean(text: str) -> str:
    """Normalize unicode and strip lone surrogates so the string is safe for UTF-8 encoding."""
    if not text:
        return text
    normalized = unicodedata.normalize("NFC", text)
    # encode with surrogatepass to handle existing surrogates, decode with replace to drop them
    return normalized.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
