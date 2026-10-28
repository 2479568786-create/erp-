"""
GeekOzon ERP - Agent 套图批量服务（R3）

复刻前端 `pages_media.js` 的 8 模块定义与 `buildAISuitePrompt()` 模板（2026-10-28 线上版），
服务端按模块并发调用现有凌科 AI 通道并落图库：
- 复用 `services/jlc_media_service.generate_image`（与前端单模块生成同一条通道）
- 复用 `routes.gallery_routes` 的落盘/建资产逻辑（写 product_id/module_key/request_id/agent_source）
- 复用 `services/image_billing_service` 的计费闸门与扣点（不绕过 image_usage/image_ops_log）
- 并发 SUITE_CONCURRENCY（默认 2），每模块失败重试 1 次，单模块失败不影响其它模块
- 幂等：(request_id, module_key) 已有成功资产则直接复用，不重复生成、不重复扣点
"""
import concurrent.futures as futures
import os
import re

from config import Config
from db import query
from services import image_billing_service as billing
from services import jlc_media_service

MAX_REFERENCES = 6
MAX_PROMPT_LEN = 3000
MAX_REFERENCE_CHARS = 14 * 1024 * 1024  # dataURL 字符串上限（≈10MB 原图）
AGENT_SUITE_SOURCE = 'agent-suite'

HERO_RULE = '采用 Ozon 主图构造：商品主体优先，干净浅色背景，少量高质量俄语信息。'
DETAIL_RULE = ('采用专业电商详情图构图：商品或真实局部清晰突出，主体完整且不被遮挡；'
               '根据当前模块目标设计有层次的背景、光影、信息区、图标和构图，不套用统一白卡。')

# 与前端 pages_media.js 的 AI_SUITE_MODULES 完全一致（2026-10-28 线上版）
AI_SUITE_MODULES = [
    {'key': 'hero', 'name': '首屏主视觉', 'ruTitle': 'Главное изображение',
     'description': '清晰展示商品与核心价值', 'textMode': '可无文字或仅保留短俄语标题',
     'instruction': 'Ozon 首图：浅色或白色背景，商品完整居中，四周留安全边距，不放大段文字；如需文字，只使用简短俄语标题。'},
    {'key': 'selling_point', 'name': '核心卖点图', 'ruTitle': 'Ключевые преимущества',
     'description': '俄语标题 + 真实卖点', 'textMode': '必须有俄语标题和 2-3 条短俄语卖点',
     'instruction': '详情卖点图：商品完整清晰，结合真实使用环境与克制的功能视觉元素表现产品价值，为后期文字排版预留干净区域。'},
    {'key': 'usage_scene', 'name': '真实使用场景', 'ruTitle': 'Сценарий использования',
     'description': '俄语标题 + 可信使用场景', 'textMode': '必须有俄语标题和 1-2 条短俄语说明',
     'instruction': '详情场景图：展示商品在真实生活环境中的使用方式，人物与环境不能遮挡商品，为后期文字排版预留干净区域。'},
    {'key': 'angle', 'name': '多角度图', 'ruTitle': 'Внешний вид и конструкция',
     'description': '俄语标题 + 结构展示', 'textMode': '必须有俄语标题和结构短标签',
     'instruction': '详情结构图：突出参考图中商品的外观与结构，可用合理的局部特写辅助，不改变商品型号，为后期文字排版预留干净区域。'},
    {'key': 'atmosphere', 'name': '场景氛围图', 'ruTitle': 'Сценарий применения',
     'description': '俄语标题 + 使用环境', 'textMode': '必须有俄语标题',
     'instruction': '详情氛围图：用克制、可信的环境强化商品用途，背景不能喧宾夺主，为后期文字排版预留干净区域。'},
    {'key': 'detail', 'name': '商品细节图', 'ruTitle': 'Детали и материалы',
     'description': '俄语标题 + 材质工艺细节', 'textMode': '必须有俄语标题和细节短标签',
     'instruction': '详情细节图：放大参考图中可见的材质、接口、纹理或做工，不虚构不可见结构，为后期文字排版预留干净区域。'},
    {'key': 'size', 'name': '尺寸/参数图', 'ruTitle': 'Размеры и характеристики',
     'description': '俄语标题 + 已确认参数', 'textMode': '必须有俄语标题，仅写已确认数据',
     'instruction': '详情参数图：清晰呈现商品主体，并为后期排入已确认参数预留整洁区域；不要自行生成尺寸线、数字或参数。'},
    {'key': 'accessories', 'name': '配件/赠品图', 'ruTitle': 'Комплектация',
     'description': '俄语标题 + 实际配件清单', 'textMode': '必须有俄语标题和实际配件名称',
     'instruction': '详情配件图：仅展示参考图或提示词明确存在的商品与配件，为后期文字排版预留整洁区域，不添加赠品。'},
]
MODULE_MAP = {item['key']: item for item in AI_SUITE_MODULES}


