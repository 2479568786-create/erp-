"""
AI 模型配置服务
统一管理所有 AI 提供商的 API 配置，整个系统共用一份配置。
配置持久化到 JSON 文件，前端通过模型管理页面进行设置。
"""
import json
import os
from datetime import datetime
from config import DATA_DIR

# 配置文件路径
AI_CONFIG_FILE = os.path.join(DATA_DIR, 'ai_models.json')

# 内置预设提供商（首次加载时使用）
_PRESET_PROVIDERS = {
    'deepseek': {
        'name': 'DeepSeek',
        'api_key': '',
        'base_url': 'https://api.deepseek.com/v1',
        'model': 'deepseek-chat',
        'enabled': True,
        'description': '深度求索 - 性价比高，中文理解强',
    },
    'qwen': {
        'name': '通义千问',
        'api_key': '',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'model': 'qwen-plus',
        'enabled': True,
        'description': '阿里云通义千问 - 阿里旗下大模型',
    },
    'openai': {
        'name': 'OpenAI',
        'api_key': '',
        'base_url': 'https://api.openai.com/v1',
        'model': 'gpt-4o-mini',
        'enabled': False,
        'description': 'OpenAI GPT 系列 - 需要海外网络',
    },
    'yandexgpt': {
        'name': 'YandexGPT',
        'api_key': '',
        'base_url': 'https://ai.api.cloud.yandex.net/foundationModels/v1',
        'model': 'gpt://b1g4fd2oie4j24c3oehb/yandexgpt-lite',
        'enabled': True,
        'description': 'Yandex AI Studio - 适合俄语电商文案',
    },
    'custom': {
        'name': '自定义',
        'api_key': '',
        'base_url': '',
        'model': '',
        'enabled': False,
        'description': '任何 OpenAI 兼容接口',
    },
    'lingkeai': {
        'name': '凌科 AI',
        'api_key': '',
            'base_url': 'https://api.lk888.ai/v1',
        'model': '',
        'enabled': True,
        'description': '凌科 AI OpenAI 兼容接口；模型代号以其开发文档为准',
    },
}


