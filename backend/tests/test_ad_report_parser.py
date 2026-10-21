# -*- coding: utf-8 -*-
"""回归测试：Ozon 广告报表 CSV 解析（用真实表头与俄式数字格式）。

真实表头（实测自 api-performance.ozon.ru）：
  День;sku;Название товара;Предельная цена, ₽;Показы;Клики;CTR, %;Добавления в корзину;
  Средняя стоимость клика, ₽;Расход, ₽;Продано товаров;Продажи в продвижении, ₽;
  Продано товаров модели;Продажи в продвижении с заказов модели, ₽;ДРР в продвижении, %;
  ДРР, %;Дата добавления

运行：cd backend && python tests/test_ad_report_parser.py
"""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import ozon_performance_api as perf  # noqa: E402

fails = []


def check(label, cond, detail=''):
    print('[%s] %s %s' % ('PASS' if cond else 'FAIL', label, detail))
    if not cond:
        fails.append(label)


HEADER = ('День;sku;Название товара;Предельная цена, ₽;Показы;Клики;CTR, %;'
          'Добавления в корзину;Средняя стоимость клика, ₽;Расход, ₽;Продано товаров;'
          'Продажи в продвижении, ₽;Продано товаров модели;'
          'Продажи в продвижении с заказов модели, ₽;ДРР в продвижении, %;ДРР, %;Дата добавления')

META = ';Кампания по продвижению товаров № 33283208, период 15.08.2026-13.09.2026'

CSV_TEXT = '\n'.join([
    '\ufeff' + META,
    HEADER,
    # 逐日行：日期用「日.月.年」格式，数字用俄式「千分位空格 + 逗号小数」
    '01.09.2026;123456;Тестовый товар 1;5 000;1 200;35;2,92;3;12,00;420,50;2;1 200,00;0;0,00;35,0;35,0;01.09.2026',
    '02.09.2026;123456;Тестовый товар 1;5 000;800;15;1,88;1;11,50;172,50;0;0,00;0;0,00;0,0;0,0;02.09.2026',
    '01.09.2026;789012;Тестовый товар 2;9 000;500;10;2,00;0;8,00;80,00;0;0,00;0;0,00;0,0;0,0;01.09.2026',
    # 合计行（无 sku / 无日期）必须被跳过
    'Всего;;;;2 500;60;2,40;4;10,84;673,00;2;1 200,00;0;0,00;56,1;56,1;',
])

print('=== 1. 基础解析 ===')
rows = perf.parse_report_csv('33283208', CSV_TEXT)
check('解析出 3 条逐日行（跳过合计行）', len(rows) == 3, '实际 %d' % len(rows))
check('合计行未被计入', all(str(r.get('sku')) in ('123456', '789012') for r in rows))

first = next((r for r in rows if r.get('sku') == '123456' and r.get('stat_date') == '01.09.2026'), None)
check('日期保留原样供上层归一化', first is not None, str(first and first.get('stat_date')))
check('商品名解析正确', first and first.get('title') == 'Тестовый товар 1', str(first and first.get('title')))
check('campaignId 已注入', first and first.get('campaignId') == '33283208')
check('曝光解析（含千分位空格）', first and first.get('impressions') == '1 200',
      str(first and first.get('impressions')))
check('点击解析', first and first.get('clicks') == '35')
check('CTR 解析为百分数', first and first.get('ctr_percent') == '2,92')
check('花费解析', first and first.get('expense') == '420,50')
check('订单解析', first and first.get('orders') == '2')
check('未把「Продано товаров модели」误当订单', first and 'model' not in str(first.get('orders')))
check('未把「ДРР в продвижении」误当 ДРР', first and first.get('drr_percent') == '35,0',
      str(first and first.get('drr_percent')))

print()
print('=== 2. 俄式数字与日期归一化 ===')
check('_num("1 234,56") = 1234.56', abs(perf._num('1 234,56') - 1234.56) < 1e-9, str(perf._num('1 234,56')))
check('_num 处理不换行空格', abs(perf._num('1\xa0234,5') - 1234.5) < 1e-9, str(perf._num('1\xa0234,5')))
check('_num 处理 ₽ 后缀', abs(perf._num('420,50 ₽') - 420.5) < 1e-9, str(perf._num('420,50 ₽')))
check('_num 处理空值', perf._num('') == 0.0 and perf._num(None) == 0.0)
check('_num 处理非数字', perf._num('abc') == 0.0)

print()
print('=== 3. 表头归一化（区分近似列名）===')
check('Расход, ₽ -> расход', perf._norm_header('Расход, ₽') == 'расход')
check('CTR, % -> ctr', perf._norm_header('CTR, %') == 'ctr')
check('ДРР, % -> дрр', perf._norm_header('ДРР, %') == 'дрр')
check('ДРР в продвижении, % 与 дрр 区分',
      perf._norm_header('ДРР в продвижении, %') != perf._norm_header('ДРР, %'),
      perf._norm_header('ДРР в продвижении, %'))
check('Продано товаров 与 Продано товаров модели 区分',
      perf._norm_header('Продано товаров') != perf._norm_header('Продано товаров модели'))

print()
print('=== 4. 空报表（无投放）===')
EMPTY = '\n'.join(['\ufeff' + META, HEADER,
                   'Всего;;;;0;0;0,00;0;0,00;0,00;0;0,00;0;0,00;0,0;0,0;'])
check('无投放时返回空列表（不报错）', perf.parse_report_csv('1', EMPTY) == [],
      str(perf.parse_report_csv('1', EMPTY)))

print()
print('=== 5. ZIP 解包（多活动）===')
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as zf:
    zf.writestr('33283208_15.08.2026-13.09.2026.csv', CSV_TEXT)
    zf.writestr('99999999_15.08.2026-13.09.2026.csv', EMPTY)
parts = perf._split_report_blob(buf.getvalue())
check('ZIP 拆出 2 个活动', len(parts) == 2, str([p[0] for p in parts]))
check('从文件名解析出 campaignId', {p[0] for p in parts} == {'33283208', '99999999'},
      str([p[0] for p in parts]))
all_rows = []
for cid, text in parts:
    all_rows.extend(perf.parse_report_csv(cid, text))
check('跨活动合并后共 3 行', len(all_rows) == 3, '%d 行' % len(all_rows))

print()
print('=== 6. 单活动返回裸 CSV（不是 ZIP）===')
parts2 = perf._split_report_blob(CSV_TEXT.encode('utf-8'))
check('裸 CSV 也能处理', len(parts2) == 1, str(len(parts2)))

print()
if fails:
    print('===== 失败项: %s =====' % ', '.join(fails))
    sys.exit(1)
print('===== 全部通过 =====')
