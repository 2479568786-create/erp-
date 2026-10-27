"""Small Seerfar Open API client used by the Ozon selection module."""
import json
import os
import urllib.error
import urllib.request


class SeerfarConfigError(RuntimeError):
    """Raised when the server-side Seerfar configuration is incomplete."""


class SeerfarApiError(RuntimeError):
    """Raised for transport, HTTP, or Seerfar business errors."""

    def __init__(self, message, status_code=None, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SeerfarClient:
    def __init__(self, base_url=None, api_key=None, opener=None, timeout=30):
        self.base_url = (base_url or os.environ.get(
            'SEERFAR_API_BASE_URL', 'http://api.seerfar.cn'
        )).rstrip('/')
        self.api_key = api_key if api_key is not None else os.environ.get(
            'SEERFAR_API_KEY', ''
        )
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def request(self, method, path, payload=None):
        if not self.api_key:
            raise SeerfarConfigError('Seerfar API Key 未配置')

        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            f'{self.base_url}/{path.lstrip("/")}',
            data=body,
            method=method.upper(),
        )
        request.add_header('Authorization', f'Bearer {self.api_key}')
        request.add_header('Accept', 'application/json')
        if body is not None:
            request.add_header('Content-Type', 'application/json')

        try:
            with self.opener(request, timeout=self.timeout) as response:
                response_body = response.read()
                status_code = getattr(response, 'status', 200)
        except urllib.error.HTTPError as exc:
            raise SeerfarApiError(
                f'Seerfar 请求失败 HTTP {exc.code}', status_code=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SeerfarApiError('Seerfar 请求失败，请检查网络连接') from exc

        try:
            result = json.loads(response_body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SeerfarApiError('Seerfar 返回了无效 JSON', status_code=status_code) from exc

        if not isinstance(result, dict):
            raise SeerfarApiError('Seerfar 返回格式无效', status_code=status_code)

        code = result.get('code')
        if status_code >= 400 or code != 200:
            message = result.get('msg') or f'Seerfar 请求失败 HTTP {status_code}'
            raise SeerfarApiError(message, status_code=status_code, code=code)
        return result
