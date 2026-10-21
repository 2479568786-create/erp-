import json
import os
import sys
import unittest
from unittest.mock import patch


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import ai_config_service


class _Response:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body.encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class YandexGptAdapterTests(unittest.TestCase):
    def test_yandex_preset_uses_folder_qualified_model(self):
        provider = ai_config_service._PRESET_PROVIDERS['yandexgpt']

        self.assertEqual('YandexGPT', provider['name'])
        self.assertEqual('gpt://b1g4fd2oie4j24c3oehb/yandexgpt-lite', provider['model'])

    @patch('urllib.request.urlopen')
    def test_yandex_request_uses_api_key_auth_and_parses_completion(self, urlopen):
        urlopen.return_value = _Response(json.dumps({
            'result': {'alternatives': [{'message': {'text': 'ok'}}]},
            'modelVersion': 'yandexgpt-lite',
        }))

        content = ai_config_service.call_text_model(
            {
                'provider': 'yandexgpt',
                'api_key': 'test-key',
                'base_url': 'https://ai.api.cloud.yandex.net/foundationModels/v1',
                'model': 'gpt://b1g4fd2oie4j24c3oehb/yandexgpt-lite',
            },
            [
                {'role': 'system', 'content': 'Return a short answer'},
                {'role': 'user', 'content': 'hello'},
            ],
            temperature=0.2,
            max_tokens=12,
        )

        self.assertEqual('ok', content)
        request = urlopen.call_args.args[0]
        self.assertEqual(
            'Api-Key test-key',
            request.get_header('Authorization'),
        )
        payload = json.loads(request.data.decode('utf-8'))
        self.assertEqual('gpt://b1g4fd2oie4j24c3oehb/yandexgpt-lite', payload['modelUri'])
        self.assertEqual(False, payload['completionOptions']['stream'])
        self.assertEqual('Return a short answer', payload['messages'][0]['text'])
        self.assertEqual('hello', payload['messages'][1]['text'])


if __name__ == '__main__':
    unittest.main()
