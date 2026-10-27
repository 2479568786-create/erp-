# -*- coding: utf-8 -*-
"""
商品卡服务（card_service）
自旧 ERP（ozon-listing-assistant workbench/app/card_workflow_service.py + ai_product_card.py）迁移：

【三步工作流】
  第1步 summarize_selling_points：业务员填中文卖点 -> AI 总结规范化 + 翻译俄语（失败兜底原始填写）
  第2步 generate_seo_keywords：基于卖点生成 30 个俄语 SEO 关键词（分3组：主标签/精准长尾/辅助词）
  第3步 generate_product_card：用核心卖点 + SEO 关键词生成标题/描述/主题标签

【文案规范（完整迁移）】
  - title_ru：俄语标题，按 Ozon 现行平台规则 ≤130 字符（旧 ERP 为 ≤100，涂总已确认按现行规则）
  - description_ru：150-500 词，须达到最低 150 词质量要求（不足则报错重试）
  - keywords_ru：20-28 个俄文 SEO 关键词（主流程按 30 个生成），JSON 数组
  - 主标签 main_tags_ru 8-10 / 精准长尾 long_tail_tags_ru 10-12 / 辅助词 auxiliary_tags_ru 2-3
  - 每个俄文标签以 # 开头，只含俄文字母/英文字母/数字/下划线，总长度 ≤28 字符
  - 禁止：品牌名称、政策、军事、违禁品、极限词（prompt 层约束 + blocked_claims 输出）
  - 类目画像：card_profiles.category_title_guidance（10 类目画像原样迁移）

【AI 对接】
  复用新 ERP ai_config_service.get_ai_config() 统一提供商（DeepSeek/千问/OpenAI 兼容），
  走 OpenAI 兼容 /chat/completions 接口，response_format=json_object。
  纯文本模型（涂总决策：暂不配视觉模型）→ 跳过图片事实提取，observed_facts 用卖点填充。

【双轨制落库（powered by product_cards 表）】
  - AI 生成 = save_generated_card：新版本，status=GENERATED，source=ai
  - 人工新建 = save_manual_card：新版本，status=EDITED，source=manual
  - 人工编辑 = update_card：原地更新，status 置 EDITED（AI 草稿被人工接手）
  - 人工采纳 = activate_card：某版本 status 置 EDITED（确认采纳）
  - 上架取值 get_current_card：latest EDITED -> latest GENERATED -> None（回退 product 原字段）
  任何路径都不会覆盖/删除生效 EDITED 内容。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from db import query, execute
from services.card_profiles import category_title_guidance

# 旧 ERP ai_product_card 的 _remove_detected_brands 需要品牌词列表；
# 纯文本模型不提取图片品牌，保持空列表（无过滤动作），逻辑与原实现一致。
_DETECTED_BRAND_PLACEHOLDER = []

# 按 Ozon 现行平台规则：俄语标题最多 130 字符（旧 ERP 写死 100，涂总已确认升级）
MAX_TITLE_RU_LEN = 130
MAX_TITLE_ZH_LEN = 200
# 标签总长度限制：'#' + ≤27 字符 = ≤28 字符
MAX_TAG_LEN = 28
_CHINESE_RE = re.compile(r'[\u3400-\u9fff\uf900-\ufaff]')


class CardServiceError(ValueError):
    """商品卡业务错误：继承 ValueError，经 handle_errors 自动转 HTTP 400（友好错误码）。"""


def _contains_chinese(value) -> bool:
    return bool(_CHINESE_RE.search(str(value or '')))


def validate_russian_card_fields(card: dict) -> None:
    """禁止中文进入供 Ozon 使用的俄语标题、描述、卖点、关键词和标签。"""
    text_fields = ('title_ru', 'description_ru')
    list_fields = (
        'selling_points_ru', 'keywords_ru', 'main_tags_ru',
        'long_tail_tags_ru', 'auxiliary_tags_ru',
    )
    for field in text_fields:
        if _contains_chinese(card.get(field)):
            raise CardServiceError(f'俄语字段 {field} 含中文，已拒绝保存，请重新生成')
    for field in list_fields:
        for item in card.get(field) or []:
            if _contains_chinese(item):
                raise CardServiceError(f'俄语字段 {field} 含中文，已拒绝保存，请重新生成')


# ============================================================
# AI 调用（复用新 ERP 统一 AI 配置）
# ============================================================

def _extract_json(raw: str) -> dict:
    """容错解析模型输出：剥离 markdown 代码块、提取首个 JSON 对象。"""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start: end + 1])
        raise


def _chat_json(prompt: str, temperature: float = 0.3, attempts: int = 2, provider_key=None) -> tuple[dict, str, str]:
    """调用已配置文本模型并要求 JSON 输出；解析失败自动重试。

    返回 (解析后的 dict, provider, model)。未配置 AI 时报错。
    """
    from services.ai_config_service import get_ai_config

    config = get_ai_config(provider_key)
    api_key = config.get('api_key', '')
    base_url = (config.get('base_url', '') or '').rstrip('/')
    model = config.get('model', '')
    provider = config.get('provider', '')
    if not api_key:
        raise CardServiceError('AI API Key 未配置：请先在「模型管理」页填写并激活提供商')
    if not base_url or not model:
        raise CardServiceError('AI base_url 或 model 未配置：请先在「模型管理」页填写')
    if 'dashscope' in base_url and not provider:
        provider = 'qwen'

    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            from services.ai_config_service import call_text_model
            content = call_text_model(
                config,
                [{'role': 'user', 'content': prompt}],
                temperature=temperature,
                max_tokens=6000,
                json_output=True,
                timeout=300,
            )
            if not content:
                raise CardServiceError('AI 返回空内容，请重试')
            return _extract_json(content), provider, model
        except json.JSONDecodeError as exc:
            last_exc = exc
        except (KeyError, IndexError, TypeError) as exc:
            last_exc = exc
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            # 网络/服务错误直接抛出，不重试（避免浪费配额）
            detail = ''
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    detail = exc.read().decode('utf-8', errors='replace')[:300]
                except Exception:
                    pass
                raise CardServiceError(f'AI API HTTP {exc.code}: {detail or exc.reason}') from exc
            raise CardServiceError(f'AI API 连接失败: {exc}') from exc
    raise CardServiceError(f'模型输出解析失败，请重试（{last_exc}）') from last_exc


# ============================================================
# 商品上下文适配（新 ERP JSON Product -> 老 ERP 风格字段）
# ============================================================

def _product_context(product: dict) -> dict:
    """把新 ERP 的 Product（JSON 模型）映射为老 ERP 商品卡流程所需的上下文。

    映射关系：
      title_zh            <- product.title（采集标题）
      source_title        <- product.sourceName or title（货源名称兜底）
      ozon_category_name  <- product.category / categoryMatch.label
      source_platform     <- product.platform
      source_url          <- product.originalUrl / sourceLink
      variants            <- product.variants（[{name, options}] 或 [{spec_value}]）
    """
    variants = product.get('variants') or []
    variant_names = []
    for item in variants:
        if isinstance(item, dict):
            candidate = item.get('spec_value') or item.get('specValue') or item.get('name') or ''
            if candidate:
                variant_names.append(str(candidate))
    category = product.get('category') or ''
    category_match = product.get('categoryMatch') or {}
    if isinstance(category_match, dict) and category_match.get('label'):
        category = category_match['label']
    return {
        'title_zh': str(product.get('title') or ''),
        'source_title': str(product.get('sourceName') or product.get('title') or ''),
        'ozon_category_name': str(category),
        'source_platform': str(product.get('platform') or 'ozon'),
        'source_url': str(product.get('originalUrl') or product.get('sourceLink') or ''),
        'variants': variant_names,
    }


# ============================================================
# 第1步：核心卖点整理
# ============================================================

def summarize_selling_points(raw_lines, provider_key=None) -> dict:
    """第1步：人工填中文卖点 -> AI 总结规范化 + 翻译俄语。

    返回 {selling_points_zh: [...], selling_points_ru: [...], summary: str, fallback: bool}
    """
    if isinstance(raw_lines, str):
        raw_lines = [line for line in raw_lines.splitlines() if line.strip()]
    cleaned = [line.strip().lstrip("-• ").strip() for line in (raw_lines or []) if line and str(line).strip()]
    if not cleaned:
        raise CardServiceError('请先填写核心卖点（每行一条）')

    prompt = (
        "你是俄罗斯 OZON 电商的商品卖点整理助手。业务员用中文填写了以下核心卖点，"
        "请：1) 用简洁专业的中文总结/规范化（去掉口语、重复，每条 10-20 字，最多 8 条）；"
        "2) 把每条翻译成地道俄语（每条不超过 8 个词）。\n"
        "业务员填写（中文）：\n" + "\n".join(f"- {item}" for item in cleaned) + "\n"
        "输出严格 JSON：{\"selling_points_zh\": [\"中文卖点\", ...], "
        "\"selling_points_ru\": [\"俄语卖点\", ...], \"summary\": \"一句话总结（中文）\"}"
    )
    try:
        result, _, _ = _chat_json(prompt, provider_key=provider_key)
        zh = [str(item).strip() for item in result.get('selling_points_zh', []) if str(item).strip()]
        ru = [str(item).strip() for item in result.get('selling_points_ru', []) if str(item).strip()]
        if not zh:
            raise CardServiceError('AI 未返回有效的中文卖点')
    except CardServiceError as exc:
        raise CardServiceError(f'俄语翻译失败，未生成商品卡，请重试：{exc}') from exc
    # 保证中俄数量一致
    while len(ru) < len(zh):
        ru.append(zh[len(ru)])
    while len(ru) > len(zh):
        ru = ru[: len(zh)]
    return {
        'selling_points_zh': zh[:8],
        'selling_points_ru': ru[:8],
        'summary': str(result.get('summary', '')).strip(),
        'fallback': False,
    }


# ============================================================
# 第2步：SEO 关键词
# ============================================================

def generate_seo_keywords(ctx: dict, selling_points_zh: list[str], selling_points_ru: list[str], provider_key=None, source_facts=None) -> dict:
    """第2步：基于核心卖点生成 30 个俄语 SEO 关键词，分 3 组。

    返回 {keywords_ru: [...30], main_tags_ru: [...8-10], long_tail_tags_ru: [...10-12],
          auxiliary_tags_ru: [...2-3] + 对应中文组}
    """
    zh_block = '；'.join(selling_points_zh) or '（未填写）'
    ru_block = '；'.join(selling_points_ru) or '（未填写）'
    if provider_key == 'shangdekuai_lingke':
        fact_block = '\n'.join(str(item) for item in (source_facts or []) if str(item).strip()) or '（无额外事实）'
        prompt = (
            "你是俄罗斯 Ozon 商品卡 SEO 助手。只根据已审核商品事实和卖点生成搜索词与主题标签候选。\n"
            f"商品：{ctx.get('title_zh') or ctx.get('source_title')}\n"
            f"Ozon 类目：{ctx.get('ozon_category_name') or '待确认'}\n"
            f"已审核原始事实：\n{fact_block}\n"
            f"中文卖点：{zh_block}\n俄语卖点：{ru_block}\n"
            "规则：\n"
            "- keywords_ru 生成 20-30 个真实相关的俄语搜索词候选，供人工参考；它们不是要直接发布的主题标签。"
            "去重，不含竞品品牌、无关热词或未确认属性。keywords_zh 与其逐项对应。\n"
            "- main_tags_ru 生成 7-9 个宽泛流量标签，表达商品所属大类、常用宽泛搜索表达或核心需求；"
            "不要照抄商品卡已有的品牌、商品类型等属性，可用相关但不重复的类别表达。\n"
            "- long_tail_tags_ru 生成 8-10 个精准长尾标签，围绕已确认的使用场景、用途、买点和商品特征组合；"
            "每个组合必须有事实依据，不得推断功能或适配范围。\n"
            "- 每个标签以 # 开头，标签内部只用俄文字母、英文字母、数字和下划线，不含空格，最多 30 个字符；"
            "两个标签组之间去重，每个商品标签总量不得超过 30 个。\n"
            "- 仅在事实足够支持时输出标签；不要为凑数量编造或重复。辅助场景标签组不再单独生成，返回空数组。\n"
            "- 中文标签翻译分别填入 main_tags_zh 和 long_tail_tags_zh。\n"
            "输出严格 JSON：{\"keywords_ru\": [], \"keywords_zh\": [], \"main_tags_ru\": [], \"main_tags_zh\": [], "
            "\"long_tail_tags_ru\": [], \"long_tail_tags_zh\": [], \"auxiliary_tags_ru\": [], \"auxiliary_tags_zh\": []}"
        )
    else:
        prompt = (
        "你是俄罗斯 Ozon 市场的 SEO 关键词专家。基于商品的核心卖点生成 SEO 关键词。\n"
        f"商品：{ctx.get('title_zh') or ctx.get('source_title')}\n"
        f"核心卖点（中文）：{zh_block}\n"
        f"核心卖点（俄语）：{ru_block}\n"
        "要求：\n"
        "- keywords_ru: 恰好 30 个俄语关键词，按搜索意图覆盖核心品类词、已确认属性词、真实使用场景和精准长尾词；去重，不要同义词机械重复\n"
        "- keywords_zh: 与 keywords_ru 一一对应的中文翻译\n"
        "- main_tags_ru: 8-10 个泛流量主标签（高频大词）\n"
        "- long_tail_tags_ru: 10-12 个精准长尾词（含属性/场景组合）\n"
        "- auxiliary_tags_ru: 2-3 个真实使用场景辅助词（如 дома、в поездке、для офиса；禁止季节、趋势和促销表达）\n"
        "- 每个标签组对应中文翻译 main_tags_zh / long_tail_tags_zh / auxiliary_tags_zh\n"
        "输出严格 JSON：{\"keywords_ru\": [...], \"keywords_zh\": [...], "
        "\"main_tags_ru\": [...], \"main_tags_zh\": [...], "
        "\"long_tail_tags_ru\": [...], \"long_tail_tags_zh\": [...], "
        "\"auxiliary_tags_ru\": [...], \"auxiliary_tags_zh\": [...]}"
        )
    try:
        result, _, _ = _chat_json(prompt, provider_key=provider_key)
    except CardServiceError as exc:
        raise CardServiceError(f'SEO 关键词生成失败：{exc}') from exc

    def lst(key):
        return [str(item).strip() for item in result.get(key, []) if str(item).strip()]

    keywords_ru = lst('keywords_ru')[:30]
    if len(keywords_ru) < 20:
        raise CardServiceError(f'AI 仅生成 {len(keywords_ru)} 个关键词，请重试')
    return {
        'keywords_ru': keywords_ru,
        'keywords_zh': (lst('keywords_zh') + [''] * 30)[: len(keywords_ru)],
        'main_tags_ru': lst('main_tags_ru')[:9 if provider_key == 'shangdekuai_lingke' else 10],
        'main_tags_zh': lst('main_tags_zh')[:9 if provider_key == 'shangdekuai_lingke' else 10],
        'long_tail_tags_ru': lst('long_tail_tags_ru')[:10 if provider_key == 'shangdekuai_lingke' else 12],
        'long_tail_tags_zh': lst('long_tail_tags_zh')[:10 if provider_key == 'shangdekuai_lingke' else 12],
        'auxiliary_tags_ru': [] if provider_key == 'shangdekuai_lingke' else lst('auxiliary_tags_ru')[:3],
        'auxiliary_tags_zh': [] if provider_key == 'shangdekuai_lingke' else lst('auxiliary_tags_zh')[:3],
    }


# ============================================================
# 第3步：商品卡生成
# ============================================================

def _norm_tags(tags: list[str], max_len=MAX_TAG_LEN) -> list[str]:
    """Ozon 主题标签规范化：# 开头，无空格（下划线替换），≤28 字符（旧 ERP norm_tags 原样迁移）。"""
    out = []
    for tag in tags:
        cleaned = str(tag).strip().lstrip('#')
        cleaned = re.sub(r'\s+', '_', cleaned)
        cleaned = re.sub(r'[^A-Za-zА-Яа-яЁё0-9_]', '', cleaned)
        if not cleaned:
            continue
        if len(cleaned) > max_len - 1:
            cleaned = cleaned[:max_len - 1]
        out.append('#' + cleaned)
    return out