class AgentSuiteError(ValueError):
    """入参不合法（路由层映射 422）。"""


class AgentSuiteBillingError(AgentSuiteError):
    """计费不通过（路由层映射 403）。"""


def fallback_plan(module, user_prompt):
    """与前端 fallbackAISuitePlan 一致。"""
    return {
        'key': module['key'],
        'goal': module['description'],
        'composition': module['instruction'],
        'copy': '%s\n%s' % (module['ruTitle'], module['textMode']),
        'facts': user_prompt,
    }


def build_suite_prompt(module, user_prompt, ratio, plan=None):
    """与前端 buildAISuitePrompt 完全一致的模板（勿改措辞，前端/后端必须同源）。"""
    plan = plan or {}
    template_rules = HERO_RULE if module['key'] == 'hero' else DETAIL_RULE
    return (
        '当前只生成一个模块：%s（%s）。不要执行其他模块的任务。\n\n'
        '当前模块目标：%s\n'
        '当前模块构图：%s\n'
        '当前模块允许使用的真实事实：%s\n'
        '当前模块俄语文案：%s\n'
        '%s\n'
        '模型必须一次性完成商品、背景、构图、光影、俄语文字、图标和信息层级的最终成图；'
        '不要把文字、图标或版式留给 ERP 后期，不要输出无文字底图。'
        '严格使用上面的俄语文案，文字必须清晰、完整、可读，只能使用俄语西里尔字母，'
        '不得出现中文、乱码或无意义伪文字。\n'
        '画布必须使用 %s 比例。参考图是最高优先级，必须复制参考图中实际可见的商品、颜色、结构和配件；'
        '参考图中没有的物品一律禁止新增；如果参考图中的配件是入耳式或有线耳机，必须保持原样，禁止改成头戴式耳机。'
        '只使用当前模块事实；不添加未提供的参数、认证、配件或功能；'
        '不要添加水印、价格、折扣、二维码、联系方式或购买引导。'
    ) % (
        module['name'], module['ruTitle'],
        plan.get('goal') or module['description'],
        plan.get('composition') or module['instruction'],
        plan.get('facts') or user_prompt,
        plan.get('copy') or module['textMode'],
        template_rules,
        ratio,
    )


