"""
AI 模型管理 API 路由
提供 AI 提供商配置的 CRUD、激活切换、连接测试等接口
整个系统共用一份配置，由 ai_config_service 统一管理持久化
"""
from flask import Blueprint, request
import json
import urllib.error
import urllib.request
from utils.response import success_response, error_response, handle_errors
from services import ai_config_service

model_bp = Blueprint('model', __name__)


@model_bp.route('/ai/models/<provider_key>/available', methods=['GET'])
@handle_errors
def get_available_models(provider_key):
    """读取提供商的 OpenAI-compatible /models 列表，不返回 API Key。"""
    config = ai_config_service.get_ai_config(provider_key)
    if not config.get('api_key') or not config.get('base_url'):
        return error_response(msg='请先配置 API Key 和 Base URL', code=400)
    url = config['base_url'].rstrip('/') + '/models'
    req = urllib.request.Request(url, headers={'Authorization': f"Bearer {config['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:300]
        return error_response(msg=f'读取模型失败 HTTP {exc.code}: {detail}', code=502)
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        return error_response(msg=f'读取模型失败: {exc}', code=502)
    raw_models = payload.get('data', payload if isinstance(payload, list) else [])
    models = []
    for item in raw_models:
        model_id = item.get('id') if isinstance(item, dict) else str(item)
        if model_id and model_id not in models:
            models.append(model_id)
    return success_response(data={'models': models, 'provider': provider_key}, msg=f'读取到 {len(models)} 个模型')


@model_bp.route('/ai/models', methods=['GET'])
@handle_errors
def get_models():
    """获取所有 AI 模型配置

    返回当前激活的提供商、所有提供商列表（API Key 脱敏）和最后更新时间
    """
    config = ai_config_service.get_all_config()
    return success_response(data=config, msg='获取配置成功')


@model_bp.route('/ai/models/<provider_key>', methods=['PUT'])
@handle_errors
def update_model(provider_key):
    """更新单个 AI 提供商配置

    请求体: {
        "api_key": "sk-xxx",       // 空 or ******** 表示不修改
        "base_url": "https://...",  // 可选
        "model": "deepseek-chat",   // 可选
        "enabled": true,            // 可选
        "name": "DeepSeek",         // 可选，仅自定义生效
        "description": "..."        // 可选
    }
    """
    body = request.get_json(silent=True) or {}
    ai_config_service.update_provider(provider_key, body)
    return success_response(msg='配置已保存')


@model_bp.route('/ai/models/active', methods=['PUT'])
@handle_errors
def set_active_model():
    """设置当前激活的 AI 提供商

    请求体: { "provider": "deepseek" }
    """
    body = request.get_json(silent=True) or {}
    provider_key = body.get('provider', '').strip()
    if not provider_key:
        return error_response(msg='缺少 provider 参数', code=400)
    try:
        ai_config_service.set_active_provider(provider_key)
        return success_response(msg=f'已切换至 {provider_key}')
    except ValueError as e:
        return error_response(msg=str(e), code=400)


@model_bp.route('/ai/models/<provider_key>', methods=['DELETE'])
@handle_errors
def delete_model(provider_key):
    """删除自定义 AI 提供商（内置提供商不可删除）"""
    try:
        ai_config_service.delete_provider(provider_key)
        return success_response(msg=f'已删除提供商 {provider_key}')
    except ValueError as e:
        return error_response(msg=str(e), code=400)


@model_bp.route('/ai/models/test', methods=['POST'])
@handle_errors
def test_model_connection():
    """测试 AI 提供商连接

    请求体: {
        "provider": "deepseek",
        // 可选：传入则测试临时配置，否则用已保存的配置
        "api_key": "sk-xxx",
        "base_url": "https://...",
        "model": "..."
    }
    """
    body = request.get_json(silent=True) or {}
    provider_key = body.get('provider', '').strip()
    api_key = body.get('api_key', '').strip()
    base_url = body.get('base_url', '').strip()
    model = body.get('model', '').strip()

    # 如果传入了完整的临时配置，直接测试
    if api_key and base_url and model:
        result = ai_config_service.test_provider_connection(
            provider_key, api_key=api_key, base_url=base_url, model=model
        )
    else:
        if not provider_key:
            return error_response(msg='缺少 provider 参数', code=400)
        result = ai_config_service.test_provider_connection(provider_key)

    if result.get('success'):
        return success_response(data=result, msg=result.get('message', '连接成功'))
    return error_response(msg=result.get('message', '连接失败'), code=400, data=result)


@model_bp.route('/ai/models/status', methods=['GET'])
@handle_errors
def get_models_status():
    """获取当前激活的 AI 提供商配置状态（轻量接口，供其他模块检查）

    返回: { "provider": "deepseek", "name": "DeepSeek", "has_key": true, "model": "deepseek-chat" }
    不返回完整 API Key，只返回是否已配置
    """
    config = ai_config_service.get_ai_config()
    return success_response(data={
        'provider': config.get('provider', ''),
        'name': config.get('name', ''),
        'model': config.get('model', ''),
        'has_key': bool(config.get('api_key', '')),
        'base_url': config.get('base_url', ''),
    })


@model_bp.route('/ai/generate-description', methods=['POST'])
@handle_errors
def generate_description():
    """AI 生成商品描述（C5）

    复用 AIService.generate('description', ...)，自动使用当前激活的 AI 提供商。
    未配置 API Key 或调用失败时返回错误。

    请求体:
      {
        "title": "商品标题",            # 必填，作为生成主输入
        "sourceDescription": "源描述",  # 可选，作为参考文本
        "platform": "ozon",            # 可选，默认 ozon
        "language": "ru"               # 可选，默认 ru（俄语）；可选 en/de/zh
      }

    返回:
      {
        "content": "生成的描述文本",
        "provider": "qwen",            # 实际使用的提供商 key
        "model": "qwen-plus",
        "language": "ru"
      }
    """
    body = request.get_json(silent=True) or {}
    title = (body.get('title') or '').strip()
    if not title:
        return error_response(msg='商品标题不能为空', code=400)

    source_desc = (body.get('sourceDescription') or '').strip()
    platform = body.get('platform') or 'ozon'
    language = body.get('language') or 'ru'

    # 拼接输入文本：标题 + 源描述（如有，截断防止 token 超限）
    text = title
    if source_desc:
        text = f'{title}\n\n[源商品描述参考]\n{source_desc[:1500]}'

    from services.ai_service import AIService

    config = ai_config_service.get_ai_config()

    content = AIService.generate('description', text, {
        'platform': platform,
        'language': language,
    })

    return success_response(data={
        'content': content,
        'provider': config.get('provider', ''),
        'model': config.get('model', ''),
        'language': language,
    }, msg='描述生成成功')


@model_bp.route('/ai/generate-listing', methods=['POST'])
@handle_errors
def generate_listing():
    """AI 生成中俄双语完整商品 Listing（商品卡片）

    融合自有精铺助手文案规范：俄语标题 ≤130 字符、标签下划线连接、禁极限词、
    纯客观描述。走当前激活的 AI 提供商（OpenAI 兼容接口）。

    请求体:
      {
        "title": "商品标题（中文必填）",
        "attributes": {"材质": "不锈钢", "颜色": "黑色"},   # 可选
        "hotKeywords": ["keyword1"],                       # 可选（OZON 热搜词）
        "description": "详细描述/图片分析结果"               # 可选
      }

    返回: { "content": "中俄双语完整文案（===分段===）" }
    """
    body = request.get_json(silent=True) or {}
    title = (body.get('title') or '').strip()
    if not title:
        return error_response(msg='商品标题不能为空', code=400)

    attrs = body.get('attributes') or {}
    hot = body.get('hotKeywords') or []
    desc = (body.get('description') or '').strip()

    attr_text = '，'.join(f'{k}:{v}' for k, v in attrs.items() if v) or '无'
    keywords_text = '，'.join(hot[:10]) if hot else '无'

    text = (
        f'商品标题：{title}\n'
        f'核心属性：{attr_text}\n'
        f'OZON 热搜关键词参考：{keywords_text}'
        + (f'\n商品详情/图片分析：{desc[:1500]}' if desc else '')
    )

    from services.ai_service import AIService

    config = ai_config_service.get_ai_config()
    content = AIService.generate('listing', text, {
        'platform': 'ozon',
        'language': 'ru',
    })

    return success_response(data={
        'content': content,
        'provider': config.get('provider', ''),
        'model': config.get('model', ''),
    }, msg='Listing 生成成功')


@model_bp.route('/ai/generate', methods=['POST'])
@handle_errors
def ai_generate():
    """AI 通用生成（AI 撰写页统一入口）

    依据 type 分发到 AIService 对应能力：
      title / description / keywords / translate / tags / listing

    请求体:
      {
        "type": "title",
        "content": "输入文本",
        "params": {"platform": "ozon", "language": "ru"}
      }

    返回: { "result": "生成文本" }
    """
    body = request.get_json(silent=True) or {}
    gen_type = (body.get('type') or 'title').strip()
    content = (body.get('content') or '').strip()
    if not content:
        return error_response(msg='输入内容不能为空', code=400)
    params = body.get('params') or {}

    from services.ai_service import AIService

    try:
        result = AIService.generate(gen_type, content, params)
    except RuntimeError as e:
        return error_response(msg=str(e), code=400)
    except Exception as e:
        return error_response(msg=f'AI 调用失败: {e}', code=500)

    if gen_type == 'listing':
        # 完整 Listing 返回结构化分段，便于前端拆分填充
        return success_response(data={'result': result, 'type': 'listing'}, msg='生成成功')

    return success_response(data={'result': result, 'type': gen_type}, msg='生成成功')
