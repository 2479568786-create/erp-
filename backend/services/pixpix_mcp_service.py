"""PixPix OAuth connection state, kept exclusively on the server."""
import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import db


class PixPixMcpService:
    MCP_ENDPOINT = 'https://api.pixpix.media/pixpix/mcp/oauth2'
    OAUTH_BASE_URL = 'https://api.pixpix.media/pixpix/oauth2'
    REQUIRED_SCOPE = 'mcp:connect offline_access'
    STATE_TTL = timedelta(minutes=10)

    def __init__(self, account_id=None):
        self.account_id = account_id

    def get_public_status(self):
        """Return only fields which are safe to send to a browser."""
        record = self._load_record()
        tokens = self._decode(record['token_json'], {}) if record else {}
        tools = self._decode(record['tools_json'], []) if record else []
        return {
            'connected': bool(tokens.get('access_token')),
            'expiresAt': record['expires_at'] if record else None,
            'toolCount': len(tools) if isinstance(tools, list) else 0,
            'updatedAt': record['updated_at'] if record else None,
        }

    def begin_authorization(self, redirect_uri):
        """Create and persist one PKCE authorization attempt."""
        if not redirect_uri:
            raise ValueError('redirect_uri is required')
        client = self._get_or_register_client(redirect_uri)
        client_id = client.get('client_id')
        if not client_id:
            raise ValueError('PixPix client registration did not return client_id')

        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode('ascii')).digest()
        ).rstrip(b'=').decode('ascii')
        now = self._now()
        self._upsert({
            'client_json': json.dumps(client),
            'oauth_state': state,
            'pkce_verifier': verifier,
            'redirect_uri': redirect_uri,
            'state_created_at': now,
            'updated_at': now,
        })
        query = urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": self.REQUIRED_SCOPE,
            "resource": self.MCP_ENDPOINT,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return f'{self.OAUTH_BASE_URL}/authorize?{query}'

    def complete_authorization(self, code, state, redirect_uri):
        """Validate the single-use state and persist a token exchange result."""
        record = self._load_record()
        if not code or not record or state != record['oauth_state']:
            raise ValueError('invalid OAuth state')
        if redirect_uri != record['redirect_uri'] or not self._state_is_fresh(record['state_created_at']):
            raise ValueError('expired OAuth state')

        client = self._decode(record['client_json'], {})
        tokens = self._exchange_code(code, redirect_uri, record['pkce_verifier'], client)
        if not tokens.get('access_token'):
            raise ValueError('PixPix token response did not contain access_token')
        expires_at = self._expires_at(tokens)
        self._upsert({
            'token_json': json.dumps(tokens),
            'oauth_state': None,
            'pkce_verifier': None,
            'redirect_uri': None,
            'state_created_at': None,
            'expires_at': expires_at,
            'updated_at': self._now(),
        })

    def list_image_tools(self):
        """Return cached image-capable MCP tools, refreshing from PixPix when connected."""
        record = self._load_record()
        tokens = self._decode(record['token_json'], {}) if record else {}
        if not tokens.get('access_token'):
            raise ValueError('PixPix 尚未连接或需要重新授权')
        result = self._mcp_request('tools/list', {})
        tools = [tool for tool in (result.get('tools') or []) if self._is_image_tool(tool)]
        self._upsert({'tools_json': json.dumps(tools), 'updated_at': self._now()})
        return tools

    def get_cached_image_tools(self):
        record = self._load_record()
        tools = self._decode(record['tools_json'], []) if record else []
        return [tool for tool in tools if self._is_image_tool(tool)]

    def call_tool(self, name, arguments):
        tools = {tool.get('name'): tool for tool in self.get_cached_image_tools()}
        if name not in tools:
            raise ValueError('请选择 PixPix 当前可用的图片工具')
        if not isinstance(arguments, dict):
            raise ValueError('工具参数必须是对象')
        return self._mcp_request('tools/call', {'name': name, 'arguments': arguments})

    def disconnect(self):
        self._upsert({
            'token_json': '{}', 'tools_json': '[]', 'oauth_state': None,
            'pkce_verifier': None, 'redirect_uri': None, 'state_created_at': None,
            'expires_at': None, 'updated_at': self._now(),
        })

    def _save_connection(self, connection):
        """Persist connection data for server-side callers and tests only."""
        tools = connection.get('tools')
        tokens = {
            key: value for key, value in connection.items()
            if key not in ('tools', 'expires_at')
        }
        self._upsert({
            'token_json': json.dumps(tokens),
            'tools_json': json.dumps(tools if tools is not None else []),
            'expires_at': connection.get('expires_at'),
            'updated_at': self._now(),
        })

    def _get_or_register_client(self, redirect_uri):
        record = self._load_record()
        client = self._decode(record['client_json'], {}) if record else {}
        if client.get('client_id'):
            return client
        return self._register_client(redirect_uri)

    def _register_client(self, redirect_uri):
        return self._post_json(f'{self.OAUTH_BASE_URL}/register', {
            'client_name': 'GeekOzon ERP',
            'redirect_uris': [redirect_uri],
            'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'],
        })

    def _exchange_code(self, code, redirect_uri, verifier, client):
        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': client.get('client_id', ''),
            'code_verifier': verifier or '',
        }
        if client.get('client_secret'):
            payload['client_secret'] = client['client_secret']
        return self._post_json(f'{self.OAUTH_BASE_URL}/token', payload)

    @staticmethod
    def _post_json(url, payload):
        request = Request(url, data=json.dumps(payload).encode('utf-8'), method='POST')
        request.add_header('Content-Type', 'application/json')
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))

    def _mcp_request(self, method, params):
        record = self._load_record()
        tokens = self._decode(record['token_json'], {}) if record else {}
        token = tokens.get('access_token')
        if not token:
            raise ValueError('PixPix 尚未连接或需要重新授权')
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
        request = Request(self.MCP_ENDPOINT, data=json.dumps(payload).encode('utf-8'), method='POST')
        request.add_header('Authorization', f'Bearer {token}')
        request.add_header('Content-Type', 'application/json')
        request.add_header('Accept', 'application/json, text/event-stream')
        try:
            with urlopen(request, timeout=120) as response:
                body = response.read().decode('utf-8')
        except HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')[:300]
            raise ValueError(f'PixPix MCP 请求失败 HTTP {exc.code}: {detail}') from exc
        except URLError as exc:
            raise ValueError(f'PixPix MCP 连接失败: {exc.reason}') from exc
        result = self._parse_mcp_response(body)
        if result.get('error'):
            message = result['error'].get('message') if isinstance(result['error'], dict) else str(result['error'])
            raise ValueError(f'PixPix MCP 返回错误: {message}')
        return result.get('result') or {}

    @staticmethod
    def _parse_mcp_response(body):
        body = (body or '').strip()
        if body.startswith('data:'):
            body = '\n'.join(line[5:].strip() for line in body.splitlines() if line.startswith('data:'))
        try:
            return json.loads(body)
        except ValueError as exc:
            raise ValueError('PixPix MCP 返回了无法识别的数据') from exc

    @staticmethod
    def _is_image_tool(tool):
        if not isinstance(tool, dict):
            return False
        text = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
        return any(keyword in text for keyword in ('image', 'generate', 'create'))

    def _load_record(self):
        if self.account_id is None:
            return db.query('SELECT * FROM pixpix_oauth WHERE id = 1', one=True)
        return db.query('SELECT * FROM pixpix_oauth_accounts WHERE account_id = ?', (self.account_id,), one=True)

    @staticmethod
    def _decode(value, fallback):
        try:
            return json.loads(value or '')
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    def _state_is_fresh(self, created_at):
        try:
            created = datetime.fromisoformat((created_at or '').replace('Z', '+00:00'))
        except ValueError:
            return False
        return datetime.now(timezone.utc) - created <= self.STATE_TTL

    def _expires_at(self, tokens):
        if tokens.get('expires_at'):
            return tokens['expires_at']
        try:
            return (datetime.now(timezone.utc) + timedelta(seconds=int(tokens['expires_in']))).isoformat().replace('+00:00', 'Z')
        except (KeyError, TypeError, ValueError):
            return None

    def _upsert(self, changes):
        columns = list(changes)
        values = [changes[column] for column in columns]
        assignments = ', '.join(f'{column} = excluded.{column}' for column in columns)
        conn = db.get_connection()
        try:
            if self.account_id is None:
                conn.execute(
                    f"INSERT INTO pixpix_oauth (id, {', '.join(columns)}) VALUES (1, {', '.join('?' for _ in columns)}) "
                    f"ON CONFLICT(id) DO UPDATE SET {assignments}", values,
                )
            else:
                conn.execute(
                    f"INSERT INTO pixpix_oauth_accounts (account_id, {', '.join(columns)}) VALUES (?, {', '.join('?' for _ in columns)}) "
                    f"ON CONFLICT(account_id) DO UPDATE SET {assignments}", [self.account_id] + values,
                )
            conn.commit()
        finally:
            conn.close()
