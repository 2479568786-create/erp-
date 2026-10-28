# -*- coding: utf-8 -*-
"""R2-Fix1：商品卡俄文描述词数门槛（可配 + 自动重试 + 可降级）。

不连外部 AI：card_service._chat_json 全部 mock。
"""
import os
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from config import Config  # noqa: E402
from services import card_service  # noqa: E402


def _desc(words):
    return ' '.join(['слово'] * int(words))


def _result(words):
    return {
        'title_ru': 'Тестовый товар для дома и дачи',
        'title_zh': '测试商品',
        'description_ru': _desc(words),
        'description_zh': '测试描述',
        'keywords_ru': ['тест'],
        'keywords_zh': ['测试'],
        'main_tags_ru': ['#тест'],
        'main_tags_zh': ['#测试'],
        'long_tail_tags_ru': [],
        'long_tail_tags_zh': [],
        'auxiliary_tags_ru': [],
        'auxiliary_tags_zh': [],
        'pending_confirmations_zh': [],
        'blocked_claims_zh': [],
    }


CTX = {'title_zh': '测试商品', 'ozon_category_name': 'Электроника'}
SEO = {'keywords_ru': ['тест'], 'main_tags_ru': ['#тест'], 'long_tail_tags_ru': [], 'auxiliary_tags_ru': []}


class MinDescWordsTests(unittest.TestCase):
    def _run(self, word_seq, **kwargs):
        """按 word_seq 依次返回 mock 模型输出，返回 (card, 调用次数)。"""
        state = {'i': 0}
        seq = list(word_seq)

        def fake_chat(prompt, temperature=0.4, provider_key=None):
            idx = min(state['i'], len(seq) - 1)
            state['i'] += 1
            return _result(seq[idx]), 'lingkeai', 'banana-2.1'

        with patch.object(card_service, '_chat_json', fake_chat):
            card = card_service.generate_product_card(
                CTX, ['卖点一'], ['пункт'], SEO, **kwargs
            )
        return card, state['i']

    def test_retry_then_success(self):
        card, calls = self._run([120, 160])
        self.assertEqual(calls, 2)
        self.assertEqual(card_service._word_count(card['description_ru']), 160)
        self.assertEqual(card['_warnings'], [])

    def test_exhausted_raises_without_flag(self):
        with self.assertRaises(card_service.CardServiceError) as ctx:
            self._run([100, 110, 120])
        message = str(ctx.exception)
        self.assertIn('未达到 150 词质量要求', message)
        self.assertIn('已尝试 3 次', message)

    def test_allow_short_description_degrades(self):
        card, calls = self._run([100, 110, 120], allow_short_description=True)
        self.assertEqual(calls, 3)
        self.assertEqual(card['_warnings'], ['description_words=120<150'])
        self.assertEqual(card_service._word_count(card['description_ru']), 120)

    def test_min_desc_words_override(self):
        card, calls = self._run([60], min_desc_words=50)
        self.assertEqual(calls, 1)
        self.assertEqual(card['_warnings'], [])

    def test_config_default_is_used(self):
        with patch.object(Config, 'CARD_MIN_DESC_WORDS', 90):
            card, calls = self._run([95])
        self.assertEqual(calls, 1)
        self.assertEqual(card['_warnings'], [])

    def test_max_attempts_configurable(self):
        with patch.object(Config, 'CARD_DESC_MAX_ATTEMPTS', 1):
            with self.assertRaises(card_service.CardServiceError):
                self._run([100, 160])

    def test_invalid_min_desc_words(self):
        with self.assertRaises(card_service.CardServiceError):
            self._run([200], min_desc_words='abc')


if __name__ == '__main__':
    unittest.main()
