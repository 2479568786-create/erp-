# -*- coding: utf-8 -*-
"""回归测试：Ozon 新应计接口的数据解析与落库。

背景：Ozon 于 2026-09-08 停用 /v3/finance/transaction/list，
本模块改用 /v1/finance/accrual/by-day。下面用**实测抓到的真实报文**验证解析。

运行：cd backend && python tests/test_finance_accrual.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='finacc_')
db.DB_PATH = os.path.join(_TMP, 'test.db')
db.init_db()

from services import finance_service as fs  # noqa: E402

fails = []


def check(label, cond, detail=''):
    print('[%s] %s %s' % ('PASS' if cond else 'FAIL', label, detail))
    if not cond:
        fails.append(label)


# ---- 实测报文（2026-09-13 从 api-seller.ozon.ru 抓取，字段原样保留）----
REAL_ITEM = {
    "accrual_id": 62902392204,
    "date": "2026-09-13",
    "total_amount": {"amount": "-16.66", "currency": "RUB"},
    "unit_number": "0141031129-0091",
    "accrued_category": "ITEM",
    "posting": None,
    "item_fees": {"fees": [
        {"sku": 4825473503, "fees": [{"type_id": 1, "accrued": {"amount": "-16.66", "currency": "RUB"}}]}
    ]},
    "non_item_fee": None,
    "container_fees": None,
}

# 一条含多 SKU、多费用类型的记录（模拟真实的多费用场景）
MULTI_FEE = {
    "accrual_id": 111,
    "date": "2026-09-10",
    "total_amount": {"amount": "-300.00", "currency": "RUB"},
    "unit_number": "13908620-0063",
    "accrued_category": "ITEM",
    "posting": "13908620-0063-1",
    "item_fees": {"fees": [
        {"sku": 4984707434, "fees": [
            {"type_id": 1, "accrued": {"amount": "-46.32", "currency": "RUB"}},
            {"type_id": 32, "accrued": {"amount": "-200.00", "currency": "RUB"}},
        ]},
        {"sku": 1111111111, "fees": [{"type_id": 1, "accrued": {"amount": "-53.68", "currency": "RUB"}}]},
    ]},
    "non_item_fee": {"type_id": 33, "accrued": {"amount": "-0.00", "currency": "RUB"}},
    "container_fees": None,
}

print('=== 1. 金额解析 ===')
check('_amount 取 dict 内 amount', fs._amount({'amount': '-16.66'}) == -16.66)
check('_amount 支持正数字符串', fs._amount({'amount': '250.5'}) == 250.5)
check('_amount 容错 None', fs._amount(None) == 0.0)
check('_amount 容错脏值', fs._amount({'amount': 'abc'}) == 0.0)

print()
print('=== 2. 费用块规整 _as_list ===')
check('None -> []', fs._as_list(None) == [])
check('{fees:[...]} -> 内层列表', len(fs._as_list({'fees': [{'a': 1}, {'b': 2}]})) == 2)
check('裸 dict -> [dict]', fs._as_list({'fees': None, 'x': 1}) == [{'fees': None, 'x': 1}])
check('list 过滤非 dict', fs._as_list([{'a': 1}, 'bad', None]) == [{'a': 1}])

print()
print('=== 3. 单费用记录拆分 ===')
fees = fs._fee_entries(REAL_ITEM)
check('拆出 1 条费用', len(fees) == 1, '%d 条' % len(fees))
check('type_id = 1（Acquiring）', fees[0]['type_id'] == 1, str(fees[0]['type_id']))
check('金额 = -16.66', abs(fees[0]['amount'] + 16.66) < 1e-9, str(fees[0]['amount']))
check('带出 sku', fees[0]['sku'] == 4825473503, str(fees[0]['sku']))

print()
print('=== 4. 多费用记录拆分（关键：一条记录拆多行才能分类统计）===')
fees2 = fs._fee_entries(MULTI_FEE)
check('拆出 4 条费用（3 条商品费 + 1 条非商品费）', len(fees2) == 4, '%d 条' % len(fees2))
# 期望构成：sku 4984707434 有 type 1(-46.32)+type 32(-200)；sku 1111111111 有 type 1(-53.68)；
#           non_item_fee 有 type 33(-0.00) → 排序后 [1, 1, 32, 33]
types = sorted(f['type_id'] for f in fees2)
check('type_id 构成正确 [1,1,32,33]', types == [1, 1, 32, 33], str(types))
total = round(sum(f['amount'] for f in fees2), 2)
check('费用合计 = -300.00（与 total_amount 一致）', abs(total + 300.0) < 1e-9, str(total))

print()
print('=== 5. 无细项时兜底用总额 ===')
BARE = {'accrual_id': 9, 'date': '2026-09-01',
        'total_amount': {'amount': '-7.77', 'currency': 'RUB'}, 'accrued_category': 'OTHER'}
bf = fs._fee_entries(BARE)
check('无细项也能落 1 行', len(bf) == 1)
check('兜底金额 = -7.77', abs(bf[0]['amount'] + 7.77) < 1e-9, str(bf[0]['amount']))

print()
print('=== 6. 落库映射 ===')
TYPE_MAP = {1: {'type_id': 1, 'name': 'Acquiring', 'description': 'Эквайринг'},
            32: {'type_id': 32, 'name': 'Logistic', 'description': 'Логистика'},
            33: {'type_id': 33, 'name': 'Marketing', 'description': 'Рекламные услуги'}}
fs._save_accrual('STORE1', MULTI_FEE, TYPE_MAP)
rows = db.query('SELECT * FROM finance_transactions WHERE store_id=? ORDER BY id', ('STORE1',))
check('落库 4 行', len(rows) == 4, '%d 行' % len(rows))
r0 = rows[0]
check('operation_type = 英文机器名', r0['operation_type'] == 'Acquiring', r0['operation_type'])
check('operation_type_name = 俄文说明', r0['operation_type_name'] == 'Эквайринг', r0['operation_type_name'])
check('operation_parent_type 用于分类聚合', r0['operation_parent_type'] == 'Acquiring')
check('operation_date 正确', r0['operation_date'] == '2026-09-10', r0['operation_date'])
check('posting_number 优先用 posting', r0['posting_number'] == '13908620-0063-1', r0['posting_number'])
check('amount 落库正确', abs(r0['amount'] + 46.32) < 1e-9, str(r0['amount']))
check('raw_json 保留了原始报文', 'accrual_id' in (r0['raw_json'] or ''))

print()
print('=== 7. 幂等性：同一批数据重复落库不应产生重复行 ===')
fs._save_accrual('STORE1', MULTI_FEE, TYPE_MAP)
rows2 = db.query('SELECT COUNT(*) AS c FROM finance_transactions WHERE store_id=?', ('STORE1',))
check('重复写入后仍是 4 行', rows2[0]['c'] == 4, '%d 行' % rows2[0]['c'])

print()
print('=== 8. 中文标签覆盖常用费用类型 ===')
labels = fs._labels()
for name, zh in [('Acquiring', '收单手续费'), ('Logistic', '物流费'),
                 ('Marketing', '广告服务费'), ('LastMile', '最后一公里')]:
    check('%s -> %s' % (name, zh), labels.get(name) == zh, str(labels.get(name)))

print()
print('=== 9. 无 posting 时回退 unit_number ===')
fs._save_accrual('STORE2', REAL_ITEM, TYPE_MAP)
r = db.query("SELECT posting_number FROM finance_transactions WHERE store_id='STORE2'", one=True)
check('posting 为 null 时用 unit_number', r['posting_number'] == '0141031129-0091', str(r['posting_number']))

print()
if fails:
    print('===== 失败项: %s =====' % ', '.join(fails))
    sys.exit(1)
print('===== 全部通过 =====')
