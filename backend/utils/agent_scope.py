"""店铺级权限校验（方案 A：业务员 JWT 直接调 Agent 上架接口）。

背景：/api/agent/* 原先只接受 Agent 令牌或管理员 JWT，业务员会 403。
放开后必须保证「只能操作自己店铺 / 只能扣自己店账号」，因此把店铺解析与归属判断集中到这里，
供路由层（403/404）与服务层（建卡前校验）共用。

要点：
- `stores` 表有两个店铺标识：`id`（内部主键，如 4）与 `store_id`（Ozon 店铺 id，如 216800183）。
  `Product.storeId` 两种都可能出现（建卡写内部 id，插件采集写 Ozon id），所以 resolve_store 两种都认。
- 管理员不受店铺范围限制；业务员/操作员只看自己 `stores.account_id = 自己 id` 的店。
- 无权限抛 AgentPermissionError（路由转 403），对象不存在抛 AgentNotFoundError（路由转 404）。
"""
from db import query

ADMIN_ROLE = 'admin'
SCOPED_ROLES = ('salesperson', 'operator')


class AgentPermissionError(Exception):
    """无权限：跨店操作 / 越权指定计费账号 / 业务员未指定店铺。"""


class AgentNotFoundError(Exception):
    """对象不存在（商品、店铺、卡片）。"""


def current_user():
    """当前登录用户（accounts 行 dict）；未登录返回 None。"""
    try:
        from utils.auth import get_current_user

        return get_current_user()
    except Exception:
        return None


def is_admin(user):
    return bool(user) and user.get('role') == ADMIN_ROLE


def requires_store_scope(user):
    """业务员/操作员需要店铺级校验；管理员与未登录（Agent 令牌）返回 False。"""
    if not user:
        return False
    if is_admin(user):
        return False
    return True


def _account_pk(user):
    try:
        return int((user or {}).get('id') or 0)
    except (TypeError, ValueError):
        return 0


def resolve_store(raw):
    """按 stores.id 或 stores.store_id 解析店铺行；不存在返回 None。"""
    if raw in (None, ''):
        return None
    text = str(raw).strip()
    if not text:
        return None
    row = None
    try:
        row = query('SELECT * FROM stores WHERE id = ?', (int(text),), one=True)
    except (TypeError, ValueError):
        row = None
    if not row:
        row = query('SELECT * FROM stores WHERE store_id = ?', (text,), one=True)
    return dict(row) if row else None


def store_account_id(store):
    """店铺归属账号（stores.account_id）；缺失或非法返回 None。"""
    if not store:
        return None
    raw = store.get('account_id')
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def product_store(product):
    """商品所属店铺行（按 Product.storeId 解析，两种 id 都认）。"""
    if not product:
        return None
    raw = product.get('storeId')
    if raw in (None, '', 0, '0'):
        return None
    return resolve_store(raw)


def store_belongs_to_user(raw_store, user):
    """店铺是否属于该用户（管理员恒 True）。"""
    if is_admin(user):
        return True
    store = resolve_store(raw_store)
    return bool(store) and store_account_id(store) == _account_pk(user)


def assert_store_for_user(user, raw_store):
    """建卡前校验店铺：业务员必须显式给 store_id 且属于自己。

    返回解析到的店铺行（admin 可能为 None）；不通过抛 AgentPermissionError。
    """
    if not requires_store_scope(user):
        return resolve_store(raw_store)
    if raw_store in (None, ''):
        raise AgentPermissionError('业务员建卡必须指定 store_id（只能在自己的店铺建卡）')
    store = resolve_store(raw_store)
    if not store:
        raise AgentPermissionError('店铺不存在或无权使用：%s' % raw_store)
    if store_account_id(store) != _account_pk(user):
        raise AgentPermissionError('无权使用该店铺：%s' % raw_store)
    return store


def assert_product_owned(product_id, user):
    """商品级校验：返回 (product, store)；不存在抛 AgentNotFoundError，越权抛 AgentPermissionError。"""
    from models.product import Product

    product = Product.find_by_id(str(product_id))
    if not product:
        raise AgentNotFoundError('商品不存在：%s' % product_id)
    if not requires_store_scope(user):
        return product, product_store(product)
    store = product_store(product)
    if not store or store_account_id(store) != _account_pk(user):
        raise AgentPermissionError('无权操作该店铺的商品：%s' % product_id)
    return product, store


def forced_billing_account(product):
    """套图强制计费账号 = 商品所属店铺的 account_id；解析不到返回 None。"""
    return store_account_id(product_store(product))


def allowed_store_pks(user):
    """业务员可见的内部店铺主键集合；admin 返回 None（全量）。"""
    if not user or is_admin(user):
        return None
    rows = query('SELECT id FROM stores WHERE account_id = ?', (_account_pk(user),))
    return [int(r['id']) for r in rows]