def _word_count(text: str) -> int:
    return len(re.findall(r'[A-Za-zА-Яа-яЁё0-9]+', text or ''))


def _normalize_card_lists(result: dict) -> None:
    """后处理：把模型可能输出的分号连接长字符串，拆分成独立数组元素（去重保序）。"""
    for field in ('keywords_ru', 'keywords_zh', 'main_tags_ru', 'main_tags_zh',
                  'long_tail_tags_ru', 'long_tail_tags_zh', 'auxiliary_tags_ru', 'auxiliary_tags_zh'):
        value = result.get(field)
        if not isinstance(value, list):
            continue
        parts = []
        for item in value:
            if isinstance(item, str) and (';' in item or '；' in item or '， ' in item or ', ' in item):
                parts.extend(part.strip() for part in re.split(r'[;；]', item) if part.strip())
            else:
                parts.append(item)
        seen = set()
        unique = []
        for part in parts:
            key = str(part).lower()
            if key not in seen:
                seen.add(key)
                unique.append(part)
        result[field] = unique


def _remove_detected_brands(result: dict, brands: list[str]) -> None:
    """从标题/描述/标签中移除检测到的品牌词（旧 ERP 原样迁移；纯文本模式 brands 一般为空）。"""
    for brand in (str(item).strip() for item in (brands or []) if str(item).strip()):
        normalized_brand = ''.join(char for char in brand.casefold() if char.isalnum())
        if not normalized_brand:
            continue
        for field in ('title_ru', 'title_zh', 'description_ru', 'description_zh'):
            value = result.get(field)
            if isinstance(value, str):
                result[field] = ' '.join(value.replace(brand, ' ').split())
        for field in ('keywords_ru', 'keywords_zh', 'tags_ru', 'tags_zh', 'main_tags_ru', 'main_tags_zh',
                      'long_tail_tags_ru', 'long_tail_tags_zh', 'auxiliary_tags_ru', 'auxiliary_tags_zh'):
            if field not in result or not isinstance(result.get(field), list):
                continue
            result[field] = [
                item for item in result[field]
                if normalized_brand not in ''.join(char for char in str(item).casefold() if char.isalnum())
            ]


