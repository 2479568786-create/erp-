import pytest

from services import card_service


def test_rejects_chinese_characters_in_russian_card_fields():
    card = {
        'title_ru': 'Наушники для игр',
        'description_ru': 'Игровые наушники с мягкими амбушюрами 中文',
        'keywords_ru': ['игровые наушники'],
        'main_tags_ru': ['игровые наушники'],
        'long_tail_tags_ru': ['наушники для PS5'],
        'auxiliary_tags_ru': [],
    }

    with pytest.raises(card_service.CardServiceError, match='中文'):
        card_service.validate_russian_card_fields(card)


def test_selling_point_translation_failure_does_not_copy_chinese_to_russian(monkeypatch):
    def fail_chat(*_args, **_kwargs):
        raise card_service.CardServiceError('AI API 连接失败')

    monkeypatch.setattr(card_service, '_chat_json', fail_chat)

    with pytest.raises(card_service.CardServiceError, match='俄语翻译失败'):
        card_service.summarize_selling_points(['黑色耳机耳垫'])
