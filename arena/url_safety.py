"""Keep authenticated browser operations on the configured origin."""
from urllib.parse import urljoin, urlsplit


def origin(url: str) -> tuple:
    try:
        if not isinstance(url, str) or any(ord(c) < 32 or ord(c) == 127 for c in url) or '\\' in url:
            raise ValueError
        p = urlsplit(url)
        if p.scheme.lower() not in ('http', 'https') or not p.hostname or any(c.isspace() for c in p.netloc) or p.username is not None or p.password is not None:
            raise ValueError
        port = p.port
        if port == 0:
            raise ValueError
        return p.scheme.lower(), p.hostname.lower(), port if port is not None else (443 if p.scheme.lower() == 'https' else 80)
    except (TypeError, ValueError):
        raise ValueError('Expected an HTTP(S) URL without embedded credentials') from None


def validate_base_url(url: str) -> None:
    origin(url)
    p = urlsplit(url)
    if p.path not in ('', '/') or p.query or p.fragment:
        raise ValueError('The base URL must be an origin without path, query or fragment')


def same_origin_url(base: str, target: str) -> str:
    validate_base_url(base)
    if not isinstance(target, str) or any(ord(c) < 32 or ord(c) == 127 for c in target) or '\\' in target:
        raise ValueError('Invalid URL')
    url = urljoin(base.rstrip('/') + '/', target)
    if origin(url) != origin(base):
        raise ValueError('URL must use the configured origin')
    return url
