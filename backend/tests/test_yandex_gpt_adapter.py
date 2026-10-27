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

    def test_russian_prompt_requires_russian_only_output(self):
        from services.ai_service import AIService

        system_prompt, _ = AIService._build_prompt(
            'description', '黑色耳机耳垫', {'language': 'ru', 'platform': 'ozon'}
        )

        self.assertIn('仅使用俄语输出', system_prompt)
        self.assertIn('禁止英文', system_prompt)

    @patch('services.ai_config_service.call_text_model', return_value='ok\n\nextra')
    def test_connection_test_requests_exact_ok_response(self, call_text_model):
        result = ai_config_service.test_provider_connection(
            'yandexgpt', api_key='test-key',
            base_url='https://ai.api.cloud.yandex.net/foundationModels/v1',
            model='gpt://b1g4fd2oie4j24c3oehb/yandexgpt-lite',
        )

        prompt = call_text_model.call_args.args[1][0]['content']
        self.assertIn('只回复小写字母 ok', prompt)
        self.assertIn('"ok"', result['message'])
        self.assertNotIn('extra', result['message'])


if __name__ == '__main__':
    unittest.main()
