from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler


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


class OzonImageRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_ozon_image_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)