def generate_product_card(ctx: dict, selling_points_zh: list[str], selling_points_ru: list[str], seo: dict, provider_key=None, source_facts=None) -> dict:
    """第3步：用核心卖点 + SEO 关键词生成商品卡（标题/描述/主题标签）。

    上得快商品卡遵循 ERP 内部 title_ru ≤130 字符校验；原 ERP 商品卡分支不变。
    """
    zh_block = '；'.join(selling_points_zh) or '（未填写）'
    ru_block = '；'.join(selling_points_ru) or '（未填写）'
    kw_block = '；'.join((seo.get('keywords_ru') or [])[:30])
    main_tags = ' '.join(seo.get('main_tags_ru') or [])
    long_tail = ' '.join(seo.get('long_tail_tags_ru') or [])
    aux = ' '.join(seo.get('auxiliary_tags_ru') or [])
    category_label, title_structure = category_title_guidance(
        ctx.get('ozon_category_name'), ctx.get('title_zh') or ctx.get('source_title')
    )
    if provider_key == 'shangdekuai_lingke':
        fact_block = '\n'.join(str(item) for item in (source_facts or []) if str(item).strip()) or '（无额外事实）'
        prompt = (
            "你是俄罗斯 Ozon 商品卡文案专家。根据已审核事实生成可由人工复核的俄语商品卡。\n"
            f"商品：{ctx.get('title_zh') or ctx.get('source_title')}\n"
            f"Ozon 类目：{ctx.get('ozon_category_name') or '待确认'}\n"
            f"已审核原始事实：\n{fact_block}\n"
            f"中文卖点：{zh_block}\n俄语卖点：{ru_block}\n"
            f"搜索词候选（仅供自然参考，不要复制成词表）：{kw_block}\n"
            f"主标签候选：{main_tags}\n精准长尾标签候选：{long_tail}\n"
            f"类目画像：{category_label}；建议标题结构：{title_structure}\n"
            "要求：\n"
            f"- title_ru 使用俄语，≤{MAX_TITLE_RU_LEN} 字符（ERP 内部上限），先写清商品类型，再加入最重要且已确认的型号/参数/特点；"
            "简洁、可读，不堆砌关键词。品牌仅在事实明确且可合法使用时保留。不得含价格、折扣、促销、库存、重量、店铺信息或极限词。\n"
            "- title_zh 是准确的中文对应标题，不增加俄语标题中没有的事实。\n"
            "- description_ru 按 ERP 全局文案要求控制在约 200-300 词，并且不得超过 Ozon 6000 字符上限；"
            "写成与本商品相关的连贯事实描述，介绍用途、已确认参数/材质、实际使用场景、适配范围或包装信息（仅限已确认）。"
            "不要把关键词罗列成清单，不写没有证据的评价、性能承诺或使用效果。\n"
            "- description_zh 与俄语描述含义一致。俄语字段不得混入中文。可用自然段或列表，不生成复杂 HTML。\n"
            "- keywords_ru / keywords_zh 复用搜索词候选；main_tags_ru / long_tail_tags_ru 复用对应候选标签，不新增、不重复；"
            "辅助标签组返回空数组。\n"
            "- pending_confirmations_zh 列出影响商品理解但尚未确认的信息；blocked_claims_zh 列出已排除的品牌/违规或无依据声明，没有则空数组。\n"
            "- 价格和包装/物流重量由人工填写，不写入任何文案字段。不得写原装/正品/仿品/复制品等无法证明的身份声明，"
            "也不得写价格、折扣、赠品促销、库存、联系方式、外链、配送/退货说明或购买号召。\n"
            "输出严格 JSON：{\"title_ru\": \"\", \"title_zh\": \"\", \"description_ru\": \"\", \"description_zh\": \"\", "
            "\"keywords_ru\": [], \"keywords_zh\": [], \"main_tags_ru\": [], \"main_tags_zh\": [], "
            "\"long_tail_tags_ru\": [], \"long_tail_tags_zh\": [], \"auxiliary_tags_ru\": [], "
            "\"auxiliary_tags_zh\": [], \"pending_confirmations_zh\": [], \"blocked_claims_zh\": []}"
        )
    else:
        prompt = (
        "你是俄罗斯 Ozon 市场的资深商品卡文案专家。基于核心卖点与 SEO 关键词生成完整商品卡。\n"
        f"商品：{ctx.get('title_zh') or ctx.get('source_title')}\n"
        f"类目：{ctx.get('ozon_category_name') or '待确认'}\n"
        f"核心卖点（中文）：{zh_block}\n"
        f"核心卖点（俄语）：{ru_block}\n"
        f"SEO关键词（俄语）：{kw_block}\n"
        f"主标签：{main_tags}\n精准长尾：{long_tail}\n辅助：{aux}\n"
        "要求：\n"
        f"- 当前类目画像：{category_label}；标题结构：{title_structure}\n"
        f"- title_ru: 俄语标题，≤{MAX_TITLE_RU_LEN}字符，核心品类词靠前，只选最有价值的 2—3 个真实参数，不要机械堆词\n"
        "- title_zh: 中文标题\n"
        "- description_ru: 俄语详情描述，300-500词，按用途、已确认参数、使用场景、兼容性或包装组成组织；自然融入核心词和少量长尾词，禁止关键词罗列\n"
        "- description_zh: 中文对照\n"
        "- keywords_ru: 复用给定的 30 个 SEO 关键词（按原样输出数组）\n"
        "- main_tags_ru / long_tail_tags_ru / auxiliary_tags_ru: 复用给定标签（原样输出数组）\n"
        "- 对应中文翻译 main_tags_zh / long_tail_tags_zh / auxiliary_tags_zh / keywords_zh\n"
        "- pending_confirmations_zh: 未核验的参数/认证（没有则空数组）\n"
        "- blocked_claims_zh: 禁止声明（品牌/医疗功效，没有则空数组）\n"
        "- 全程禁止：品牌名称、极限词（лучший/супер/самый 等）、价格/库存/促销信息、未核验的功效声明\n"
        "输出严格 JSON：{\"title_ru\": \"\", \"title_zh\": \"\", \"description_ru\": \"\", \"description_zh\": \"\", "
        "\"keywords_ru\": [], \"keywords_zh\": [], \"main_tags_ru\": [], \"main_tags_zh\": [], "
        "\"long_tail_tags_ru\": [], \"long_tail_tags_zh\": [], \"auxiliary_tags_ru\": [], "
        "\"auxiliary_tags_zh\": [], \"pending_confirmations_zh\": [], \"blocked_claims_zh\": []}"
        )
    try:
        result, provider, model = _chat_json(prompt, temperature=0.4, provider_key=provider_key)
    except CardServiceError as exc:
        raise CardServiceError(f'商品卡生成失败：{exc}') from exc

    def lst(key):
        return [str(item).strip() for item in result.get(key, []) if str(item).strip()]

    keywords_ru = lst('keywords_ru') or (seo.get('keywords_ru') or [])
    description_ru = str(result.get('description_ru', '')).strip()
    is_shangdekuai = provider_key == 'shangdekuai_lingke'
    if is_shangdekuai and len(description_ru) > 6000:
        raise CardServiceError('商品卡生成失败：俄语简介超过 Ozon 的 6000 字符上限，请重新生成')
    word_count = _word_count(description_ru)
    if word_count < 150:
        raise CardServiceError(f'模型返回的俄文描述仅 {word_count} 词，未达到 150 词质量要求，请重试')

    card = {
        'title_ru': str(result.get('title_ru', ''))[:MAX_TITLE_RU_LEN],
        'title_zh': str(result.get('title_zh', ''))[:MAX_TITLE_ZH_LEN],
        'description_ru': description_ru,
        'description_zh': str(result.get('description_zh', '')),
        'keywords_ru': keywords_ru,
        'keywords_zh': lst('keywords_zh') or (seo.get('keywords_zh') or []),
        'main_tags_ru': _norm_tags(lst('main_tags_ru') or (seo.get('main_tags_ru') or []), max_len=30 if is_shangdekuai else MAX_TAG_LEN),
        'main_tags_zh': lst('main_tags_zh') or (seo.get('main_tags_zh') or []),
        'long_tail_tags_ru': _norm_tags(lst('long_tail_tags_ru') or (seo.get('long_tail_tags_ru') or []), max_len=30 if is_shangdekuai else MAX_TAG_LEN),
        'long_tail_tags_zh': lst('long_tail_tags_zh') or (seo.get('long_tail_tags_zh') or []),
        'auxiliary_tags_ru': [] if is_shangdekuai else _norm_tags(lst('auxiliary_tags_ru') or (seo.get('auxiliary_tags_ru') or [])),
        'auxiliary_tags_zh': lst('auxiliary_tags_zh') or (seo.get('auxiliary_tags_zh') or []),
        'tags_ru': [],
        'tags_zh': [],
        'observed_facts_zh': list(selling_points_zh),
        'pending_confirmations_zh': lst('pending_confirmations_zh'),
        'blocked_claims_zh': lst('blocked_claims_zh'),
        # 内部透传字段：落库前由 caller 读出并移除
        '_provider': provider,
        '_model': model,
    }
    _normalize_card_lists(card)
    _remove_detected_brands(card, _DETECTED_BRAND_PLACEHOLDER)
    validate_russian_card_fields(card)
    return card


