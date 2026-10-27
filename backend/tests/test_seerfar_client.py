import os
import sys
import unittest


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class SeerfarClientTests(unittest.TestCase):
    def test_client_sends_bearer_header_and_decodes_success(self):
        captured = {}

        def opener(request, timeout):
            captured['url'] = request.full_url
            captured['authorization'] = request.get_header('Authorization')
            captured['timeout'] = timeout
            return _Response(b'{"code":200,"msg":"SUCCESS","data":{"creditUsed":0}}')

        from services.seerfar_client import SeerfarClient

        result = SeerfarClient(
            'http://api.seerfar.cn', 'test-key', opener=opener,
        ).request('GET', '/open-api/quota')

        self.assertEqual(0, result['data']['creditUsed'])
        self.assertEqual('http://api.seerfar.cn/open-api/quota', captured['url'])
        self.assertEqual('Bearer test-key', captured['authorization'])
        self.assertEqual(30, captured['timeout'])

    def test_missing_key_fails_before_network(self):
        from services.seerfar_client import SeerfarClient, SeerfarConfigError

        with self.assertRaises(SeerfarConfigError):
            SeerfarClient(
                'http://api.seerfar.cn', '',
                opener=lambda *_: self.fail('network called'),
            ).request('GET', '/open-api/quota')

    def test_business_quota_error_is_not_success(self):
        from services.seerfar_client import SeerfarApiError, SeerfarClient

        with self.assertRaises(SeerfarApiError) as context:
            SeerfarClient(
                'http://api.seerfar.cn', 'test-key',
                opener=lambda *args, **kwargs: _Response(
                    b'{"code":8000,"msg":"quota exhausted","data":null}'
                ),
            ).request('GET', '/open-api/quota')

        self.assertEqual(8000, context.exception.code)


if __name__ == '__main__':
    unittest.main()