def _validate_payload(payload):
    if not isinstance(payload, dict):
        raise AgentSuiteError('请求体必须是 JSON 对象')
    request_id = str(payload.get('request_id') or '').strip()
    if not request_id:
        raise AgentSuiteError('request_id 必填（幂等键）')
    prompt = str(payload.get('prompt') or '').strip()
    if not prompt:
        raise AgentSuiteError('prompt 必填')
    if len(prompt) > MAX_PROMPT_LEN:
        raise AgentSuiteError('prompt 不能超过 %d 字' % MAX_PROMPT_LEN)

    raw_modules = payload.get('modules')
    if not isinstance(raw_modules, list) or not raw_modules:
        raise AgentSuiteError('modules 必须是至少 1 个模块的数组')
    modules = []
    for item in raw_modules:
        key = str(item).strip()
        if key not in MODULE_MAP:
            raise AgentSuiteError('未知模块：%s（可用：%s）' % (key, ','.join(MODULE_MAP)))
        if key not in modules:
            modules.append(key)

    refs = payload.get('reference_images')
    if not isinstance(refs, list) or not (1 <= len(refs) <= MAX_REFERENCES):
        raise AgentSuiteError('reference_images 需要 1-%d 张' % MAX_REFERENCES)
    normalized = []
    for item in refs:
        value = item.get('url') if isinstance(item, dict) else item
        if not isinstance(value, str) or not value.strip():
            raise AgentSuiteError('参考图格式不正确（需要 dataURL 或公网 URL）')
        value = value.strip()
        if not (value.startswith('data:image/') or re.match(r'^https?://', value)):
            raise AgentSuiteError('参考图格式不正确（需要 dataURL 或公网 URL）')
        if len(value) > MAX_REFERENCE_CHARS:
            raise AgentSuiteError('单张参考图不能超过 10MB')
        normalized.append(value)

    model = str(payload.get('model') or 'banana-2.1').strip() or 'banana-2.1'
    ratio = str(payload.get('aspect_ratio') or '3:4').strip() or '3:4'
    image_size = str(payload.get('image_size') or '2K').strip() or '2K'
    product_id = str(payload.get('product_id') or '').strip() or None
    return {
        'request_id': request_id,
        'prompt': prompt,
        'modules': modules,
        'references': normalized,
        'model': model,
        'ratio': ratio,
        'image_size': image_size,
        'product_id': product_id,
    }


def _existing_assets(request_id, modules):
    if not modules:
        return {}
    placeholders = ','.join('?' * len(modules))
    rows = query(
        """SELECT * FROM gallery_assets
           WHERE request_id = ? AND agent_source = ? AND module_key IN (%s)""" % placeholders,
        tuple([request_id, AGENT_SUITE_SOURCE] + list(modules)),
    )
    return {row['module_key']: row for row in rows}


def _asset_result(row, cached=True):
    return {
        'module': row['module_key'],
        'status': 'done',
        'asset_id': str(row['id']),
        'url': '/api/gallery/files/%s' % row['filename'],
        'cached': cached,
    }


def _generate_one(module_key, prompt, ratio, image_size, references, model,
                  account_id, product_id, request_id):
    module = MODULE_MAP[module_key]
    generation_prompt = build_suite_prompt(module, prompt, ratio, fallback_plan(module, prompt))
    last_error = None
    for _attempt in (1, 2):
        try:
            result = jlc_media_service.generate_image(
                generation_prompt,
                aspect_ratio=ratio,
                image_size=image_size,
                images=references,
                model=model,
            )
            urls = result.get('urls') or []
            if not urls:
                raise AgentSuiteError('模型未返回图片')
            # 复用 gallery_routes 的下载/落盘逻辑（避免复制一套文件写入）
            from routes.gallery_routes import _download_generated_image, _save_bytes, image_extension

            data = _download_generated_image(urls[0])
            extension = image_extension(data)
            asset = _save_bytes(
                data,
                'agent-suite-%s.%s' % (module_key, extension),
                'ai',
                generation_prompt,
                ['AI套图', module_key],
                provider='jlc',
                model=result.get('model') or model,
                endpoint='/v1/media/generate',
                reference_count=len(references),
                template_version='suite-v1',
                product_id=product_id,
                module_key=module_key,
                request_id=request_id,
                agent_source=AGENT_SUITE_SOURCE,
                user_id=account_id,
            )
            return {'module': module_key, 'status': 'done', 'asset_id': asset['id'],
                    'url': asset['url'], 'model': result.get('model') or model}
        except Exception as exc:  # noqa: BLE001 - 单模块失败不影响其它模块
            last_error = exc
    return {'module': module_key, 'status': 'failed', 'error': str(last_error)[:200]}


def resolve_billing_account_id(payload, token_account_id=None):
    """按「商品所属账号」解析计费账号；解析不到再回退令牌绑定账号。"""
    explicit = payload.get('billing_account_id') if isinstance(payload, dict) else None
    if explicit not in (None, ''):
        try:
            explicit_id = int(explicit)
        except (TypeError, ValueError):
            raise AgentSuiteError('billing_account_id 必须是整数')
        if query('SELECT id FROM accounts WHERE id = ?', (explicit_id,), one=True):
            return explicit_id
        raise AgentSuiteError('billing_account_id 对应账号不存在')

    product_id = (payload or {}).get('product_id')
    if product_id:
        owner = _product_owner_account_id(str(product_id))
        if owner:
            return owner
    return token_account_id


