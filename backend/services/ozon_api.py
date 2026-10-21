"""
Ozon Seller API 服务层
封装与 Ozon 平台的 API 交互，包括类目树拉取、特征拉取等
"""
import requests
import json
import os
import time
from flask import current_app

# 导入通用映射表和动态映射缓存
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from data.category_mapping_general import SYNONYM_MAP, GENERAL_MAPPINGS, CATEGORY_GROUPS
from services.category_mapping_cache import get_mapping, set_mapping, get_all_mappings, delete_mapping, clear_all_mappings, get_cache_stats, add_manual_mapping

OZON_API_BASE = 'https://api-seller.ozon.ru'

# 本地缓存目录
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)

# ========== 内存缓存 ==========
_memory_cache = {}  # key -> { data, expire_at }

# 缓存 TTL 配置（秒）
CACHE_TTL = {
    'category_tree': 3600 * 24,      # 类目树：24小时
    'attributes': 3600 * 6,           # 属性列表：6小时
    'attribute_values': 3600 * 24,    # 字典值：24小时
}


class OzonAPIError(Exception):
    """Ozon API 调用异常"""
    def __init__(self, message, status_code=None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


def _get_store_credentials(store_id=None):
    """获取店铺的 API 凭证，优先使用指定店铺，否则取第一个可用店铺

    api_key 在数据库中加密存储，此处解密后返回明文供 API 调用使用。
    """
    from models.account import Store
    from utils.security import decrypt_secret

    if store_id:
        store = Store.find_by_store_id(store_id)
    else:
        stores = Store.find_all(auth_status='active')
        store = stores[0] if stores else None

    if not store:
        return None, None

    client_id = store.get('client_id') or store.get('store_id')
    # 解密 api_key（兼容历史明文数据）
    api_key = decrypt_secret(store.get('api_key'))

    if not client_id or not api_key:
        return None, None

    return str(client_id), api_key


# ========== 重试策略（P0 稳定性修复） ==========
# Ozon 接口在高负载时会返回瞬时错误（VALIDATION ERROR / 5xx / 429），
# 同一次请求立即重试往往就能成功。此前代码单次请求失败即抛异常，
# 导致"明明能成功却报失败"，并让整批图片提交被放弃。
OZON_API_RETRIES = int(os.environ.get('OZON_API_RETRIES', '2'))      # 额外重试次数（共 3 次尝试）
OZON_API_BACKOFF = float(os.environ.get('OZON_API_BACKOFF', '3'))    # 首次重试等待秒数，指数退避

# 只对这些情况重试：服务端错误 / 限流 / 瞬时校验失败 / 网络抖动
_RETRYABLE_STATUS = (429, 500, 502, 503, 504, 408)
_RETRYABLE_HINTS = ('validation error', 'try again', 'timeout', 'too many requests',
                    'internal error', 'service unavailable', 'temporarily')


def _is_retryable(status_code, message):
    """判断一个失败的 Ozon 响应是否值得重试

    Ozon 对参数/图片校验失败时返回的是 **400 + message='VALIDATION ERROR'**，
    这既可能是"请求本身不合规"（重试无用），也可能是"服务端瞬时抖动"
    （重试即过）。线上实测属于后者，因此这里按 message 文案判定为可重试；
    只有明确的凭证/权限错误（401/403/404）才不重试。
    """
    if status_code in (401, 403, 404):
        return False
    if status_code in _RETRYABLE_STATUS:
        return True
    text = str(message or '').lower()
    return any(h in text for h in _RETRYABLE_HINTS)


def _post_with_retry(url, body, headers, retries=None, backoff=None, logger=None):
    """带指数退避重试的 POST，返回 (data, attempts)；全部失败时抛 OzonAPIError。

    仅对瞬时性错误重试（5xx / 429 / VALIDATION ERROR / 网络抖动），
    对 401/403/404 这类确定性错误立即失败，避免无谓等待。
    """
    retries = OZON_API_RETRIES if retries is None else int(retries)
    backoff = OZON_API_BACKOFF if backoff is None else float(backoff)
    log = logger or print
    attempts = 0

    while True:
        attempts += 1
        reason = None
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            try:
                data = resp.json()
            except ValueError:
                data = {'message': resp.text[:500]}

            if resp.status_code == 200:
                return data, attempts

            message = str(data.get('message') or data.get('error') or resp.text[:500])
            if attempts <= retries and _is_retryable(resp.status_code, message):
                wait = backoff * (2 ** (attempts - 1))
                log(f'[OzonAPI] 瞬时错误 HTTP {resp.status_code}，{wait:.0f}s 后重试 '
                    f'({attempts}/{retries}): {message[:160]}')
                time.sleep(wait)
                continue

            raise OzonAPIError(f'Ozon API 错误: {message}', resp.status_code)

        except OzonAPIError:
            raise
        except requests.exceptions.Timeout:
            reason = 'Ozon API 请求超时'
        except requests.exceptions.ConnectionError:
            reason = '无法连接 Ozon API 服务器'
        except requests.exceptions.RequestException as e:
            reason = f'请求异常: {str(e)}'

        # 网络类异常同样值得重试
        if reason and attempts <= retries:
            wait = backoff * (2 ** (attempts - 1))
            log(f'[OzonAPI] {reason}，{wait:.0f}s 后重试 ({attempts}/{retries})')
            time.sleep(wait)
            continue
        raise OzonAPIError(reason)


def _get_with_retry(url, headers, retries=None, backoff=None, logger=None):
    """带指数退避重试的 GET，返回 (data, attempts)；全部失败时抛 OzonAPIError。

    与 _post_with_retry 语义完全对称 —— 少数 Ozon 接口（如 /v1/actions）
    只接受 GET，用 POST 调用会返回 405。重试判定复用 _is_retryable，
    同样对 5xx / 429 / 408 / 瞬时校验失败重试，对 401/403/404 立即失败。
    """
    retries = OZON_API_RETRIES if retries is None else int(retries)
    backoff = OZON_API_BACKOFF if backoff is None else float(backoff)
    log = logger or print
    attempts = 0

    while True:
        attempts += 1
        reason = None
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            try:
                data = resp.json()
            except ValueError:
                data = {'message': resp.text[:500]}

            if resp.status_code == 200:
                return data, attempts

            message = str(data.get('message') or data.get('error') or resp.text[:500])
            if attempts <= retries and _is_retryable(resp.status_code, message):
                wait = backoff * (2 ** (attempts - 1))
                log(f'[OzonAPI] 瞬时错误 HTTP {resp.status_code}，{wait:.0f}s 后重试 '
                    f'({attempts}/{retries}): {message[:160]}')
                time.sleep(wait)
                continue

            raise OzonAPIError(f'Ozon API 错误: {message}', resp.status_code)

        except OzonAPIError:
            raise
        except requests.exceptions.Timeout:
            reason = 'Ozon API 请求超时'
        except requests.exceptions.ConnectionError:
            reason = '无法连接 Ozon API 服务器'
        except requests.exceptions.RequestException as e:
            reason = f'请求异常: {str(e)}'

        if reason and attempts <= retries:
            wait = backoff * (2 ** (attempts - 1))
            log(f'[OzonAPI] {reason}，{wait:.0f}s 后重试 ({attempts}/{retries})')
            time.sleep(wait)
            continue
        raise OzonAPIError(reason)


def _call_ozon_api_get(endpoint, client_id=None, api_key=None,
                       retries=None, logger=None):
    """调用只支持 GET 的 Ozon Seller API（带瞬时错误重试）

    专供 /v1/actions 这类"仅 GET"接口使用；POST 会返回 405。
    """
    if not client_id or not api_key:
        client_id, api_key = _get_store_credentials()

    if not client_id or not api_key:
        raise OzonAPIError('未配置 Ozon API 凭证，请先在店铺管理中添加 API Key', 401)

    headers = {
        'Client-Id': client_id,
        'Api-Key': api_key,
        'Content-Type': 'application/json',
    }

    url = f'{OZON_API_BASE}{endpoint}'
    data, _attempts = _get_with_retry(url, headers, retries=retries, logger=logger)
    return data


def _call_ozon_api(endpoint, body, client_id=None, api_key=None,
                   retries=None, logger=None):
    """调用 Ozon Seller API（带瞬时错误重试）

    默认重试次数由 OZON_API_RETRIES 控制（默认 2 次额外重试）。
    可通过 retries=0 关闭重试，用于"必须只调一次"的场景。
    """
    if not client_id or not api_key:
        client_id, api_key = _get_store_credentials()

    if not client_id or not api_key:
        raise OzonAPIError('未配置 Ozon API 凭证，请先在店铺管理中添加 API Key', 401)

    headers = {
        'Client-Id': client_id,
        'Api-Key': api_key,
        'Content-Type': 'application/json',
    }

    url = f'{OZON_API_BASE}{endpoint}'
    data, _attempts = _post_with_retry(url, body, headers, retries=retries, logger=logger)
    return data


def _cache_get(key, ttl=None):
    """读取缓存：优先内存缓存，回退文件缓存，支持 TTL"""
    # 1. 内存缓存
    mem_entry = _memory_cache.get(key)
    if mem_entry:
        if mem_entry['expire_at'] > time.time():
            return mem_entry['data']
        else:
            del _memory_cache[key]  # 过期清除

    # 2. 文件缓存
    path = os.path.join(CACHE_DIR, f'{key}.json')
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                entry = json.load(f)
            # 兼容旧格式（无 expire_at）
            if isinstance(entry, dict) and 'expire_at' in entry:
                if entry['expire_at'] > time.time():
                    # 回填内存缓存
                    _memory_cache[key] = entry
                    return entry['data']
                else:
                    # 文件缓存过期，删除
                    os.remove(path)
                    return None
            else:
                # 旧格式数据，直接返回（无 TTL 限制）
                _memory_cache[key] = {'data': entry, 'expire_at': time.time() + (ttl or 3600)}
                return entry
        except (json.JSONDecodeError, KeyError):
            return None
    return None


def _cache_set(key, data, ttl=None):
    """写入缓存：同时写内存和文件，支持 TTL"""
    expire_at = time.time() + (ttl or 3600)
    entry = {'data': data, 'expire_at': expire_at}

    # 写内存
    _memory_cache[key] = entry

    # 写文件
    path = os.path.join(CACHE_DIR, f'{key}.json')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 文件写入失败不影响功能


def _cache_get_with_language_alias(cache_key, language, ttl=None):
    """Reuse legacy DEFAULT and current RU cache files interchangeably."""
    cached = _cache_get(cache_key, ttl=ttl)
    if cached is not None:
        return cached
    aliases = {'RU': 'DEFAULT', 'DEFAULT': 'RU'}
    alias = aliases.get(str(language or '').upper())
    if not alias:
        return None
    token = f'_{language}'
    if cache_key.endswith(token):
        alias_key = cache_key[:-len(token)] + f'_{alias}'
    else:
        alias_key = cache_key.replace(f'_{language}_', f'_{alias}_', 1)
    cached = _cache_get(alias_key, ttl=ttl)
    if cached is not None:
        _cache_set(cache_key, cached, ttl=ttl)
        print(f'[Ozon缓存] 语言别名命中: {alias_key} -> {cache_key}')
    return cached


def get_category_tree(language='ZH_HANS', client_id=None, api_key=None, use_cache=True):
    """获取 Ozon 类目树（带内存+文件双级缓存，TTL 24小时）

    Ozon 类目树结构:
    - L1: description_category_id, category_name, children
    - L2: description_category_id, category_name, children
    - L3: type_id, type_name (叶子节点是 type)
    """
    cache_key = f'category_tree_{language}'
    ttl = CACHE_TTL['category_tree']
    if use_cache:
        cached = _cache_get(cache_key, ttl=ttl)
        if cached:
            return cached

    result = _call_ozon_api('/v1/description-category/tree', {
        'language': language,
    }, client_id, api_key)

    if result:
        _cache_set(cache_key, result, ttl=ttl)

    return result


def get_category_tree_bilingual(use_cache=True):
    """获取双语类目树，合并中文名和俄语名为 '中文（俄语）' 格式

    优先级：
    1. use_cache=True 时优先从数据库读取（完整映射库）
    2. 数据库无数据或 use_cache=False 时，从 Ozon API 拉取
    """
    # 优先从数据库读取（完整映射库，每月同步）
    if use_cache:
        try:
            from models.category import OzonCategory
            if OzonCategory.count() > 0:
                db_tree = OzonCategory.get_tree()
                if db_tree:
                    return db_tree
        except Exception as e:
            print(f'[类目树] 从数据库读取失败，回退到 API: {e}')

    # 从 Ozon API 拉取（用于同步或数据库为空时）
    zh_result = get_category_tree(language='ZH_HANS', use_cache=use_cache)
    ru_result = get_category_tree(language='DEFAULT', use_cache=use_cache)

    zh_tree = zh_result.get('result', []) if zh_result else []
    ru_tree = ru_result.get('result', []) if ru_result else []

    # 构建 L1/L2 的 ID -> name_ru 映射
    ru_l1_map = {l1['description_category_id']: l1 for l1 in ru_tree}

    def get_ru_l2_name(l1_id, l2_id):
        ru_l1 = ru_l1_map.get(l1_id)
        if not ru_l1:
            return ''
        for l2 in ru_l1.get('children', []):
            if l2['description_category_id'] == l2_id:
                return l2.get('category_name', '')
        return ''

    def get_ru_l3_name(l1_id, l2_id, l3_type_id):
        ru_l1 = ru_l1_map.get(l1_id)
        if not ru_l1:
            return ''
        for l2 in ru_l1.get('children', []):
            if l2['description_category_id'] == l2_id:
                for l3 in l2.get('children', []):
                    if l3.get('type_id') == l3_type_id:
                        return l3.get('type_name', '')
        return ''

    def merge_name(zh_name, ru_name):
        if ru_name and ru_name != zh_name:
            return f'{zh_name}（{ru_name}）'
        return zh_name

    merged_tree = []
    for l1 in zh_tree:
        l1_id = l1['description_category_id']
        ru_l1 = ru_l1_map.get(l1_id, {})
        ru_l1_name = ru_l1.get('category_name', '')

        merged_l1 = {
            'description_category_id': l1_id,
            'category_name': merge_name(l1['category_name'], ru_l1_name),
            'category_name_zh': l1['category_name'],
            'category_name_ru': ru_l1_name,
            'disabled': l1.get('disabled', False),
            'children': [],
        }

        for l2 in l1.get('children', []):
            l2_id = l2['description_category_id']
            ru_l2_name = get_ru_l2_name(l1_id, l2_id)

            merged_l2 = {
                'description_category_id': l2_id,
                'category_name': merge_name(l2['category_name'], ru_l2_name),
                'category_name_zh': l2['category_name'],
                'category_name_ru': ru_l2_name,
                'disabled': l2.get('disabled', False),
                'children': [],
            }

            for l3 in l2.get('children', []):
                l3_type_id = l3.get('type_id')
                ru_l3_name = get_ru_l3_name(l1_id, l2_id, l3_type_id) if l3_type_id else ''

                merged_l3 = {
                    'type_id': l3_type_id,
                    'type_name': merge_name(l3.get('type_name', ''), ru_l3_name),
                    'type_name_zh': l3.get('type_name', ''),
                    'type_name_ru': ru_l3_name,
                    'disabled': l3.get('disabled', False),
                    'children': [],
                }
                merged_l2['children'].append(merged_l3)

            merged_l1['children'].append(merged_l2)

        merged_tree.append(merged_l1)

    return merged_tree


def find_category_by_ru_name(source_category, tree=None):
    """[已废弃] 基于俄文类目名称的精确匹配

    本函数已被 services/category_matcher.py 中的分层匹配逻辑取代。
    保留仅为向后兼容，内部直接委托给新模块的精确名称匹配层。

    新模块根据源语言自动选择匹配字段（中文/俄文/混合），不再需要单独的
    "俄文匹配" 函数。详见 category_matcher._try_exact_name_match。

    Args:
        source_category: 采集的原始分类名
        tree: Ozon 类目树（双语），如不提供则从缓存加载

    Returns:
        匹配结果 dict（与 match_category 同格式）
    """
    from services.category_matcher import _build_flat_categories, _try_exact_name_match

    if not source_category:
        return {'matched': False, 'candidates': [], 'reason': '缺少产品类目信息'}

    if tree is None:
        try:
            tree = get_category_tree_bilingual(use_cache=True)
        except OzonAPIError:
            return {'matched': False, 'candidates': []}

    if not tree:
        return {'matched': False, 'candidates': []}

    flat_cats = _build_flat_categories(tree)
    # 强制使用俄文字段匹配（保持旧函数语义）
    result = _try_exact_name_match(source_category, flat_cats, 'ru')
    return result if result else {'matched': False, 'candidates': [], 'reason': '俄文名称未匹配到类目', '_source': 'ru_no_match'}


def validate_description_category_id(desc_cat_id, tree=None):
    """验证 descriptionCategoryId 是否在 Ozon 类目树中有效

    采集器可能从页面 JSON 中提取到无效的 descriptionCategoryId（如内部导航 ID），
    本函数在类目树中查找该 ID，判断其是否为有效的 L2 类目。

    Args:
        desc_cat_id: 待验证的 description_category_id（字符串或整数）
        tree: Ozon 类目树（双语），如不提供则从缓存加载

    Returns:
        dict:
          - valid: bool 是否有效
          - l1_name: str L1 类目名
          - l2_name: str L2 类目名
          - description_category_id: int
          - l3_candidates: list 该 L2 下的 L3 候选列表
    """
    if not desc_cat_id:
        return {'valid': False, 'l3_candidates': []}

    try:
        target_id = int(desc_cat_id)
    except (ValueError, TypeError):
        return {'valid': False, 'l3_candidates': []}

    if tree is None:
        try:
            tree = get_category_tree_bilingual(use_cache=True)
        except OzonAPIError:
            return {'valid': False, 'l3_candidates': []}

    if not tree:
        return {'valid': False, 'l3_candidates': []}

    for l1 in tree:
        for l2 in (l1.get('children') or []):
            if l2.get('description_category_id') == target_id:
                # 找到 L2，收集其下所有 L3 候选
                l3_list = []
                for l3 in (l2.get('children') or []):
                    if l3.get('type_id'):
                        l3_list.append({
                            'description_category_id': target_id,
                            'type_id': l3.get('type_id'),
                            'label': f"{l1.get('category_name_zh', '')} / {l2.get('category_name_zh', '')} / {l3.get('type_name_zh', '')}",
                        })
                    if len(l3_list) >= 10:
                        break
                return {
                    'valid': True,
                    'l1_name': l1.get('category_name_zh', ''),
                    'l2_name': l2.get('category_name_zh', ''),
                    'description_category_id': target_id,
                    'l3_candidates': l3_list,
                }

    return {'valid': False, 'l3_candidates': []}


def validate_category_pair(desc_cat_id, type_id, tree=None):
    """Validate that type_id is an actual child of description_category_id."""
    try:
        target_desc_id = int(desc_cat_id)
        target_type_id = int(type_id)
    except (ValueError, TypeError):
        return {'valid': False, 'reason': '类目 ID 格式无效'}

    if tree is None:
        try:
            tree = get_category_tree_bilingual(use_cache=True)
        except OzonAPIError as exc:
            return {'valid': False, 'reason': f'类目树加载失败: {exc}'}

    for l1 in tree or []:
        for l2 in l1.get('children') or []:
            if int(l2.get('description_category_id') or 0) != target_desc_id:
                continue
            for l3 in l2.get('children') or []:
                if int(l3.get('type_id') or 0) == target_type_id:
                    label_parts = [
                        l1.get('category_name_zh') or l1.get('category_name'),
                        l2.get('category_name_zh') or l2.get('category_name'),
                        l3.get('type_name_zh') or l3.get('type_name'),
                    ]
                    return {
                        'valid': True,
                        'description_category_id': target_desc_id,
                        'type_id': target_type_id,
                        'label': ' / '.join(str(part) for part in label_parts if part),
                        'source': 'category_tree_pair',
                    }
            return {'valid': False, 'reason': 'typeId 不属于 descriptionCategoryId'}
    return {'valid': False, 'reason': 'descriptionCategoryId 不在当前 Ozon 类目树中'}


def match_category(source_category, source_platform='ozon', title='', description=''):
    """根据采集的原始分类名称，自动匹配 Ozon 类目树中的类目

    本函数为向后兼容入口，实际匹配逻辑已迁移至 services/category_matcher.py。
    新模块采用严格分层匹配策略（缓存→通用映射→语言感知精确匹配→关键词打分→AI 兜底），
    修复了原代码中 ru_no_match 短路导致中文源永远跳过关键词匹配的 BUG。

    多信号匹配（对齐妙手 ERP 做法）：
      - 主信号：source_category（采集的原始类目）
      - 辅助信号：title + description
      - 当 source_category 非空：层 1-4 用类目匹配，层 5 AI 用类目+标题+描述
      - 当 source_category 为空但 title/description 非空：直接走层 5 AI 多信号匹配
      - 当三者都为空：返回未匹配

    Args:
        source_category: 采集到的原始分类名称（如 "电子 > 手机 > 智能手机" 或 "服装/饰品"）
        source_platform: 来源平台（ozon/1688/taobao）
        title: 商品标题（多信号辅助，AI 匹配时使用）
        description: 商品描述（多信号辅助，AI 匹配时使用）

    Returns:
        匹配结果 dict，包含:
          - matched: bool 是否匹配成功
          - description_category_id: int Ozon 二级类目 ID
          - type_id: int Ozon 三级类型 ID
          - label: str 匹配到的类目面包屑路径
          - confidence: str 匹配置信度 (high/medium/low)
          - candidates: list 候选类目列表（最多5个）
          - _source: str 匹配来源标记
    """
    from services.category_matcher import match_category as _do_match
    return _do_match(source_category, source_platform, title, description=description)


def get_category_attributes(description_category_id, type_id, language='ZH_HANS',
                            client_id=None, api_key=None, refresh=False):
    """获取类目下的特征（属性）列表（带缓存，TTL 6小时）

    Args:
        description_category_id: Ozon 类目 ID
        type_id: Ozon 类型 ID
        language: 语言（ZH_HANS 中文, DEFAULT 俄语）
    """
    cache_key = f'attrs_{description_category_id}_{type_id}_{language}'
    ttl = CACHE_TTL['attributes']
    if not refresh:
        cached = _cache_get_with_language_alias(cache_key, language, ttl=ttl)
        if cached:
            return cached

    result = _call_ozon_api('/v1/description-category/attribute', {
        'description_category_id': description_category_id,
        'type_id': type_id,
        'language': language,
    }, client_id, api_key)

    if result:
        _cache_set(cache_key, result, ttl=ttl)

    return result


def get_attribute_values(description_category_id, type_id, attribute_id,
                         language='ZH_HANS', last_value_id=0, limit=100,
                         client_id=None, api_key=None):
    """获取属性的可选值（字典值，带缓存，TTL 24小时）

    自动分页拉取全量字典值并缓存。
    """
    cache_key = f'attr_vals_{description_category_id}_{type_id}_{attribute_id}_{language}'
    ttl = CACHE_TTL['attribute_values']

    # 非首页请求不使用缓存（分页场景）
    if last_value_id == 0:
        cached = _cache_get_with_language_alias(cache_key, language, ttl=ttl)
        if cached:
            return cached

    result = _call_ozon_api('/v1/description-category/attribute/values', {
        'description_category_id': description_category_id,
        'type_id': type_id,
        'attribute_id': attribute_id,
        'language': language,
        'last_value_id': last_value_id,
        'limit': limit,
    }, client_id, api_key)

    # 首页请求缓存全量结果
    if result and last_value_id == 0:
        _cache_set(cache_key, result, ttl=ttl)

    return result


def search_attribute_values(description_category_id, type_id, attribute_id, query,
                            language='ZH_HANS', limit=50, client_id=None, api_key=None):
    """搜索属性字典值（适用于品牌等大字典，不需要全量加载）

    使用 Ozon API 的 query 参数按关键词搜索，返回匹配的字典值。
    缓存 1 小时。
    """
    cache_key = f'attr_vals_search_{description_category_id}_{type_id}_{attribute_id}_{language}_{query}'
    cached = _cache_get_with_language_alias(cache_key, language, ttl=3600)
    if cached:
        return cached

    result = _call_ozon_api('/v1/description-category/attribute/values', {
        'description_category_id': description_category_id,
        'type_id': type_id,
        'attribute_id': attribute_id,
        'language': language,
        'query': query,
        'limit': limit,
    }, client_id, api_key)

    _cache_set(cache_key, result, ttl=3600)
    return result


def get_attribute_values_full(description_category_id, type_id, attribute_id,
                               language='ZH_HANS', client_id=None, api_key=None):
    """获取属性的全量字典值（自动翻页，带缓存）

    对于大字典（如颜色、品牌），自动翻页拉取全部值。
    """
    cache_key = f'attr_vals_full_{description_category_id}_{type_id}_{attribute_id}_{language}'
    ttl = CACHE_TTL['attribute_values']
    cached = _cache_get_with_language_alias(cache_key, language, ttl=ttl)
    if cached:
        return cached

    all_values = []
    last_value_id = 0
    limit = 2000

    while True:
        result = _call_ozon_api('/v1/description-category/attribute/values', {
            'description_category_id': description_category_id,
            'type_id': type_id,
            'attribute_id': attribute_id,
            'language': language,
            'last_value_id': last_value_id,
            'limit': limit,
        }, client_id, api_key)

        values = result.get('result', [])
        all_values.extend(values)

        if not values or not result.get('has_next'):
            break

        last_value_id = values[-1]['id']

    full_result = {'result': all_values, 'has_next': False, 'total': len(all_values)}
    _cache_set(cache_key, full_result, ttl=ttl)

    return full_result


def import_products(items, client_id=None, api_key=None):
    """创建/更新商品（发布到 Ozon）

    Args:
        items: 商品列表，每个元素为 Ozon /v3/product/import 要求的 item 格式

    Returns:
        dict: { "result": { "task_id": 12345, "items": [...] } }
    """
    return _call_ozon_api('/v3/product/import', {
        'items': items,
    }, client_id, api_key)


def get_import_info(task_id, client_id=None, api_key=None):
    """查询商品发布任务状态

    Args:
        task_id: import_products 返回的 task_id

    Returns:
        dict: { "result": { "items": [{ "status": "imported", ... }] } }
    """
    return _call_ozon_api('/v1/product/import/info', {
        'task_id': task_id,
    }, client_id, api_key)


def import_product_pictures(product_id, images=None, color_image=None, images360=None,
                            client_id=None, api_key=None, chunk_size=None):
    """【图片专用通道】上传/更新已创建商品的图片（Ozon /v1/product/pictures/import）

    为什么必须单独走这个接口：
    - 图片如果只"夹带"在 /v3/product/import 里，Ozon 是**异步**处理的，且
      import 状态接口只回商品级错误，图片失败会被完全吞掉 —— 表现为
      "上架成功但图片没上/不显示"，且无从排查。
    - 本接口是 Ozon 的图片专用通道，提交后会返回每张图的处理状态；
      配合 get_product_pictures_info 可以拿到每张图的最终结果与错误。
    - color_image（营销颜色样本）在这里原生支持，比塞进 v3 import 更规范。

    分批提交（P0 稳定性修复）：
    - 此前把全部图片打包成**一次**请求，Ozon 会整批校验，只要有一张图不合规
      整批都会被拒（VALIDATION ERROR），且已成功的图也白提交。
    - 现在按 chunk_size（默认 6 张）分批提交，单批失败不影响其它批次，
      并返回每批的成功/失败明细，便于上层判断"部分成功"。

    Args:
        product_id: Ozon 商品 ID（注意是 Ozon 的 product_id，不是 offer_id）
        images: 图片 URL 数组，最多 30 张，**第一张会被设为主图**
        color_image: 营销颜色样本（单张 URL）
        images360: 360 环绕图，最多 70 张
        chunk_size: 每批提交的图片数量，默认 6；传 0 或 None 之外的负数则不分批

    Returns:
        dict: { "result": { "pictures": [...] }, "partial": bool,
                "failed_chunks": [...], "submitted": int, "attempts": int }
    """
    clean_images = [u for u in (images or []) if u][:30]

    # 计算分批：images360 与 color_image 体积小，不参与分批
    size = 6 if chunk_size is None else int(chunk_size)
    if size <= 0 or len(clean_images) <= size:
        chunks = [clean_images] if clean_images else []
    else:
        chunks = [clean_images[i:i + size] for i in range(0, len(clean_images), size)]

    # 无图片时仍需提交 color_image / images360
    if not chunks:
        chunks = [None]

    merged_pictures = []
    failed_chunks = []
    submitted = 0
    total_attempts = 0
    first_error = None

    for idx, chunk in enumerate(chunks):
        body = {'product_id': int(product_id)}
        if chunk:
            body['images'] = chunk
        # 附件只在第一批带，避免重复提交
        if idx == 0:
            if color_image:
                body['color_image'] = color_image
            if images360:
                body['images360'] = [u for u in images360 if u][:70]

        try:
            data, attempts = _call_ozon_api_with_attempts(
                '/v1/product/pictures/import', body, client_id, api_key)
            total_attempts += attempts
            submitted += len(chunk or [])
            for pic in ((data or {}).get('result') or {}).get('pictures') or []:
                merged_pictures.append(pic)
        except Exception as e:
            if first_error is None:
                first_error = e
            failed_chunks.append({
                'chunk_index': idx,
                'count': len(chunk or []),
                'urls': chunk or [],
                'error': str(e),
            })

    # 全部批次都失败 -> 按原语义抛异常，让上层记录明确失败
    if failed_chunks and submitted == 0:
        raise first_error if first_error else OzonAPIError('图片提交失败')

    return {
        'result': {'pictures': merged_pictures},
        'partial': bool(failed_chunks),
        'failed_chunks': failed_chunks,
        'submitted': submitted,
        'chunk_total': len(chunks),
        'attempts': total_attempts,
    }


def _call_ozon_api_with_attempts(endpoint, body, client_id, api_key):
    """内部：调用 Ozon API 并返回 (data, attempts)，供分批提交统计重试次数"""
    if not client_id or not api_key:
        client_id, api_key = _get_store_credentials()
    if not client_id or not api_key:
        raise OzonAPIError('未配置 Ozon API 凭证，请先在店铺管理中添加 API Key', 401)
    headers = {
        'Client-Id': client_id,
        'Api-Key': api_key,
        'Content-Type': 'application/json',
    }
    return _post_with_retry(f'{OZON_API_BASE}{endpoint}', body, headers)


def get_product_pictures_info(product_ids, client_id=None, api_key=None):
    """【图片核查通道】查询商品图片的最终状态与错误（Ozon /v2/product/pictures/info）

    Returns:
        dict: { "items": [ {product_id, primary_photo[], photo[], color_photo[],
                            photo_360[], errors[]} ] }
    """
    if not isinstance(product_ids, (list, tuple)):
        product_ids = [product_ids]
    return _call_ozon_api('/v2/product/pictures/info', {
        'product_id': [str(pid) for pid in product_ids if pid],
    }, client_id, api_key)


def update_prices(prices_items, client_id=None, api_key=None):
    """独立更新已发布商品的价格（不重新发布商品）

    通过 /v3/products/update-prices 接口更新价格/划线价。
    适用于"已发布商品管理"场景：商品已在 Ozon 上架，仅需调整价格。

    Args:
        prices_items: 价格更新列表，每个元素格式：
            {
                "offer_id": "SKU编码",
                "price": {"price": "999.00", "old_price": "1299.00"},
                # 或者带 VAT 价格：
                # "price": {"price": "999.00", "old_price": "1299.00", "marketing_price": "..."}
            }
        client_id: 可选，店铺 Client-Id
        api_key: 可选，店铺 API-Key

    Returns:
        dict: { "result": [...] }
    """
    return _call_ozon_api('/v3/products/update-prices', {
        'items': prices_items,
    }, client_id, api_key)


def update_stocks(stocks_items, client_id=None, api_key=None):
    """独立更新已发布商品的库存（不重新发布商品）

    通过 /v3/products/update-stocks 接口更新库存。
    适用于"已发布商品管理"场景：商品已在 Ozon 上架，仅需调整库存。

    Args:
        stocks_items: 库存更新列表，每个元素格式：
            {
                "offer_id": "SKU编码",
                "stock": { "present": 100, "reserved": 0 }
            }
        client_id: 可选，店铺 Client-Id
        api_key: 可选，店铺 API-Key

    Returns:
        dict: { "result": [...] }
    """
    return _call_ozon_api('/v3/products/update-stocks', {
        'items': stocks_items,
    }, client_id, api_key)


# ========== 在线商品同步（拉取店铺商品列表 + 详情）==========

def list_products(status_filter=None, last_id='', limit=100, client_id=None, api_key=None):
    """获取店铺商品列表（仅返回 product_id 和 offer_id，需再调 get_product_info_list 取详情）

    通过 /v3/product/list 接口分页拉取店铺全部商品的 ID 列表。
    注意：Ozon 已下线 /v2/product/list（返回 404），v3 的 filter 字段为必填。

    Args:
        status_filter: 可选状态过滤，Ozon 原始状态值：
            '' (全部) / 'pending' / 'processing' / 'active' / 'not_processed' / 'imported' / 'validation' / 'failed' / 'rejected'
        last_id: 分页游标，首次传 ''，后续传上一次返回的 result.last_id
        limit: 单页数量（1-1000），默认 100
        client_id: 可选，店铺 Client-Id
        api_key: 可选，店铺 API-Key

    Returns:
        dict: { "result": { "items": [{"product_id": 123, "offer_id": "SKU"}, ...], "last_id": "", "total": 100 } }
    """
    body = {
        'limit': min(max(int(limit), 1), 1000),
        'last_id': last_id or '',
    }
    # v3 的 filter 为必填字段：有 status_filter 时按状态过滤，否则取全部
    if status_filter:
        body['filter'] = {'status': status_filter}
    else:
        body['filter'] = {'visibility': 'ALL'}
    return _call_ozon_api('/v3/product/list', body, client_id, api_key)


def list_all_products(client_id=None, api_key=None, status_filter=None, page_size=500):
    """拉取店铺全部商品的 ID 列表（自动分页）

    Returns:
        list: [{"product_id": 123, "offer_id": "SKU"}, ...]
    """
    all_items = []
    last_id = ''
    while True:
        result = list_products(
            status_filter=status_filter, last_id=last_id, limit=page_size,
            client_id=client_id, api_key=api_key,
        )
        items = (result.get('result', {}) or {}).get('items', []) or []
        all_items.extend(items)
        last_id = (result.get('result', {}) or {}).get('last_id', '') or ''
        if not last_id or not items:
            break
    return all_items


def get_product_info_list(product_ids, client_id=None, api_key=None):
    """批量获取商品详情（最多 100 个/次）

    通过 /v3/product/info/list 接口查询商品详情，包含标题、图片、状态、价格、库存等。

    Args:
        product_ids: Ozon 商品 ID 列表（数字或字符串，最多 100 个）
        client_id: 可选，店铺 Client-Id
        api_key: 可选，店铺 API-Key

    Returns:
        list: 商品详情列表，每个元素结构：
            {
                "id": 123, "offer_id": "SKU", "name": "标题",
                "primary_image": "图片URL", "status": "active",
                "price": {"price": "999.00", "old_price": "1299.00", "marketing_price": "..."},
                "stocks": [{"present": 100, "reserved": 0, "type": "fbo"}],
                "sources": [{"source": "ozon", "count": 50}],  # 销量来源
                "category_id": 12345,
                ...
            }
    """
    # 转为 int 列表，去重，限 100 个
    ids = []
    seen = set()
    for pid in product_ids:
        try:
            pid_int = int(pid)
            if pid_int not in seen:
                seen.add(pid_int)
                ids.append(pid_int)
        except (ValueError, TypeError):
            continue
        if len(ids) >= 100:
            break

    if not ids:
        return []

    result = _call_ozon_api('/v3/product/info/list', {
        'product_id': ids,
    }, client_id, api_key)

    # v3 返回 {"items": [...]}（注意：不是 {"result": [...]}）
    return (result.get('items') or result.get('result') or [])


def get_product_info(product_id=None, offer_id=None, client_id=None, api_key=None):
    """获取单个商品详情（通过 product_id 或 offer_id）

    Args:
        product_id: Ozon 商品 ID（数字）
        offer_id: 卖家 SKU（字符串）
        两者传其一即可。

    Returns:
        dict: 商品详情（结构同 get_product_info_list 返回的单个元素）
    """
    body = {}
    if product_id:
        try:
            body['product_id'] = int(product_id)
        except (ValueError, TypeError):
            pass
    if offer_id:
        body['offer_id'] = str(offer_id)
    if not body:
        raise OzonAPIError('必须提供 product_id 或 offer_id 之一')

    # /v3/product/info 已下线；单品查询统一复用仍可用的 list 接口。
    if body.get('product_id'):
        rows = get_product_info_list([body['product_id']], client_id, api_key)
        return rows[0] if rows else {}
    result = _call_ozon_api('/v3/product/info/list', {
        'offer_id': [body['offer_id']],
    }, client_id, api_key)
    rows = result.get('items') or []
    return rows[0] if rows else {}


def get_product_attributes(product_id, client_id=None, api_key=None):
    """读取已发布商品的完整属性，供在线商品反向编辑回填。"""
    result = _call_ozon_api('/v4/product/info/attributes', {
        'filter': {'product_id': [str(product_id)]},
        'limit': 100,
        'last_id': '',
    }, client_id, api_key)
    rows = result.get('result') or result.get('items') or []
    if isinstance(rows, dict):
        return rows.get('items', []) or []
    return rows if isinstance(rows, list) else []


def get_product_description(product_id, client_id=None, api_key=None):
    """读取 Ozon 商品描述；部分旧商品可能不返回富内容字段。"""
    result = _call_ozon_api('/v1/product/info/description', {
        'product_id': int(product_id),
    }, client_id, api_key)
    return result.get('result', {}) or {}


def get_product_content_ratings(skus, client_id=None, api_key=None):
    """按 Ozon SKU 批量读取内容评分。未计算评分的商品可能不会返回。"""
    normalized = []
    for sku in skus or []:
        try:
            normalized.append(int(sku))
        except (TypeError, ValueError):
            continue
    if not normalized:
        return []
    data = _call_ozon_api('/v1/product/rating-by-sku', {
        'skus': normalized[:100],
    }, client_id, api_key)
    result = data.get('result', data)
    if isinstance(result, dict):
        return result.get('products') or result.get('items') or result.get('ratings') or []
    return result if isinstance(result, list) else []


# Ozon 原始状态 → 前端展示状态映射
OZON_STATUS_TO_FRONTEND = {
    'pending': 'reviewing',         # 待审核
    'validation': 'reviewing',      # 校验中
    'imported': 'ready',            # 已导入，待上架
    'processing': 'reviewing',      # 处理中
    'active': 'onsale',             # 在售
    'not_processed': 'ready',       # 待处理（未上架）
    'failed': 'rejected',           # 失败
    'rejected': 'rejected',         # 审核不通过
    'visible': 'onsale',
    'in_sale': 'onsale',
    'ready_to_supply': 'ready',
    'to_supply': 'ready',
    'moderated': 'ready',
    'not_moderated': 'reviewing',
    'validation_state_pending': 'reviewing',
    'validation_state_fail': 'rejected',
    'state_failed': 'rejected',
    'moderation_block': 'rejected',
    'disabled': 'offline',
    'invisible': 'offline',
    'empty_stock': 'offline',
    'removed_from_sale': 'offline',
    'banned': 'offline',
    'quarantine': 'offline',
    'archived': 'archived',
}


def map_ozon_status_to_frontend(ozon_status):
    """将 Ozon 原始状态映射为前端 6 种展示状态"""
    if not ozon_status:
        return 'reviewing'
    return OZON_STATUS_TO_FRONTEND.get(str(ozon_status).strip().lower(), 'reviewing')


# ========== 订单（FBS / FBO 发货单）==========
def list_fbs_postings(filter_body=None, limit=100, offset=0, with_data=True,
                      client_id=None, api_key=None):
    """拉取 FBS 发货单列表

    Ozon 接口: POST /v3/posting/fbs/list
    Args:
        filter_body: 筛选条件，如 {"status": "awaiting_packaging"} 或
                     {"since": "2026-08-01T00:00:00.000Z", "to": "..."} 等
        limit: 每页数量（接口上限 1000）
        offset: 偏移量
        with_data: 是否附带 analytics_data / financial_data
    Returns:
        dict: { "result": { "postings": [...], "has_next": true } }
    """
    body = {
        'dir': 'desc',
        'filter': filter_body or {},
        'limit': max(1, min(int(limit), 1000)),
        'offset': int(offset),
    }
    if with_data:
        body['with'] = {'analytics_data': True, 'financial_data': True}
    return _call_ozon_api('/v3/posting/fbs/list', body, client_id, api_key)


def get_fbs_posting_detail(posting_number, with_data=True, client_id=None, api_key=None):
    """获取 FBS 发货单详情

    Ozon 接口: POST /v3/posting/fbs/get
    """
    body = {'posting_number': posting_number}
    if with_data:
        body['with'] = {'analytics_data': True, 'financial_data': True}
    return _call_ozon_api('/v3/posting/fbs/get', body, client_id, api_key)


def ship_fbs_postings(posting_number, products, client_id=None, api_key=None):
    """FBS 发货（出库）

    Ozon 接口: POST /v3/posting/fbs/ship
    Args:
        posting_number: 发货单号
        products: 出库商品列表，如 [{"product_id": 123456, "quantity": 2}]
                  product_id 为 Ozon 侧商品 SKU（订单详情 products 中返回）
    Returns:
        dict: { "result": true }
    """
    body = {
        'posting_number': posting_number,
        'products': products,
    }
    return _call_ozon_api('/v3/posting/fbs/ship', body, client_id, api_key)


def list_fbo_postings(filter_body=None, limit=100, offset=0, with_data=True,
                      client_id=None, api_key=None):
    """拉取 FBO 发货单列表

    Ozon 接口: POST /v2/posting/fbo/list
    """
    body = {
        'dir': 'desc',
        'filter': filter_body or {},
        'limit': max(1, min(int(limit), 1000)),
        'offset': int(offset),
    }
    if with_data:
        body['with'] = {'analytics_data': True, 'financial_data': True}
    return _call_ozon_api('/v2/posting/fbo/list', body, client_id, api_key)


def get_fbo_posting_detail(posting_number, with_data=True, client_id=None, api_key=None):
    """获取 FBO 发货单详情

    Ozon 接口: POST /v2/posting/fbo/get
    """
    body = {'posting_number': posting_number}
    if with_data:
        body['with'] = {'analytics_data': True, 'financial_data': True}
    return _call_ozon_api('/v2/posting/fbo/get', body, client_id, api_key)


# ========== 财务（交易明细） ==========
def list_finance_transactions(date_from, date_to, transaction_type='all',
                              page=1, page_size=1000, client_id=None, api_key=None):
    """拉取 Ozon 财务交易明细（逐笔佣金/服务费/物流/退货扣款等）

    Ozon 接口: POST /v3/finance/transaction/list（v2 同名接口已下线，返回 404）
    注意：Ozon 限制单次查询区间不超过 1 年；date_from/date_to 为 YYYY-MM-DD，
    内部转换为 ISO8601 UTC。分页采用 page/page_size（每页最多 1000）。
    """
    body = {
        'filter': {
            'date': {
                'from': f'{date_from}T00:00:00.000Z',
                'to': f'{date_to}T23:59:59.999Z',
            },
            'operation_type': [],
            'posting_number': '',
            'transaction_type': transaction_type,
        },
        'page': max(1, int(page)),
        'page_size': max(1, min(int(page_size), 1000)),
    }
    return _call_ozon_api('/v3/finance/transaction/list', body, client_id, api_key)


# ============================================================================
# 财务应计明细（新接口）
# ============================================================================
# ⚠️ 背景：Ozon 已于 2026-09-08 停用 /v3/finance/transaction/list 与
#    /v3/finance/transaction/totals，调用会返回：
#        obsolete method cannot be used
#    官方给出的替代方法是 /v1/finance/accrual/postings、/types、/by-day。
#    注意这**不是简单的版本号替换**——请求参数与返回结构都变了：
#      · by-day 一次只接受【单日】（Date 必须 10 位 YYYY-MM-DD），没有区间参数
#      · 详情要按天循环拉取，或改用 postings（按发货单号，1~200 个/次）
#      · 金额从 amount 变成 total_amount.amount，费用细项在 item_fees / non_item_fee
# ============================================================================

def get_finance_accruals_by_day(date_str, client_id=None, api_key=None, last_id=''):
    """按【单日】拉取 Ozon 应计明细

    Ozon 接口: POST /v1/finance/accrual/by-day
    请求体: {date: 'YYYY-MM-DD'[, last_id: '...']}
    响应:   {accruals: [...], last_id: ''}   last_id 非空表示还有下一页

    Args:
        date_str: 'YYYY-MM-DD'（必须正好 10 个字符，传区间会 400）
    """
    body = {'date': str(date_str)}
    if last_id:
        body['last_id'] = str(last_id)
    return _call_ozon_api('/v1/finance/accrual/by-day', body, client_id, api_key)


def get_finance_accrual_types(client_id=None, api_key=None):
    """拉取应计类型字典（一次返回全部，实测约 124 种）

    Ozon 接口: POST /v1/finance/accrual/types（无参数）
    响应:   {accrual_types: [{id, name, description}]}
            name 是英文机器名（如 Acquiring），description 是俄文说明（如 Эквайринг）
    """
    return _call_ozon_api('/v1/finance/accrual/types', {}, client_id, api_key)


def get_finance_accruals_by_postings(posting_numbers, client_id=None, api_key=None):
    """按发货单号批量拉取应计明细（1~200 个/次）

    Ozon 接口: POST /v1/finance/accrual/postings
    单号格式受正则约束：^[0-9]{1,32}-[0-9]{1,32}-[0-9]{1,32}$
    """
    numbers = [str(p) for p in (posting_numbers or [])][:200]
    if not numbers:
        raise ValueError('posting_numbers 不能为空')
    return _call_ozon_api('/v1/finance/accrual/postings',
                          {'posting_numbers': numbers}, client_id, api_key)

# ========== 促销活动（Actions / Promos）==========
# 用于「自动移除促销」功能：读取活动、识别平台自动拉入(AUTO)的商品、移除出活动。
# 接口实测要点（Client-Id 5057369）：
#   - GET  /v1/actions                           活动清单（含 auto_add_dates）
#   - POST /v1/actions/products                  活动内商品（含 add_mode: AUTO/MANUAL）
#   - POST /v1/actions/products/deactivate       移除商品出活动（{action_id, product_ids[]}）
#   - POST /v1/actions/auto-add/products/candidates  自动添加候选（action_id + auto_add_date 均必填）
#   - POST /v1/actions/auto-add/products/delete  从自动添加名单移除（需 auto_add_date）
# 注意：Ozon 没有删除活动本身的接口，只能把商品移出活动。


def get_actions(client_id=None, api_key=None):
    """拉取店铺的全部促销活动清单

    Ozon 接口: GET /v1/actions （注意：该接口只支持 GET，POST 返回 405）

    Returns:
        list: 活动列表，每个元素包含：
            id, title, date_start, date_end, potential_products_count,
            is_participating, participating_products_count, description(HTML),
            action_type, banned_products_count, with_targeting, discount_type,
            discount_value, order_amount, freeze_date, is_voucher_action,
            auto_add_dates: ["2026-09-17T21:00:00Z", ...]
    """
    data = _call_ozon_api_get('/v1/actions', client_id, api_key)
    if isinstance(data, dict):
        return data.get('result') or []
    if isinstance(data, list):
        return data
    return []


def get_action_products(action_id, limit=1000, offset=0, client_id=None, api_key=None):
    """拉取某个活动下的商品列表（含 add_mode，用于识别平台自动���入）

    Ozon 接口: POST /v1/actions/products

    Args:
        action_id: 活动 ID
        limit: 单页数量（Ozon 上限 1000）
        offset: 偏移量

    Returns:
        dict: {
            "products": [{
                id, price, action_price, max_action_price, add_mode,
                stock, min_stock, alert_max_action_price_failed,
                alert_max_action_price, current_boost,
                price_min_elastic, price_max_elastic, min_boost, max_boost
            }, ...],
            "total": int,
            "last_id": ...
        }
    """
    body = {
        'action_id': int(action_id),
        'limit': max(1, min(int(limit), 1000)),
        'offset': int(offset),
    }
    data = _call_ozon_api('/v1/actions/products', body, client_id, api_key)
    result = (data or {}).get('result') or {}
    return {
        'products': result.get('products') or [],
        'total': result.get('total') or 0,
        'last_id': result.get('last_id'),
    }


def get_all_action_products(action_id, client_id=None, api_key=None, page_size=1000):
    """分页拉取某个活动下的全部商品

    Returns:
        list: 商品列表（已翻完所有页）
    """
    out = []
    offset = 0
    total = None
    while True:
        page = get_action_products(action_id, limit=page_size, offset=offset,
                                   client_id=client_id, api_key=api_key)
        items = page.get('products') or []
        out.extend(items)
        if total is None:
            total = page.get('total') or len(items)
        offset += len(items)
        if not items or offset >= (total or 0) or len(items) < page_size:
            break
        if offset > 100000:  # 安全阀，防死循环
            break
    return out


def deactivate_action_products(action_id, product_ids, client_id=None, api_key=None):
    """将商品移出促销活动

    Ozon 接口: POST /v1/actions/products/deactivate
    注意：product_ids 单次 1~1000 个，超过会返回参数校验错误。

    Args:
        action_id: 活动 ID
        product_ids: 商品 ID 列表（Ozon product_id，整数）

    Returns:
        dict: 接口原始返回
    """
    ids = [int(x) for x in (product_ids or [])]
    if not ids:
        raise OzonAPIError('移除活动商品失败：product_ids 不能为空', 400)
    if len(ids) > 1000:
        raise OzonAPIError('移除活动商品失败：单次最多 1000 个商品', 400)
    return _call_ozon_api('/v1/actions/products/deactivate', {
        'action_id': int(action_id),
        'product_ids': ids,
    }, client_id, api_key)


def get_auto_add_candidates(action_id, auto_add_date, limit=1000, offset=0,
                            client_id=None, api_key=None):
    """拉取「自动添加到活动」的候选商品名单

    Ozon 接口: POST /v1/actions/auto-add/products/candidates
    【注意】action_id 与 auto_add_date 均为必填，auto_add_date 必须是活动
    auto_add_dates 里的 ISO8601 时间戳（如 "2026-09-17T21:00:00Z"），
    传空字符串会报 proto: invalid google.protobuf.Timestamp value ""。

    Returns:
        dict: {
            "products": [{
                product_id, offer_id, sku, name, price, base_price,
                max_discount_price, min_seller_price, marketplace_seller_price,
                action_price_to_auto_add, min_action_quantity,
                quantity_to_auto_add, currency
            }, ...],
            "total": int
        }
    """
    if not auto_add_date:
        raise OzonAPIError('拉取自动添加候选失败：auto_add_date 不能为空', 400)
    body = {
        'action_id': int(action_id),
        'auto_add_date': auto_add_date,
        'limit': max(1, min(int(limit), 1000)),
        'offset': int(offset),
    }
    data = _call_ozon_api('/v1/actions/auto-add/products/candidates', body,
                          client_id, api_key)
    result = (data or {}).get('result') or data or {}
    return {
        'products': result.get('products') or [],
        'total': result.get('total') or 0,
    }


def delete_auto_add_products(action_id, auto_add_date, product_ids,
                             client_id=None, api_key=None):
    """把商品从「自动添加到活动」的名单中移除

    Ozon 接口: POST /v1/actions/auto-add/products/delete
    【注意】需同时提供 auto_add_date。

    Args:
        action_id: 活动 ID
        auto_add_date: 活动自动添加时间戳（ISO8601，取自 auto_add_dates）
        product_ids: 商品 ID 列表

    Returns:
        dict: 接口原始返回
    """
    if not auto_add_date:
        raise OzonAPIError('移除自动添加商品失败：auto_add_date 不能为空', 400)
    ids = [int(x) for x in (product_ids or [])]
    if not ids:
        raise OzonAPIError('移除自动添加商品失败：product_ids 不能为空', 400)
    return _call_ozon_api('/v1/actions/auto-add/products/delete', {
        'action_id': int(action_id),
        'auto_add_date': auto_add_date,
        'product_ids': ids,
    }, client_id, api_key)
