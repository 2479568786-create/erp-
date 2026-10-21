"""
AI 服务模块
封装 AI 内容生成能力（标题优化、描述生成、关键词推荐、多语言翻译等）
使用真实 AI 提供商（通过 ai_config_service 配置）。未配置或调用失败时直接报错。
"""
import json
import urllib.request
import urllib.error


class AIService:
    """AI 内容生成服务

    调用通过 ai_config_service 配置的大模型 API（DeepSeek/通义千问/OpenAI 等）。
    """


    @classmethod
    def generate(cls, content_type, text, params=None):
        """生成 AI 内容

        :param content_type: 生成类型 title/description/keywords/translate
        :param text: 输入文本
        :param params: 额外参数（platform, language 等）
        :return: 生成的文本内容
        """
        params = params or {}
        result = cls._call_ai(content_type, text, params)
        if not result:
            raise RuntimeError('AI 未返回有效内容')
        return result

    @classmethod
    def _call_ai(cls, content_type, text, params):
        """调用真实 AI 提供商的 API

        使用 ai_config_service 获取配置，通过 OpenAI 兼容接口调用。
        未配置 API Key 或模型时直接报错。
        """
        from services.ai_config_service import get_ai_config

        config = get_ai_config()
        if not config.get('api_key'):
            raise RuntimeError('AI API Key 未配置')

        # 构建 prompt
        system_prompt, user_prompt = cls._build_prompt(content_type, text, params)

        try:
            from services.ai_config_service import call_text_model
            return call_text_model(
                config,
                [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                temperature=0.7,
                max_tokens=2000,
                timeout=30,
            ) or None
        except urllib.error.HTTPError as e:
            error_body = ''
            try:
                error_body = e.read().decode('utf-8', errors='replace')[:300]
            except Exception:
                pass
            raise Exception(f'AI API HTTP {e.code}: {error_body or e.reason}')
        except urllib.error.URLError as e:
            raise Exception(f'AI API 连接失败: {str(e.reason)}')

    @classmethod
    def _build_prompt(cls, content_type, text, params):
        """根据生成类型构建 system prompt 和 user prompt

        OZON 平台规范（融合自有精铺助手的文案规范）：
        - 俄语标题 ≤130 字符，标签下划线连接 ≤25 字符
        - 禁止极限词（лучший/супер/самый 等）、禁止价格/库存/促销等营销信息
        """
        platform = params.get('platform', 'ozon')
        language = params.get('language', 'ru')

        lang_names = {'ru': '俄语', 'en': '英语', 'de': '德语', 'zh': '中文'}
        lang_name = lang_names.get(language, language)
        output_language_rule = (
            '仅使用俄语输出，禁止英文单词、英文括注和中文。'
            if language == 'ru' else f'请用{lang_name}输出。'
        )

        if content_type == 'title':
            system_prompt = (
                '你是专业的 OZON 跨境电商运营专家，擅长为 OZON 平台生成合规的商品标题。'
                '严格遵循 OZON 平台规范：'
                '1. 俄语标题不超过 130 字符，包含核心热搜关键词；'
                '2. 禁止关键词堆砌，禁止使用极限词（лучший/супер/самый/лучшая 等）和绝对化用语；'
                '3. 仅描述客观属性（材质/功能/规格/适用场景），禁止出现价格、库存、促销、发货时间、店铺信息；'
                f'{output_language_rule}仅返回标题文本，不要加任何前缀说明。'
            )
            user_prompt = f'请为以下商品生成一个合规的 OZON 商品标题：\n{text}'

        elif content_type == 'tags':
            system_prompt = (
                '你是专业的 OZON 跨境电商关键词标签专家。'
                '按 OZON 平台规范生成主题标签：'
                '1. 3-5 个标签，每个不超过 25 字符；'
                '2. 单词间用下划线连接（如 автомобильный_аксессуар）；'
                '3. 优先使用俄语热搜词，覆盖核心词与长尾词；'
                '4. 禁止含价格/促销/品牌词堆砌。'
                f'{output_language_rule}仅返回逗号分隔的标签列表。'
            )
            user_prompt = f'请为以下商品生成 OZON 主题标签：\n{text}'

        elif content_type == 'description':
            system_prompt = (
                f'你是专业的 OZON 跨境电商运营专家，擅长为 OZON 平台撰写商品描述。'
                '描述应：'
                '1. 突出产品核心卖点、规格参数、材质、使用场景与注意事项；'
                '2. 用分点/分段呈现，结构清晰，纯客观描述；'
                '3. 禁止营销词汇、极限词、价格库存促销信息；'
                f'{output_language_rule}仅返回描述文本。'
            )
            user_prompt = f'请为以下商品生成详细的 OZON 商品描述：\n{text}'

        elif content_type == 'keywords':
            system_prompt = (
                f'你是跨境电商 SEO 专家，擅长为 {platform} 平台提取搜索关键词。'
                '关键词应覆盖产品核心词、长尾词、关联词等，用逗号分隔，包含俄语热搜词。'
                '每个关键词不超过 25 字符，避免无效宽泛词。'
                f'{output_language_rule}仅返回关键词列表。'
            )
            user_prompt = f'请为以下商品推荐 10-15 个搜索关键词：\n{text}'

        elif content_type == 'translate':
            system_prompt = (
                f'你是专业翻译，擅长电商文案的俄语翻译。请将用户提供的文本翻译为{lang_name}。'
                '翻译须符合俄罗斯电商用语习惯，地道自然；保持原文结构和营销语气；'
                f'术语（材质、规格、功能词汇）必须准确；{output_language_rule}仅返回翻译结果。'
            )
            user_prompt = f'请翻译以下文本：\n{text}'

        elif content_type == 'listing':
            # 中俄双语完整 Listing（融合自有精铺助手模板规范）
            system_prompt = (
                '你是专业的 OZON 跨境电商运营专家，请根据用户提供的商品信息生成中俄双语 Listing 文案。'
                '严格遵循 OZON 平台规范：'
                '1. 俄语标题 ≤130 字符，包含核心热搜关键词，禁止堆砌；'
                '2. 主题标签 3-5 个，每个 ≤25 字符，下划线连接（如 автомобильный_аксессуар）；'
                '3. 俄语简介 50-100 词，描述核心卖点与适用场景；'
                '4. 俄语详情 200-300 词，分点描述特点/材质/使用/注意事项，纯客观；'
                '5. 中文部分与俄语对应翻译。'
                '禁止：价格/库存/促销/店铺信息/发货时间/极限词（лучший/супер 等）/空泛表述。'
                '按以下格式输出：'
                '===俄语标题===\n...\n===中文标题===\n...\n===主题标签===\ntag1, tag2, tag3\n===俄语简介===\n...\n===中文简介===\n...\n===俄语详情===\n...\n===中文详情===\n...'
            )
            user_prompt = f'请基于以下商品信息生成完整 OZON Listing 文案：\n{text}'

        else:
            system_prompt = '你是专业的电商文案助手。'
            user_prompt = text

        return system_prompt, user_prompt