# ============================================================
# 双轨制落库（product_cards 表）
# ============================================================

_CARD_FIELDS = (
    'title_ru', 'title_zh', 'description_ru', 'description_zh',
    'keywords_ru', 'keywords_zh', 'main_tags_ru', 'main_tags_zh',
    'long_tail_tags_ru', 'long_tail_tags_zh', 'auxiliary_tags_ru', 'auxiliary_tags_zh',
    'tags_ru', 'tags_zh', 'observed_facts_zh', 'pending_confirmations_zh', 'blocked_claims_zh',
)

_JSON_FIELDS = (
    'keywords_ru', 'keywords_zh', 'main_tags_ru', 'main_tags_zh',
    'long_tail_tags_ru', 'long_tail_tags_zh', 'auxiliary_tags_ru', 'auxiliary_tags_zh',
    'tags_ru', 'tags_zh', 'observed_facts_zh', 'pending_confirmations_zh', 'blocked_claims_zh',
)

_JSON_ARRAY_FIELDS = ('selling_points_zh', 'selling_points_ru')


def _to_json(value) -> str:
    if value is None:
        return '[]'
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _from_json(value, default=None):
    if value in (None, ''):
        return default if default is not None else []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def _row_to_card(row) -> dict:
    """数据库行 -> API 字典（JSON 字段还原为数组）。"""
    card = dict(row)
    for field in _JSON_FIELDS + _JSON_ARRAY_FIELDS:
        card[field] = _from_json(card.get(field))
    return card


