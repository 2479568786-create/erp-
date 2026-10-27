from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler

from config import Config


def is_public_ozon_image_url(url):
    try:
        parsed = urlsplit(str(url or ''))
        host = (parsed.hostname or '').lower().rstrip('.')
    except ValueError:
        return False
    return parsed.scheme in ('http', 'https') and any(
        host == domain or host.endswith('.' + domain)
        for domain in ('ozone.ru', 'ozon.ru')
    )


def is_public_managed_image_url(url):
    try:
        parsed = urlsplit(str(url or ''))
        host = (parsed.hostname or '').lower().rstrip('.')
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https') or not host:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port not in (None, 80 if parsed.scheme == 'http' else 443):
        return False

    allowed_hosts = set(Config.PUBLIC_IMAGE_PROXY_HOSTS)
    if Config.OSS_BUCKET and Config.OSS_ENDPOINT:
        allowed_hosts.add(f'{Config.OSS_BUCKET}.{Config.OSS_ENDPOINT}'.lower().rstrip('.'))
    if Config.QINIU_DOMAIN:
        try:
            qiniu_host = (urlsplit(Config.QINIU_DOMAIN).hostname or '').lower().rstrip('.')
            if qiniu_host:
                allowed_hosts.add(qiniu_host)
        except ValueError:
            pass
    return host in allowed_hosts


def is_public_image_url(url):
    return is_public_ozon_image_url(url) or is_public_managed_image_url(url)


class OzonImageRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_ozon_image_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ManagedImageRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_host):
        super().__init__()
        self.allowed_host = allowed_host.lower().rstrip('.')

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            parsed = urlsplit(newurl)
            host = (parsed.hostname or '').lower().rstrip('.')
        except ValueError:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if (parsed.scheme not in ('http', 'https') or host != self.allowed_host
                or port not in (None, 80 if parsed.scheme == 'http' else 443)):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)
