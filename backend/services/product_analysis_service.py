"""Source-backed product analysis adapted from JLC GLOBAL's product-analysis stage.

The service is deterministic and does not invent unsupported claims.  It accepts the
ERP's product JSON shape, so it can be used by the collection box and the research
pool without creating a second product data model.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


def _attrs(product: dict[str, Any]) -> list[dict[str, Any]]:
    value = product.get("attributes")
    if isinstance(value, dict):
        return [{"name": key, "value": item} for key, item in value.items()]
    return [item for item in (value or []) if isinstance(item, dict)]


def _text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return " ".join(str(x) for x in value.values()) if isinstance(value, dict) else " ".join(map(str, value))
    return str(value or "").strip()


def _attribute(product: dict[str, Any], names: tuple[str, ...]) -> Any:
    wanted = {name.casefold() for name in names}
    for item in _attrs(product):
        name = _text(item.get("name") or item.get("name_cn") or item.get("key")).casefold()
        if name in wanted:
            return item.get("value_cn") or item.get("value") or item.get("sourceValue")
    return None


def _dimensions(value: Any) -> dict[str, Any] | str:
    text = _text(value)
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if len(numbers) < 3:
        return "unknown" if not text else {"raw": text, "source": "product.attributes"}
    return {"length": float(numbers[0]), "width": float(numbers[1]), "height": float(numbers[2]), "unit": "cm", "source": "product.attributes"}


def _weight(value: Any) -> dict[str, Any] | str:
    text = _text(value)
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return "unknown" if not text else {"raw": text, "source": "product.attributes"}
    amount = float(match.group(0))
    if "kg" in text.casefold() or "千克" in text:
        amount *= 1000
    return {"value": round(amount, 2), "unit": "g", "source": "product.attributes"}


def _materials(title: str, value: Any) -> list[str]:
    text = f"{title} {_text(value)}"
    return [marker for marker in ("硅胶", "塑料", "不锈钢", "玻璃", "陶瓷", "金属", "木", "亚克力") if marker in text]


def _sku(item: dict[str, Any]) -> dict[str, Any]:
    options = {}
    raw_options = item.get("option_values")
    if not raw_options and isinstance(item.get("combo"), dict):
        raw_options = item["combo"].items()
    for option in raw_options or []:
        if isinstance(option, dict):
            options[_text(option.get("name_cn") or option.get("name") or "规格")] = _text(option.get("value_cn") or option.get("value"))
        elif isinstance(option, tuple):
            options[str(option[0])] = _text(option[1])
    return {
        "sku_id": str(item.get("sku_id") or item.get("skuId") or item.get("offerId") or "unknown"),
        "name": _text(item.get("sku_name") or item.get("name") or item.get("skuCode") or "unknown"),
        "properties": options,
        "purchase_price_cny": float(item.get("purchase_price") or item.get("sourcePrice") or item.get("price") or 0) or None,
        "image_count": len(item.get("images") or item.get("imageList") or []),
    }


def analyze_product(product: dict[str, Any]) -> dict[str, Any]:
    title = _text(product.get("title") or product.get("title_cn")) or "unknown"
    category = _text(product.get("category") or product.get("categoryName") or product.get("categoryMatch", {}).get("label")) or "unknown"
    dimensions = product.get("dimensions") if isinstance(product.get("dimensions"), dict) and product.get("dimensions") else _dimensions(_attribute(product, ("尺寸", "规格尺寸", "产品尺寸", "长宽高")))
    weight = product.get("weightValue") or product.get("weight") or _attribute(product, ("重量", "净重", "毛重"))
    if isinstance(weight, (int, float)):
        weight = {"value": float(weight), "unit": product.get("weightUnit") or "g", "source": "product.weight"}
    elif not isinstance(weight, dict):
        weight = _weight(weight)
    material_value = _attribute(product, ("材质", "材料", "主体材质"))
    skus = [_sku(item) for item in (product.get("skus") or product.get("skuList") or []) if isinstance(item, dict)]
    risks = []
    if category != "unknown" and title != "unknown" and not any(token and token in title for token in re.split(r"[/ >]+", category)):
        risks.append({"area": "category_fit", "level": "medium", "message": "标题与当前类目可能不一致，请确认类目。", "blocking": False})
    missing_images = sum(1 for item in skus if item["image_count"] == 0)
    if missing_images:
        risks.append({"area": "sku_images", "level": "medium", "message": f"{missing_images} 个 SKU 缺少专属图片。", "blocking": False})
    if not skus:
        risks.append({"area": "sku", "level": "low", "message": "未发现结构化 SKU，发布前需要确认规格。", "blocking": False})
    known = sum([title != "unknown", category != "unknown", dimensions != "unknown", weight != "unknown", bool(skus)])
    score = round(45 + known * 11 - len(risks) * 5, 2)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "schema_version": "1.0.0",
        "product_id": str(product.get("id") or product.get("productId") or "unknown"),
        "source_refs": ["erp.products.json"],
        "product_type": title,
        "category": category,
        "score": max(0, min(100, score)),
        "recommended": "review" if any(item["level"] == "medium" for item in risks) else "continue",
        "facts": {
            "title": title,
            "category": category,
            "brand": _text(product.get("brand")) or "Нет бренда",
            "materials": _materials(title, material_value),
            "dimensions": dimensions,
            "weight": weight,
            "functions": [_text(_attribute(product, (name,))) for name in ("功能", "用途", "供电方式") if _attribute(product, (name,))],
            "skus": skus,
            "image_count": len(product.get("images") or []),
        },
        "missing_information": [
            {"field": "certifications", "reason": "来源未提供可靠认证信息。", "needed_from_human": False},
            {"field": "load_capacity", "reason": "来源未提供可靠承重信息。", "needed_from_human": False},
        ],
        "risks": risks or [{"area": "source", "level": "low", "message": "未发现阻塞性风险。", "blocking": False}],
        "recommendation": {"decision": "review" if risks else "continue", "reason": "分析结果仅使用商品已采集事实。"},
        "processing": {"step": "product_analysis", "status": "completed", "started_at": now, "finished_at": now, "error": None},
    }
