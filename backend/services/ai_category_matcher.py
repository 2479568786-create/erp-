"""
AI 类目匹配服务
使用大模型（DeepSeek/通义千问）进行语义级类目匹配
当关键词匹配失败时，调用 AI 进行精准匹配

缓存策略：复用 category_mapping_cache（SQLite category_mappings 表），
与关键词匹配共享同一缓存层，避免双缓存（JSON + SQLite）维护成本。
"""
import json
import os
import urllib.request
import urllib.error
from config import Config


def _get_ai_config():
    """获取当前 AI 提供商配置（从模型管理页面持久化的配置中读取）

    配置由 /api/ai/models 接口管理，整个系统共用一份
    """
    from services.ai_config_service import get_ai_config
    config = get_ai_config()
    return {
        'api_key': config.get('api_key', ''),
        'base_url': config.get('base_url', ''),
        'model': config.get('model', ''),
        'provider': config.get('provider', ''),
    }


def _load_cached_match(source_category, title):
    """从 SQLite 缓存读取 AI 匹配结果（复用 category_mapping_cache）

    Returns:
        dict 或 None：命中返回匹配结果，未命中返回 None
    """
    from services.category_mapping_cache import get_mapping
    cached = get_mapping(source_category or '', title or '')
    if cached:
        cached['_source'] = 'ai_cache'
    return cached


def _save_cached_match(source_category, title, result):
    """保存 AI 匹配结果到 SQLite 缓存（复用 category_mapping_cache）

    仅缓存匹配成功的结果（set_mapping 内部校验 matched=True）。
    """
    from services.category_mapping_cache import set_mapping
    set_mapping(source_category or '', title or '', result)


def _call_ai_api(messages, temperature=0.1):
    """调用 AI API（OpenAI 兼容接口）

    Args:
        messages: 消息列表
        temperature: 温度参数（越低越确定）

    Returns:
        AI 返回的文本内容
    """
    config = _get_ai_config()
    if not config['api_key']:
        raise ValueError('AI API Key 未配置，请在「店铺管理 > 模型管理」页面中设置')

    from services.ai_config_service import call_text_model
    return call_text_model(
        config,
        messages,
        temperature=temperature,
        max_tokens=1000,
        json_output=True,
        timeout=30,
    )


def _build_category_list(tree):
    """从类目树构建精简的类目列表（含中俄文路径）

    每个类目包含中俄文路径（用于关键词预筛选）和中文路径（用于 AI prompt）。
    """
    categories = []
    for l1 in tree:
        l1_zh = l1.get('category_name_zh') or l1.get('category_name', '')
        l1_ru = l1.get('category_name_ru') or ''
        for l2 in (l1.get('children') or []):
            l2_zh = l2.get('category_name_zh') or l2.get('category_name', '')
            l2_ru = l2.get('category_name_ru') or ''
            desc_cat_id = l2.get('description_category_id')
            if not desc_cat_id:
                continue
            for l3 in (l2.get('children') or []):
                l3_zh = l3.get('type_name_zh') or l3.get('type_name', '')
                l3_ru = l3.get('type_name_ru') or ''
                type_id = l3.get('type_id')
                if not type_id:
                    continue
                categories.append({
                    'path': f'{l1_zh} > {l2_zh} > {l3_zh}',
                    'path_ru': f'{l1_ru} > {l2_ru} > {l3_ru}',
                    'search_text': f'{l1_zh} {l1_ru} {l2_zh} {l2_ru} {l3_zh} {l3_ru}'.lower(),
                    'description_category_id': desc_cat_id,
                    'type_id': type_id,
                })
    return categories