def _product_owner_account_id(product_id):
    """商品归属账号：先看 assignee（用户名/昵称），再看 storeId → stores.account_id。"""
    try:
        from models.product import Product

        product = Product.find_by_id(product_id)
    except Exception:
        return None
    if not product:
        return None

    assignee = str(product.get('assignee') or '').strip()
    if assignee:
        row = query(
            'SELECT id FROM accounts WHERE username = ? OR nickname = ? LIMIT 1',
            (assignee, assignee), one=True,
        )
        if row:
            return int(row['id'])

    raw_store = product.get('storeId')
    if raw_store not in (None, ''):
        row = None
        try:
            row = query('SELECT account_id FROM stores WHERE id = ?', (int(raw_store),), one=True)
        except (TypeError, ValueError):
            row = None
        if not row:
            row = query('SELECT account_id FROM stores WHERE store_id = ?', (str(raw_store),), one=True)
        if row and row.get('account_id'):
            return int(row['account_id'])
    return None


def generate_suite(payload, account_id):
    """R3 主流程，返回 {request_id, ok, total, results, points, warnings}。"""
    data = _validate_payload(payload)
    account_id = resolve_billing_account_id(payload, account_id)
    request_id = data['request_id']

    existing = _existing_assets(request_id, data['modules'])
    pending = [key for key in data['modules'] if key not in existing]
    results = [_asset_result(existing[key]) for key in data['modules'] if key in existing]
    warnings = []
    if not pending:
        warnings.append('该 request_id 的模块已全部生成过，直接复用（未重复扣点）')
        return {
            'request_id': request_id,
            'ok': True,
            'total': len(data['modules']),
            'results': sorted(results, key=lambda r: data['modules'].index(r['module'])),
            'points': 0.0,
            'billing_account_id': account_id,
            'warnings': warnings,
        }

    # 计费闸门：按待生成张数预检（agent token 必须绑定 account_id）
    account = query('SELECT * FROM accounts WHERE id = ?', (account_id,), one=True) if account_id else None
    if not account:
        raise AgentSuiteBillingError('Agent 令牌未绑定计费账号，无法生图（请用 --account-id 重新签发或绑定）')
    allowed, reason = billing.can_generate(account, images=len(pending), model=data['model'])
    if not allowed:
        raise AgentSuiteBillingError(reason)

    try:
        concurrency = int(getattr(Config, 'SUITE_CONCURRENCY', 2) or 2)
    except (TypeError, ValueError):
        concurrency = 2
    concurrency = max(1, min(concurrency, len(pending)))

    with futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_map = {
            pool.submit(_generate_one, key, data['prompt'], data['ratio'], data['image_size'],
                        data['references'], data['model'], account_id, data['product_id'],
                        request_id): key
            for key in pending
        }
        for future in futures.as_completed(future_map):
            results.append(future.result())

    # 按请求模块顺序返回
    order = {key: idx for idx, key in enumerate(data['modules'])}
    results.sort(key=lambda item: order.get(item['module'], 999))

    done_count = sum(1 for item in results if item['status'] == 'done')
    points = 0.0
    if done_count:
        try:
            info = billing.charge(account_id, done_count, provider='lingkeai',
                                  model=data['model'], source='ai')
            points = float(info.get('cost_points') or 0)
        except Exception as exc:  # noqa: BLE001 - 计费异常不改写生成结果
            warnings.append('扣点失败，请人工核对：%s' % exc)

    if any(item['status'] != 'done' for item in results):
        warnings.append('部分模块失败，可直接用同一 request_id 重试（成功的不会重复生成/扣点）')

    return {
        'request_id': request_id,
        'ok': all(item['status'] == 'done' for item in results),
        'total': len(data['modules']),
        'results': results,
        'points': round(points, 4),
        'billing_account_id': account_id,
        'warnings': warnings,
    }
