# -*- coding: utf-8 -*-
"""回归测试：商品卡的「辅助词」分组已彻底下线。

覆盖：
  1. 提示词不再要求生成辅助词
  2. 即使模型"不听话"仍返回辅助词，输出也必须丢弃
  3. 落库 INSERT 参数与 SQL 占位符一致（防字段清单遗漏导致 500）
  4. 历史数据里的 auxiliary_tags_* 仍能正常解码（兼容读取）
  5. 手动新建/编辑商品卡链路正常

运行：cd backend && python tests/test_no_auxiliary_tags.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 用临时库，绝不碰真实数据
import db  # noqa: E402
_TMP = tempfile.mkdtemp(prefix='cardtest_')
db.DB_PATH = os.path.join(_TMP, 'test.db')
db.init_db()

from services import card_service  # noqa: E402

fails = []
prompts = []


def check(label, cond, detail=''):
    print('[%s] %s %s' % ('PASS' if cond else 'FAIL', label, detail))
    if not cond:
        fails.append(label)


def make_fake(reply: dict):
    """伪造 AI 返回；记录收到的 prompt，便于断言提示词内容。"""
    def fake(prompt, temperature=0.3, attempts=2):
        prompts.append(prompt)
        return dict(reply), 'test-provider', 'test-model'
    return fake


CTX = {'title_zh': '车载手机支架', 'source_title': '车载手机支架', 'ozon_category_name': 'Автомобильные держатели'}
ZH = ['出风口卡扣固定', '360度旋转']
RU = ['крепление на дефлектор', 'поворот 360 градусов']

# ---------------------------------------------------------------- 1 & 2
print('=== 1. SEO 关键词：模型仍返回辅助词时必须丢弃 ===')
card_service._chat_json = make_fake({
    'keywords_ru': ['держатель телефона %d' % i for i in range(25)],
    'keywords_zh': ['手机支架%d' % i for i in range(25)],
    'main_tags_ru': ['#держатель'],
    'main_tags_zh': ['支架'],
    'long_tail_tags_ru': ['#держатель_в_авто'],
    'long_tail_tags_zh': ['车载支架'],
    # 故意返回营销类辅助词，模拟模型未遵守指令
    'auxiliary_tags_ru': ['#новинка', '#хит_продаж'],
    'auxiliary_tags_zh': ['新品', '爆款'],
})
seo = card_service.generate_seo_keywords(CTX, ZH, RU)
check('seo 结果不含 auxiliary_tags_ru', 'auxiliary_tags_ru' not in seo)
check('seo 结果不含 auxiliary_tags_zh', 'auxiliary_tags_zh' not in seo)
check('seo 保留主标签', bool(seo.get('main_tags_ru')), str(seo.get('main_tags_ru')))
check('seo 保留精准长尾', bool(seo.get('long_tail_tags_ru')), str(seo.get('long_tail_tags_ru')))
check('提示词未要求辅助词', '辅助词' not in prompts[-1] and 'auxiliary_tags' not in prompts[-1])

# ---------------------------------------------------------------- 3
print()
print('=== 2. 商品卡生成：同样丢弃辅助词 ===')
_DESC_160 = ' '.join(['держатель'] * 160)
card_service._chat_json = make_fake({
    'title_ru': 'Держатель телефона в автомобиль',
    'title_zh': '车载手机支架',
    'description_ru': _DESC_160,
    'description_zh': '中文描述',
    'keywords_ru': seo['keywords_ru'],
    'keywords_zh': seo['keywords_zh'],
    'main_tags_ru': seo['main_tags_ru'],
    'main_tags_zh': seo['main_tags_zh'],
    'long_tail_tags_ru': seo['long_tail_tags_ru'],
    'long_tail_tags_zh': seo['long_tail_tags_zh'],
    # 同样故意返回营销类辅助词
    'auxiliary_tags_ru': ['#новинка', '#хит_продаж'],
    'auxiliary_tags_zh': ['新品', '爆款'],
})
card = card_service.generate_product_card(CTX, ZH, RU, seo)
check('card 不含 auxiliary_tags_ru', 'auxiliary_tags_ru' not in card)
check('card 不含 auxiliary_tags_zh', 'auxiliary_tags_zh' not in card)
check('card 保留主题标签字段', 'main_tags_ru' in card and 'long_tail_tags_ru' in card)
check('第3步提示词未要求辅助词',
      '辅助词' not in prompts[-1] and 'auxiliary_tags' not in prompts[-1])

# ---------------------------------------------------------------- 4
print()
print('=== 3. 落库往返（校验 INSERT 参数与 SQL 占位符一致）===')
card['description_ru'] = ' '.join(['слово'] * 160)   # 满足 150 词要求（此处直接落库不走校验）
saved = card_service.save_generated_card('prod_test_1', card, provider='test', model='m')
check('AI 卡落库成功', bool(saved) and saved.get('id'), 'id=%s' % (saved or {}).get('id'))
check('落库返回不含 auxiliary_tags_ru', 'auxiliary_tags_ru' not in (saved or {}))

read_back = card_service.get_card(saved['id'])
check('读回成功且字段完整', bool(read_back and read_back.get('title_ru')))
check('读回保留主标签数组', isinstance(read_back.get('main_tags_ru'), list),
      str(read_back.get('main_tags_ru')))

# ---------------------------------------------------------------- 5
print()
print('=== 4. 历史数据：库里保留，但接口不再外泄 ===')
db.execute(
    "UPDATE product_cards SET auxiliary_tags_ru = ?, auxiliary_tags_zh = ? WHERE id = ?",
    ('["#новинка","#хит_продаж"]', '["新品"]', saved['id']),
)
legacy = card_service.get_card(saved['id'])
check('接口不再返回 auxiliary_tags_ru（营销词不外泄）', 'auxiliary_tags_ru' not in legacy)
check('接口不再返回 auxiliary_tags_zh', 'auxiliary_tags_zh' not in legacy)
raw_row = db.query('SELECT auxiliary_tags_ru FROM product_cards WHERE id = ?',
                   (saved['id'],), one=True)
check('数据库仍保留历史值（可追溯、未破坏数据）',
      bool(raw_row and raw_row.get('auxiliary_tags_ru')), str(raw_row))

# ---------------------------------------------------------------- 6
print()
print('=== 5. 手动新建 / 编辑链路 ===')
manual = card_service.save_manual_card('prod_test_1', {
    'title_ru': 'Ручной заголовок',
    'main_tags_ru': ['#ручной'],
    'long_tail_tags_ru': ['#ручной_тег'],
    'auxiliary_tags_ru': ['#новинка'],   # 前端已不再提交，这里模拟老前端或脏数据
})
check('手动卡落库成功', bool(manual) and manual.get('id'))
check('手动卡不返回辅助词字段', 'auxiliary_tags_ru' not in (manual or {}))

updated = card_service.update_card(manual['id'], {'title_ru': 'Обновлено', 'main_tags_ru': ['#обн']})
check('手动卡编辑成功', updated.get('title_ru') == 'Обновлено', str(updated.get('title_ru')))

# ---------------------------------------------------------------- 7
print()
print('=== 6. 上架取值不受影响 ===')
cur = card_service.get_current_card('prod_test_1')
check('get_current_card 正常返回', bool(cur and cur.get('title_ru')), str((cur or {}).get('title_ru')))
check('上架取值不含辅助词', 'auxiliary_tags_ru' not in (cur or {}))

print()
if fails:
    print('===== 失败项: %s =====' % ', '.join(fails))
    sys.exit(1)
print('===== 全部通过 =====')