def _max_version(product_id: str) -> int:
    row = query(
        'SELECT COALESCE(MAX(version), 0) AS v FROM product_cards WHERE product_id = ?',
        (str(product_id),),
        one=True,
    )
    return int(row['v']) if row else 0


def _build_insert_params(product_id, data, version, status, source, provider, model, previous_id=None):
    """把生成结果/手动数据规范化为落库参数字典（JSON 字段序列化）。"""
    params = {
        'product_id': str(product_id),
        'version': version,
        'status': status,
        'source': source,
        'provider': provider or '',
        'model': model or '',
        'previous_id': previous_id,
        'title_ru': str(data.get('title_ru') or ''),
        'title_zh': str(data.get('title_zh') or ''),
        'description_ru': str(data.get('description_ru') or ''),
        'description_zh': str(data.get('description_zh') or ''),
    }
    for field in _JSON_FIELDS:
        params[field] = _to_json(data.get(field))
    params['selling_points_zh'] = _to_json(data.get('selling_points_zh', data.get('observed_facts_zh', [])))
    params['selling_points_ru'] = _to_json(data.get('selling_points_ru', []))
    params['summary_zh'] = str(data.get('summary_zh') or data.get('summary') or '')
    return params


def _insert_card(params: dict) -> int:
    conn = None
    try:
        from db import get_connection
        conn = get_connection()
        cursor = conn.execute(
            "INSERT INTO product_cards (product_id, version, status, source, provider, model, previous_id,"
            " selling_points_zh, selling_points_ru, summary_zh, title_ru, title_zh, description_ru, description_zh,"
            " keywords_ru, keywords_zh, main_tags_ru, main_tags_zh, long_tail_tags_ru, long_tail_tags_zh,"
            " auxiliary_tags_ru, auxiliary_tags_zh, tags_ru, tags_zh, observed_facts_zh,"
            " pending_confirmations_zh, blocked_claims_zh, updated_at)"
            " VALUES (:product_id, :version, :status, :source, :provider, :model, :previous_id,"
            " :selling_points_zh, :selling_points_ru, :summary_zh, :title_ru, :title_zh, :description_ru, :description_zh,"
            " :keywords_ru, :keywords_zh, :main_tags_ru, :main_tags_zh, :long_tail_tags_ru, :long_tail_tags_zh,"
            " :auxiliary_tags_ru, :auxiliary_tags_zh, :tags_ru, :tags_zh, :observed_facts_zh,"
            " :pending_confirmations_zh, :blocked_claims_zh, datetime('now','localtime'))",
            params,
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        if conn is not None:
            conn.close()


def _clean_update_data(data: dict) -> dict:
    """手动编辑/新建时的字段清洗：只取白名单字段，数组字段 JSON 化。"""
    cleaned = {}
    for field in ('title_ru', 'title_zh', 'description_ru', 'description_zh', 'summary_zh'):
        if field in data:
            cleaned[field] = str(data.get(field) or '')
    for field in _JSON_FIELDS:
        if field in data:
            cleaned[field] = _to_json(data.get(field))
    if 'selling_points_zh' in data:
        cleaned['selling_points_zh'] = _to_json(data.get('selling_points_zh'))
    if 'selling_points_ru' in data:
        cleaned['selling_points_ru'] = _to_json(data.get('selling_points_ru'))
    return cleaned


def save_generated_card(product_id, data, provider='', model='', previous_id=None) -> dict:
    """AI 生成落库：新版本，status=GENERATED，source=ai（绝不覆盖既有版本）。"""
    version = _max_version(product_id) + 1
    params = _build_insert_params(product_id, data, version, 'GENERATED', 'ai', provider, model, previous_id)
    card_id = _insert_card(params)
    return get_card(card_id)


def save_manual_card(product_id, data) -> dict:
    """人工新建落库：新版本，status=EDITED，source=manual。"""
    version = _max_version(product_id) + 1
    params = _build_insert_params(product_id, _clean_update_data(data), version, 'EDITED', 'manual', '', '', None)
    card_id = _insert_card(params)
    return get_card(card_id)


def update_card(card_id, data) -> dict:
    """人工编辑已存在的卡：原地更新，status 置 EDITED（AI 草稿被人工接手）。"""
    card = get_card(card_id)
    if not card:
        raise CardServiceError('商品卡不存在')
    cleaned = _clean_update_data(data)
    if not cleaned:
        raise CardServiceError('没有需要更新的字段')
    sets = ', '.join(f'{field} = ?' for field in cleaned)
    values = list(cleaned.values()) + [card_id]
    execute(
        f"UPDATE product_cards SET status = 'EDITED', updated_at = datetime('now','localtime'), {sets} WHERE id = ?",
        values,
    )
    return get_card(card_id)


def activate_card(card_id) -> dict:
    """人工采纳某版本：status 置 EDITED（该版本成为上架优先取值）。"""
    card = get_card(card_id)
    if not card:
        raise CardServiceError('商品卡不存在')
    execute("UPDATE product_cards SET status = 'EDITED', updated_at = datetime('now','localtime') WHERE id = ?", (card_id,))
    return get_card(card_id)


def delete_card(card_id) -> None:
    """删除版本：仅允许删除非当前生效版本。"""
    card = get_card(card_id)
    if not card:
        raise CardServiceError('商品卡不存在')
    current = get_current_card(card['product_id'])
    if current and current['id'] == int(card_id):
        raise CardServiceError('当前生效版本不可删除，请先采纳/新建其他版本')
    execute('DELETE FROM product_cards WHERE id = ?', (card_id,))


def get_card(card_id) -> dict | None:
    row = query('SELECT * FROM product_cards WHERE id = ?', (card_id,), one=True)
    return _row_to_card(row) if row else None


def get_current_card(product_id: str) -> dict | None:
    """上架取值：latest EDITED -> latest GENERATED -> None（回退 product 原字段）。"""
    row = query(
        "SELECT * FROM product_cards WHERE product_id = ? AND status = 'EDITED'"
        ' ORDER BY version DESC LIMIT 1',
        (str(product_id),),
        one=True,
    )
    if row:
        return _row_to_card(row)
    row = query(
        "SELECT * FROM product_cards WHERE product_id = ? AND status = 'GENERATED'"
        ' ORDER BY version DESC LIMIT 1',
        (str(product_id),),
        one=True,
    )
    return _row_to_card(row) if row else None


def list_versions(product_id: str, page: int = 1, page_size: int = 20) -> dict:
    """版本列表（按版本号倒序，含生效标记）。"""
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)
    total_row = query(
        'SELECT COUNT(*) AS c FROM product_cards WHERE product_id = ?', (str(product_id),), one=True
    )
    total = int(total_row['c']) if total_row else 0
    rows = query(
        'SELECT * FROM product_cards WHERE product_id = ? ORDER BY version DESC LIMIT ? OFFSET ?',
        (str(product_id), page_size, (page - 1) * page_size),
    )
    current = get_current_card(product_id)
    current_id = current['id'] if current else None
    items = [_row_to_card(row) for row in rows]
    for item in items:
        item['isCurrent'] = item['id'] == current_id
        item['isAi'] = item['source'] == 'ai'
    return {'list': items, 'total': total, 'page': page, 'pageSize': page_size}