def _filter_candidates_by_keywords(categories, source_category, title, description, top_n=100):
    """关键词预筛选候选类目（retrieval 阶段）

    7400+ 个类目全部发给 AI 会超 token 限制且截断导致关键类目丢失。
    本函数用关键词匹配从全量类目中筛选 Top-N 候选，再交给 AI 精排（reranking）。

    匹配策略：
      - 从 source_category + title + description 提取关键词
      - 对每个类目的中俄文搜索文本做关键词匹配打分
      - L3 名称匹配权重最高，L2 次之，L1 最低
      - 返回得分最高的 Top-N 候选

    Args:
        categories: 全量类目列表（来自 _build_category_list）
        source_category: 采集分类名
        title: 商品标题
        description: 商品描述
        top_n: 返回的候选数量上限

    Returns:
        筛选后的类目列表（按得分降序）
    """
    import re

    # 合并所有信号文本用于提取关键词
    all_text = ' '.join(filter(None, [source_category or '', title or '', description or '']))
    if not all_text.strip():
        # 无任何信号，返回空（无信号时不注入无关兜底，AI 侧会明确报"信息不足"）
        return []

    # 提取关键词：先把「3d打印petg耗材」这类中英文粘连词拆成独立 token，
    # 再做基础停用词过滤。中文还可额外生成 2-gram 子串提高召回。
    lowered = all_text.lower()
    raw_words = re.split(r'[>/、|，,\s\-_;:.!?\(\)（）【】\[\]]+', lowered)
    stopwords = {
        'the', 'and', 'for', 'with', 'это', 'или', 'для', 'как', 'но', 'при', 'что',
        '商品', '描述', '采集', '分类', '标题', '规格', '型号', '高速', '便携', '新款',
    }
    keywords = []
    seen = set()
    for w in raw_words:
        w = w.strip()
        if not w:
            continue
        # 中英文/数字粘连 → 拆成字母数字段 + 汉字段
        pieces = re.findall(r'[a-zа-яё0-9]+|[\u4e00-\u9fff]+', w)
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            # 字母数字 token 太短（1 字符）无区分度，跳过；但保留 2 字符以上
            if len(piece) < 2 and not re.search(r'[\u4e00-\u9fff]', piece):
                continue
            if piece in stopwords:
                continue
            if piece not in seen:
                seen.add(piece)
                keywords.append(piece)
    # 中文 token 补充 2-gram 子串，提高「打印耗材」对「打印」「耗材」的召回
    extra = []
    for kw in list(keywords):
        if re.search(r'[\u4e00-\u9fff]', kw) and len(kw) >= 2:
            for i in range(len(kw) - 1):
                bigram = kw[i:i + 2]
                if bigram not in seen and bigram not in stopwords:
                    seen.add(bigram)
                    extra.append(bigram)
    keywords.extend(extra)

    if not keywords:
        return []

    # 对每个类目打分：token 命中 search_text 即加分；L3 命中权重更高
    scored = []
    for cat in categories:
        search_text = cat.get('search_text', '')
        path = cat.get('path', '').lower()
        path_ru = cat.get('path_ru', '').lower()
        score = 0
        for kw in keywords:
            if kw in search_text:
                l3_zh = path.rsplit('>', 1)[-1].strip()
                l3_ru = path_ru.rsplit('>', 1)[-1].strip()
                if kw == l3_zh or kw == l3_ru:
                    score += 20
                elif kw in l3_zh or kw in l3_ru:
                    score += 10
                else:
                    score += 3
        if score > 0:
            scored.append((score, cat))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return [cat for _, cat in scored[:top_n]]

    # 兜底：没有任何 token 命中时，不再塞进前 top_n 个无关类目。
    # 用更粗的「单个汉字」再尝试一次，保留任何微弱相关性；仍无则返回空，
    # AI 侧据此明确报告「无候选」而不是在无关类目里强行猜。
    single_chars = []
    for kw in keywords:
        for ch in kw:
            if re.fullmatch(r'[\u4e00-\u9fff]', ch):
                single_chars.append(ch)
    if single_chars:
        char_set = set(single_chars)
        weakly = []
        for cat in categories:
            hits = sum(1 for ch in char_set if ch in cat.get('search_text', ''))
            if hits > 0:
                weakly.append((hits, cat))
        weakly.sort(key=lambda x: x[0], reverse=True)
        return [cat for _, cat in weakly[:top_n]]
    return []