def _load_config():
    """加载配置文件，不存在则初始化默认配置"""
    if not os.path.exists(AI_CONFIG_FILE):
        # 首次初始化：从环境变量迁移已有配置
        config = {
            'active_provider': os.environ.get('AI_PROVIDER', 'deepseek'),
            'providers': _clone_presets(),
            'updated_at': datetime.now().isoformat(),
        }
        # 迁移 .env 中的 API Key
        if os.environ.get('DEEPSEEK_API_KEY'):
            config['providers']['deepseek']['api_key'] = os.environ['DEEPSEEK_API_KEY']
        if os.environ.get('QWEN_API_KEY'):
            config['providers']['qwen']['api_key'] = os.environ['QWEN_API_KEY']
        _save_config(config)
        return config

    try:
        with open(AI_CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        # 兼容性：确保所有预设提供商都存在
        changed = False
        for key, preset in _PRESET_PROVIDERS.items():
            if key not in config.get('providers', {}):
                config.setdefault('providers', {})[key] = _clone_one(preset)
                changed = True
        if changed:
            _save_config(config)
        return config
    except Exception as e:
        print(f'[AI配置] 加载失败，使用默认配置: {e}')
        return {
            'active_provider': 'deepseek',
            'providers': _clone_presets(),
            'updated_at': datetime.now().isoformat(),
        }


def _clone_presets():
    """深拷贝预设提供商"""
    return {k: _clone_one(v) for k, v in _PRESET_PROVIDERS.items()}


def _clone_one(item):
    """深拷贝单个项"""
    return json.loads(json.dumps(item))


def _save_config(config):
    """保存配置到文件"""
    config['updated_at'] = datetime.now().isoformat()
    try:
        with open(AI_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[AI配置] 保存失败: {e}')


def get_ai_config(provider_key=None):
    """获取当前生效的 AI 提供商配置

    返回:
        dict: { api_key, base_url, model, provider, name }
    """
    config = _load_config()
    provider_key = provider_key or config.get('active_provider', 'deepseek')
    providers = config.get('providers', {})

    provider = providers.get(provider_key, {})
    if not provider or not provider.get('enabled', True):
        return {
            'provider': provider_key,
            'name': '',
            'api_key': '',
            'base_url': '',
            'model': '',
        }

    return {
        'provider': provider_key,
        'name': provider.get('name', ''),
        'api_key': provider.get('api_key', ''),
        'base_url': provider.get('base_url', ''),
        'model': provider.get('model', ''),
    }


def get_all_config():
    """获取完整配置（用于前端展示，API Key 会被脱敏）"""
    config = _load_config()
    result = {
        'active_provider': config.get('active_provider', 'deepseek'),
        'providers': {},
        'updated_at': config.get('updated_at', ''),
    }
    for key, p in config.get('providers', {}).items():
        result['providers'][key] = {
            'name': p.get('name', ''),
            'api_key': _mask_key(p.get('api_key', '')),
            'base_url': p.get('base_url', ''),
            'model': p.get('model', ''),
            'enabled': p.get('enabled', True),
            'description': p.get('description', ''),
            'has_key': bool(p.get('api_key', '')),
            'models': _models_with_points(p.get('models'), config),
        }
    return result


def _models_with_points(models, config):
    """模型清单 + 每张点数（前端下拉直接展示单价）。"""
    billing = (config or {}).get('image_billing') or {}
    price_map = billing.get('price_points_by_model') or {}
    default_price = billing.get('price_points_per_image')
    out = _public_models(models)
    for item in out:
        try:
            points = float(price_map.get(item['id'], default_price))
        except (TypeError, ValueError):
            points = 0.0
        item['points'] = round(points, 4)
    return out


def _public_models(models):
    """对外暴露的模型清单（不含 key），供前端下拉选择。"""
    out = []
    for item in models or []:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get('id') or '').strip()
        if not model_id:
            continue
        out.append({
            'id': model_id,
            'label': str(item.get('label') or model_id),
            'enabled': bool(item.get('enabled', True)),
            'has_key': bool(str(item.get('api_key') or '').strip()),
        })
    return out


def get_image_models(provider_key='lingkeai'):
    """生图模型清单（含每张点数），前后端共用；没有 models 配置时回退单模型。"""
    config = _load_config()
    provider = (config.get('providers') or {}).get(provider_key) or {}
    billing = config.get('image_billing') or {}
    price_map = billing.get('price_points_by_model') or {}
    default_price = billing.get('price_points_per_image')
    models = _public_models(provider.get('models'))
    if not models and provider.get('model'):
        models = [{'id': provider.get('model'), 'label': provider.get('model'),
                   'enabled': True, 'has_key': bool(provider.get('api_key'))}]
    out = []
    for item in models:
        try:
            points = float(price_map.get(item['id'], default_price))
        except (TypeError, ValueError):
            points = 0.0
        entry = dict(item)
        entry['points'] = round(points, 4)
        out.append(entry)
    return out


def update_provider(provider_key, data):
    """更新单个提供商配置

    Args:
        provider_key: 提供商标识 (deepseek/qwen/openai/custom)
        data: { api_key, base_url, model, enabled, name, description }
    """
    config = _load_config()
    providers = config.setdefault('providers', {})

    if provider_key not in providers:
        # 新增自定义提供商
        providers[provider_key] = {
            'name': data.get('name', provider_key),
            'api_key': '',
            'base_url': '',
            'model': '',
            'enabled': True,
            'description': data.get('description', ''),
        }

    provider = providers[provider_key]

    # 更新字段（空 API Key 表示不修改）
    if 'name' in data:
        provider['name'] = data['name']
    if 'base_url' in data:
        provider['base_url'] = data['base_url'].strip()
    if 'model' in data:
        provider['model'] = data['model'].strip()
    if 'enabled' in data:
        provider['enabled'] = bool(data['enabled'])
    if 'description' in data:
        provider['description'] = data['description']
    # API Key 单独处理：传入空字符串表示不修改，传入非空表示更新
    new_key = data.get('api_key', '')
    if new_key and new_key != '********':
        provider['api_key'] = new_key.strip()

    _save_config(config)
    return provider_key


def set_active_provider(provider_key):
    """设置当前激活的提供商"""
    config = _load_config()
    if provider_key not in config.get('providers', {}):
        raise ValueError(f'提供商 {provider_key} 不存在')
    config['active_provider'] = provider_key
    _save_config(config)
    return provider_key


def delete_provider(provider_key):
    """删除自定义提供商（预设提供商不可删除）"""
    if provider_key in _PRESET_PROVIDERS:
        raise ValueError(f'内置提供商 {provider_key} 不可删除')
    config = _load_config()
    if config.get('active_provider') == provider_key:
        config['active_provider'] = 'deepseek'
    config.get('providers', {}).pop(provider_key, None)
    _save_config(config)
    return provider_key


def _mask_key(key):
    """API Key 脱敏处理"""
    if not key:
        return ''
    if len(key) <= 8:
        return '*' * len(key)
    return key[:4] + '****' + key[-4:]


def call_text_model(config, messages, temperature=0.7, max_tokens=2000, json_output=False, timeout=30, thinking=None):
    """调用已配置的文本模型，兼容 OpenAI 与 YandexGPT 原生协议。"""
    import urllib.request

    api_key = config.get('api_key', '')
    base_url = (config.get('base_url', '') or '').rstrip('/')
    model = config.get('model', '')
    provider = config.get('provider', '')
    if not api_key:
        raise ValueError('AI API Key 未配置')
    if not base_url or not model:
        raise ValueError('AI base_url 或 model 未配置')

    if provider == 'yandexgpt':
        payload = {
            'modelUri': model,
            'completionOptions': {
                'stream': False,
                'temperature': temperature,
                'maxTokens': str(max_tokens),
            },
            'messages': [
                {'role': item.get('role', 'user'), 'text': item.get('content', '')}
                for item in messages
            ],
        }
        if json_output:
            payload['jsonObject'] = True
        url = base_url + '/completion'
        auth_header = f'Api-Key {api_key}'
    else:
        payload = {
            'model': model,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': temperature,
        }
        if json_output:
            payload['response_format'] = {'type': 'json_object'}
        if thinking:
            payload['thinking'] = thinking
        url = base_url + '/chat/completions'
        auth_header = f'Bearer {api_key}'

    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'))
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', auth_header)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode('utf-8'))

    if provider == 'yandexgpt':
        content = (result.get('result', {}).get('alternatives', [{}])[0]
                   .get('message', {}).get('text', ''))
    else:
        content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
    return (content or '').strip()


def test_provider_connection(provider_key, api_key=None, base_url=None, model=None):
    """测试 AI 提供商连接

    Args:
        provider_key: 提供商标识
        api_key: 传入则使用，否则用已保存的
        base_url: 同上
        model: 同上

    Returns:
        dict: { success, message, latency_ms }
    """
    import urllib.request
    import urllib.error
    import time

    # 获取配置
    if api_key and base_url and model:
        # 直接使用传入的临时配置测试
        use_key = api_key
        use_url = base_url.rstrip('/')
        use_model = model
    else:
        config = _load_config()
        provider = config.get('providers', {}).get(provider_key, {})
        use_key = api_key or provider.get('api_key', '')
        use_url = (base_url or provider.get('base_url', '')).rstrip('/')
        use_model = model or provider.get('model', '')

    if not use_key:
        return {'success': False, 'message': 'API Key 未配置', 'latency_ms': 0}
    if not use_url:
        return {'success': False, 'message': 'Base URL 未配置', 'latency_ms': 0}
    if not use_model:
        return {'success': False, 'message': '模型名称未配置', 'latency_ms': 0}

    start_time = time.time()
    try:
        content = call_text_model(
            {'provider': provider_key, 'api_key': use_key, 'base_url': use_url, 'model': use_model},
            [{'role': 'user', 'content': '只回复小写字母 ok，不要解释、换行或其他文字'}],
            temperature=0,
            max_tokens=10,
            timeout=20,
        )
        preview = next((line.strip() for line in content.splitlines() if line.strip()), '')
        latency = int((time.time() - start_time) * 1000)
        return {
            'success': True,
            'message': f'连接成功，模型返回: "{preview[:50]}"',
            'latency_ms': latency,
            'model': use_model,
        }
    except urllib.error.HTTPError as e:
        latency = int((time.time() - start_time) * 1000)
        error_body = ''
        try:
            error_body = e.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            pass
        return {
            'success': False,
            'message': f'HTTP {e.code}: {error_body or e.reason}',
            'latency_ms': latency,
        }
    except urllib.error.URLError as e:
        latency = int((time.time() - start_time) * 1000)
        return {
            'success': False,
            'message': f'连接失败: {str(e.reason)}',
            'latency_ms': latency,
        }
    except Exception as e:
        latency = int((time.time() - start_time) * 1000)
        return {
            'success': False,
            'message': f'测试失败: {str(e)}',
            'latency_ms': latency,
        }