# ============================================================
# 主流程：一键生成/重新生成
# ============================================================

def generate_card(product_id: str, raw_selling_points=None, base_card_id: int | None = None) -> dict:
    """完整三步生成并落库。

    Args:
        product_id: 商品 ID（Product JSON id）
        raw_selling_points: 业务员填写的中文卖点（list 或换行文本）。不传则从 base_card_id 复用。
        base_card_id: 基于某版本再生成（复用其卖点跑全流程）
    """
    from models.product import Product  # 延迟导入避免循环依赖

    product = Product.find_by_id(str(product_id))
    if not product:
        raise CardServiceError('商品不存在或已删除')
    ctx = _product_context(product)

    base = None
    if raw_selling_points is None or not str(raw_selling_points).strip():
        if base_card_id:
            base = get_card(base_card_id)
            if not base:
                raise CardServiceError('基础版本不存在')
            if str(base['product_id']) != str(product_id):
                raise CardServiceError('基础版本与商品不匹配')
        else:
            raise CardServiceError('请先填写核心卖点（每行一条），或指定要基于的版本')

    if base:
        points_zh = base.get('selling_points_zh') or base.get('observed_facts_zh') or []
        points_ru = base.get('selling_points_ru') or []
        # _row_to_card 已将 JSON 字符串还原为数组，此处仅需字符串兜底
        if isinstance(points_zh, str):
            points_zh = _from_json(points_zh)
        if isinstance(points_ru, str):
            points_ru = _from_json(points_ru)
        if not points_zh:
            raise CardServiceError('基础版本没有可复用的卖点')
        step1 = {
            'selling_points_zh': points_zh,
            'selling_points_ru': points_ru,
            'summary': base.get('summary_zh') or '',
            'fallback': False,
        }
    else:
        step1 = summarize_selling_points(raw_selling_points)

    validate_russian_card_fields({'selling_points_ru': step1.get('selling_points_ru') or []})

    step2 = generate_seo_keywords(ctx, step1['selling_points_zh'], step1.get('selling_points_ru') or [])
    step3 = generate_product_card(ctx, step1['selling_points_zh'], step1.get('selling_points_ru') or [], step2)

    data = {
        'selling_points_zh': step1['selling_points_zh'],
        'selling_points_ru': step1.get('selling_points_ru') or [],
        'summary_zh': step1.get('summary') or '',
        **step3,
    }
    provider = data.pop('_provider', '') or ''
    model = data.pop('_model', '') or ''
    saved = save_generated_card(
        product_id, data, provider=provider, model=model, previous_id=base_card_id
    )
    return saved


def generate_card_content(ctx: dict, raw_selling_points) -> dict:
    """按现有商品卡 AI 三步框架生成内容，但不读取 Product 或写入 product_cards。"""
    provider_key = 'shangdekuai_lingke'
    step1 = summarize_selling_points(raw_selling_points, provider_key=provider_key)
    validate_russian_card_fields({'selling_points_ru': step1.get('selling_points_ru') or []})
    step2 = generate_seo_keywords(ctx, step1['selling_points_zh'], step1.get('selling_points_ru') or [],
                                  provider_key=provider_key, source_facts=raw_selling_points)
    step3 = generate_product_card(ctx, step1['selling_points_zh'], step1.get('selling_points_ru') or [],
                                  step2, provider_key=provider_key, source_facts=raw_selling_points)
    data = {
        'selling_points_zh': step1['selling_points_zh'],
        'selling_points_ru': step1.get('selling_points_ru') or [],
        'summary_zh': step1.get('summary') or '',
        **step2,
        **step3,
    }
    data['provider'] = data.pop('_provider', '') or ''
    data['model'] = data.pop('_model', '') or ''
    return data