def ai_match_category(source_category, title='', description='', tree=None, prioritize_title=False):
    """使用 AI 进行类目语义匹配

    Args:
        source_category: 采集的原始分类名
        title: 商品标题
        description: 商品描述
        tree: Ozon 类目树（双语），如不提供则从缓存加载

    Returns:
        匹配结果 dict，与 match_category 格式一致
    """
    # 1. 检查 SQLite 缓存（与关键词匹配共享 category_mappings 表）
    cached = _load_cached_match(source_category, title)
    if cached:
        print('[AI匹配] 命中缓存')
        return cached

    # 2. 检查 API Key
    config = _get_ai_config()
    if not config['api_key']:
        print('[AI匹配] API Key 未配置，跳过 AI 匹配')
        return {'matched': False, 'candidates': [], 'reason': 'AI API Key 未配置，请在「店铺管理 > 模型管理」页面中设置'}

    # 3. 获取类目树
    if tree is None:
        try:
            from services.ozon_api import get_category_tree_bilingual
            tree = get_category_tree_bilingual(use_cache=True)
        except Exception as e:
            print(f'[AI匹配] 获取类目树失败: {e}')
            return {'matched': False, 'candidates': [], 'reason': f'获取类目树失败: {e}'}

    if not tree:
        return {'matched': False, 'candidates': [], 'reason': '类目树为空'}

    # 4. 构建类目列表
    all_categories = _build_category_list(tree)
    if not all_categories:
        return {'matched': False, 'candidates': [], 'reason': '类目列表为空'}

    # 4.1 关键词预筛选：从 7400+ 类目中筛选 Top-100 候选（retrieval 阶段）
    # 避免全量类目列表超 token 限制导致截断丢失关键类目
    categories = _filter_candidates_by_keywords(
        all_categories, source_category, title, description, top_n=100
    )
    if len(all_categories) > 100 and len(categories) <= 100:
        print(f'[AI匹配] 关键词预筛选: {len(all_categories)} → {len(categories)} 个候选')

    # 5. 构建 prompt
    # 为控制 token，将类目列表精简为 "ID|路径" 格式
    # 同时展示中俄文路径，帮助 AI 跨语言匹配（如俄文标题 "Зонт" 匹配中文 "伞"）
    cat_list_text = '\n'.join([
        f"{i+1}. [{c['description_category_id']}_{c['type_id']}] {c['path']}"
        + (f" ({c['path_ru']})" if c.get('path_ru') else "")
        for i, c in enumerate(categories)
    ])

    # 截断过长的类目列表（控制在约 8000 token 以内）
    if len(cat_list_text) > 30000:
        cat_list_text = cat_list_text[:30000] + '\n... (类目列表已截断)'
        print(f'[AI匹配] 类目列表过长，已截断（共 {len(categories)} 个类目）')

    # 构建 AI prompt 的商品信息部分
    # 多信号匹配：source_category 为主信号，title + description 为辅助信号
    # 当 source_category 为空（采集器未抓到类目）时，title 是关键信号
    product_info_parts = []
    product_info_parts.append(f"采集分类: {source_category or '无'}")
    if title:
        product_info_parts.append(f"商品标题: {title[:200]}")
    if description:
        product_info_parts.append(f"商品描述: {description[:300]}")
    product_info = '\n'.join(product_info_parts)

    # 根据是否有 source_category 调整系统提示
    has_category = bool(source_category and source_category.strip())
    if has_category and prioritize_title:
        system_prompt = """你是一个跨境电商类目匹配专家。你的任务是从Ozon平台类目列表中，按商品本体选择最匹配的类目。

规则：
1. 以商品标题为主信号，识别实际售卖的商品本体；采集分类可能是 Ozon 页面的大类或不精确分类，只作辅助
2. 商品描述用于核验商品本体，不得把适用设备误判为商品本身（例如 3D 打印线材不是打印机）
3. 从提供的Ozon类目列表中选择最匹配的类目；不确定时降低置信度，不得仅因来源类目名称相似而强行匹配
4. 返回JSON格式，包含以下字段：
   - matched: 是否匹配成功 (true/false)
   - description_category_id: Ozon二级类目ID (数字)
   - type_id: Ozon三级类型ID (数字)
   - label: 匹配的类目路径
   - confidence: 置信度 (\"high\"/\"medium\"/\"low\")
   - reason: 匹配理由 (简短说明)
   - candidates: 候选类目列表 (最多3个，每个含description_category_id, type_id, label)

只返回JSON，不要其他文字。"""
    elif has_category:
        system_prompt = """你是一个跨境电商类目匹配专家。你的任务是根据商品的采集分类信息（辅以标题和描述），从Ozon平台的类目列表中选择最匹配的类目。

规则：
1. 以采集分类为主信号，标题和描述为辅助信号，理解商品的真正品类
2. 从提供的Ozon类目列表中选择最匹配的类目
3. 如果有多个候选，选择最精准（最深层级）的匹配
4. 返回JSON格式，包含以下字段：
   - matched: 是否匹配成功 (true/false)
   - description_category_id: Ozon二级类目ID (数字)
   - type_id: Ozon三级类型ID (数字)
   - label: 匹配的类目路径 (如 "服装和鞋类 > 男士服装 > T恤")
   - confidence: 置信度 ("high"/"medium"/"low")
   - reason: 匹配理由 (简短说明)
   - candidates: 候选类目列表 (最多3个，每个含description_category_id, type_id, label)

只返回JSON，不要其他文字。"""
    else:
        # 多信号模式：source_category 为空，主要依赖标题+描述
        system_prompt = """你是一个跨境电商类目匹配专家。采集器未抓到商品的原始分类，你需要根据商品标题和描述，从Ozon平台的类目列表中选择最匹配的类目。

规则：
1. 仔细分析商品标题和描述，理解商品的真正品类（注意标题可能是俄文）
2. 从提供的Ozon类目列表中选择最匹配的类目
3. 如果有多个候选，选择最精准（最深层级）的匹配
4. 即使不确定也要给出最可能的候选，返回JSON格式，包含以下字段：
   - matched: 是否匹配成功 (true/false)
   - description_category_id: Ozon二级类目ID (数字)
   - type_id: Ozon三级类型ID (数字)
   - label: 匹配的类目路径 (如 "服装和鞋类 > 男士服装 > T恤")
   - confidence: 置信度 ("high"/"medium"/"low")
   - reason: 匹配理由 (简短说明)
   - candidates: 候选类目列表 (最多3个，每个含description_category_id, type_id, label)

只返回JSON，不要其他文字。"""

    user_prompt = f"""{product_info}

Ozon类目列表（格式: [二级类目ID_三级类型ID] 类目路径）:
{cat_list_text}

请为该商品选择最匹配的Ozon类目。"""

    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ]

    # 6. 调用 AI API
    try:
        print(f'[AI匹配] 调用 {config["model"]} 匹配类目: source={source_category[:30]}...')
        ai_response = _call_ai_api(messages, temperature=0.1)
        print(f'[AI匹配] AI返回: {ai_response}')

        # 解析 AI 返回的 JSON
        result = json.loads(ai_response)

        # 标准化返回格式
        matched = result.get('matched', False)
        confidence = result.get('confidence', 'low')

        final_result = {
            'matched': matched and confidence in ('high', 'medium'),
            'confidence': confidence,
            'label': result.get('label', ''),
            'description_category_id': result.get('description_category_id'),
            'type_id': result.get('type_id'),
            'reason': result.get('reason', ''),
            'candidates': result.get('candidates', []),
            '_source': 'ai',
        }

        # 如果匹配成功，确保 ID 是整数
        if final_result['matched']:
            if final_result.get('description_category_id'):
                final_result['description_category_id'] = int(final_result['description_category_id'])
            if final_result.get('type_id'):
                final_result['type_id'] = int(final_result['type_id'])

        # 7. 缓存结果到 SQLite（与关键词匹配共享缓存层）
        _save_cached_match(source_category, title, final_result)

        return final_result

    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        print(f'[AI匹配] API请求失败: HTTP {e.code} - {error_body[:200]}')
        return {'matched': False, 'candidates': [], 'reason': f'API请求失败: HTTP {e.code}'}
    except json.JSONDecodeError as e:
        print(f'[AI匹配] AI返回解析失败: {e}')
        return {'matched': False, 'candidates': [], 'reason': f'AI返回解析失败: {e}'}
    except Exception as e:
        print(f'[AI匹配] 匹配失败: {e}')
        return {'matched': False, 'candidates': [], 'reason': str(e)}
