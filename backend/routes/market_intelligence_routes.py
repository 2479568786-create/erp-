"""
市场情报 API 路由（从 JLC 工作台 market-intelligence 原生迁移）

只读接口，数据来自本地 market.sqlite（MarketStore）+ 缓存图片 + JSON 报告。
对应 JLC market_routes.py 的只读部分：
- GET /api/market-intelligence/status
- GET /api/market-intelligence/products
- GET /api/market-intelligence/products/<source_product_id>
- GET /api/market-intelligence/images/<source_product_id>
- GET /api/market-intelligence/keywords
- GET /api/market-intelligence/search-visibility/latest
- GET /api/market-intelligence/traffic-performance/latest

数据采集（probe_ozon_sources + import_bestsellers_report）在 Phase 2 接入。
"""
import json
import mimetypes
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, request, send_file

from services.market_intelligence import (
    MarketStore,
    MarketEnricher,
    OzonAnalyticsApiError,
    OzonAnalyticsPermissionError,
    OzonAnalyticsReadOnlyClient,
    build_search_visibility_plan,
    collect_seller_search_visibility,
)
from utils.response import success_response, error_response, handle_errors
from utils.auth import require_login

market_intelligence_bp = Blueprint("market_intelligence", __name__)

# 数据目录：与 ERP 其它数据一致，放在 backend/data/market-intelligence/
_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "market-intelligence"
MARKET_DB_PATH = _DATA_DIR / "market.sqlite"
MARKET_CATEGORIES_PATH = _DATA_DIR / "categories.json"
MARKET_TREND_REPORT_PATH = _DATA_DIR / "trend-latest.json"
MARKET_IMAGE_CACHE_DIR = _DATA_DIR / "images"
MARKET_SEARCH_VISIBILITY_PLAN_PATH = _DATA_DIR / "search-visibility-plan-latest.json"
MARKET_SEARCH_VISIBILITY_PLAN_CACHE_DIR = _DATA_DIR / "search-visibility-plans"
MARKET_TRAFFIC_PERFORMANCE_PLAN_PATH = _DATA_DIR / "traffic-performance-plan-latest.json"


def load_optional_json(path, default):
    """读取 JSON；文件缺失或损坏时返回 default。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, OSError, ValueError):
        return default


def _atomic_write_json(path, data):
    """原子写入 JSON（先写临时文件再替换）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _market_category(category_key):
    """查找已启用的选品类目；找不到返回 None。"""
    config = load_optional_json(MARKET_CATEGORIES_PATH, {"categories": []})
    for category in config.get("categories") or []:
        if category.get("key") == category_key and category.get("enabled", True):
            return dict(category)
    return None


def _search_visibility_plan_cache_path(shop_id):
    safe = re.sub(r"[^0-9A-Za-z_-]+", "-", str(shop_id or "")).strip("-")
    return MARKET_SEARCH_VISIBILITY_PLAN_CACHE_DIR / (f"{safe or 'unknown'}.json")


def _load_search_visibility_plan(shop_id=""):
    requested = str(shop_id or "").strip()
    if requested:
        cache_path = _search_visibility_plan_cache_path(requested)
        if cache_path.is_file():
            return load_optional_json(cache_path, {})
        if MARKET_SEARCH_VISIBILITY_PLAN_PATH.is_file():
            legacy = load_optional_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH, {})
            if str(legacy.get("shop_id") or "").strip() == requested:
                _atomic_write_json(cache_path, legacy)
                return legacy
        return {}
    if MARKET_SEARCH_VISIBILITY_PLAN_PATH.is_file():
        return load_optional_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH, {})
    return {}


def _write_search_visibility_plan(plan):
    """原子写入最新计划 + 按 shop_id 写入分店铺缓存。"""
    _atomic_write_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
    shop_id = str(plan.get("shop_id") or "").strip()
    if shop_id:
        _atomic_write_json(_search_visibility_plan_cache_path(shop_id), plan)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _seller_search_window(period_days):
    days = max(1, min(30, int(period_days)))
    date_to = datetime.now(timezone.utc) - timedelta(days=3)
    date_from = date_to - timedelta(days=days)
    return (
        date_from.isoformat(timespec="seconds").replace("+00:00", "Z"),
        date_to.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _seller_order_window(period_days):
    days = max(1, min(90, int(period_days)))
    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=days)
    return (
        date_from.isoformat(timespec="seconds").replace("+00:00", "Z"),
        date_to.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _search_shop_credentials(store_id=""):
    """使用 ERP 自身店铺凭据（加密存储），只读调用 Ozon Analytics。"""
    from services.ozon_api import _get_store_credentials
    client_id, api_key = _get_store_credentials(store_id or None)
    if not client_id or not api_key:
        return None, None
    return str(client_id), api_key


def _unavailable_search_visibility_plan(shop_id, period_days, date_from, date_to, state, notice, details=None):
    return {
        "schema_version": "1.0.0",
        "mode": "read_only",
        "source": "ozon_seller_search_visibility",
        "shop_id": shop_id,
        "period_days": period_days,
        "recommended_schedule_days": 14,
        "generated_at": _now_iso(),
        "available": False,
        "notice": notice,
        "summary": {"products": 0, "stable_tag_only": 0, "title_optimization_candidates": 0,
                    "tag_only_candidates": 0, "insufficient_data": 0},
        "batches": [],
        "actions": [],
        "source_status": {"state": state, "details": details or {}},
        "safety": {"dry_run_only": True, "read_only": True, "write_api_calls": 0,
                   "inventory_api_calls": 0,
                   "requires_explicit_write_scope_before_ozon_update": True},
        "write_api_calls": 0,
        "inventory_api_calls": 0,
        "date_from": date_from,
        "date_to": date_to,
    }


@market_intelligence_bp.route("/market-intelligence/status", methods=["GET"])
@handle_errors
@require_login
def market_intelligence_status():
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    categories = load_optional_json(MARKET_CATEGORIES_PATH, {"schema_version": "1.0.0", "categories": []})
    counts = store.counts()
    ranking_available = counts["products"] > 0
    sources = store.list_source_status()
    latest_checked_at = max((item.get("checked_at") or "" for item in sources), default="") or "unknown"
    trend = load_optional_json(MARKET_TREND_REPORT_PATH, {
        "state": "collecting", "days_collected": 1 if counts["snapshots"] else 0,
        "notice": "等待每日快照积累后生成趋势对比",
    })
    return success_response(data={
        "schema_version": "1.0.0",
        "module_state": "data_ready" if ranking_available else "data_source_setup",
        "categories": categories.get("categories") or [],
        "sources": sources,
        "counts": counts,
        "ranking_available": ranking_available,
        "last_updated_at": latest_checked_at,
        "trend": trend,
        "notice": "Ozon 官方市场商品数据已就绪" if ranking_available else "真实市场商品数据接入后才会显示热销榜和飙升榜",
    })


@market_intelligence_bp.route("/market-intelligence/products", methods=["GET"])
@handle_errors
@require_login
def market_intelligence_products():
    ranking = request.args.get("ranking", "hot")
    category = request.args.get("category", "home")
    period = int(request.args.get("period", 30))
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 24))
    q = request.args.get("q", "")
    if period not in {7, 30}:
        return error_response("榜单周期只支持7天或30天", 400)
    category_rule = _market_category(category)
    if category_rule is None:
        return error_response("未找到该选品类目", 404)
    if period == 7:
        return success_response(data={
            "schema_version": "1.0.0",
            "items": [], "total": 0, "page": max(1, page), "page_size": page_size,
            "ranking": ranking, "category_key": category, "period_days": 7,
            "available": False,
            "notice": "当前官方公开商品榜单只提供近30天数据；7天榜将在积累每日快照后开放",
        })
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    try:
        result = store.list_ranked_products(
            category_rule=category_rule, ranking=ranking, page=page, page_size=page_size, query=q,
        )
    except ValueError as error:
        return error_response(str(error), 400)
    return success_response(data={
        "schema_version": "1.0.0",
        **result,
        "period_days": 30,
        "available": True,
        "notice": "数据来自 Ozon 官方免费市场分析报告；排序与FBS适配度由本地规则计算",
    })


@market_intelligence_bp.route("/market-intelligence/products/<source_product_id>", methods=["GET"])
@handle_errors
@require_login
def market_intelligence_product(source_product_id):
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    product = store.get_product(source_product_id)
    if product is None:
        return error_response("未找到该 Ozon 商品", 404)
    product = MarketEnricher(store, MARKET_IMAGE_CACHE_DIR).enrich_product(source_product_id)
    enrichment = dict(product.pop("enrichment", {}) or {})
    merged_keywords = []
    seen_keywords = set()
    for keyword in product.get("keywords") or []:
        normalized = " ".join(str(keyword.get("keyword_ru") or "").lower().split())
        if not normalized or normalized in seen_keywords:
            continue
        seen_keywords.add(normalized)
        merged_keywords.append(keyword)
    product["keywords"] = merged_keywords
    return success_response(data={
        "schema_version": "1.0.0",
        **product,
        "image_state": enrichment.get("image_state") or ("ready" if product.get("image_url") != "unknown" else "syncing"),
        "keyword_state": enrichment.get("keyword_state") or ("ready" if product["keywords"] else "source_pending"),
        "keyword_notice": "关键词根据公开数据和商品信息整理",
    })


@market_intelligence_bp.route("/market-intelligence/images/<source_product_id>", methods=["GET"])
@handle_errors
@require_login
def market_intelligence_image(source_product_id):
    if not re.fullmatch(r"[0-9A-Za-z_-]+", source_product_id):
        return error_response("主图不存在", 404)
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    try:
        enrichment = store.get_product_enrichment(source_product_id)
    except KeyError:
        return error_response("主图不存在", 404)
    raw_path = enrichment.get("image_local_path")
    if not raw_path or raw_path == "unknown":
        return error_response("主图仍在同步", 404)
    image_path = Path(str(raw_path)).resolve()
    cache_root = MARKET_IMAGE_CACHE_DIR.resolve()
    if not image_path.is_file() or cache_root not in image_path.parents:
        return error_response("主图仍在同步", 404)
    response = send_file(
        str(image_path),
        mimetype=mimetypes.guess_type(image_path.name)[0] or "application/octet-stream",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@market_intelligence_bp.route("/market-intelligence/keywords", methods=["GET"])
@handle_errors
@require_login
def market_intelligence_keywords():
    category = request.args.get("category", "")
    limit = int(request.args.get("limit", 12))
    if category:
        if _market_category(category) is None:
            return error_response("未找到该选品类目", 404)
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    items = [
        item for item in store.list_keywords(category_key=category, limit=max(limit * 4, 50))
        if (item.get("evidence") or {}).get("source") == "ozon_official_search_queries"
    ][:max(1, min(200, int(limit)))]
    return success_response(data={
        "schema_version": "1.0.0",
        "items": items,
        "total": len(items),
        "category_key": category or "all",
        "period_days": 7,
        "available": bool(items),
        "notice": "数据来自 Ozon 官方近7天搜索查询；中文为本地翻译，不改变俄文原词",
    })


@market_intelligence_bp.route("/market-intelligence/search-visibility/latest", methods=["GET"])
@handle_errors
@require_login
def market_intelligence_search_visibility_latest():
    store_id = request.args.get("store_id", "")
    plan = _load_search_visibility_plan(store_id)
    if not plan:
        return success_response(data={
            "schema_version": "1.0.0",
            "available": False,
            "shop_id": str(store_id or "").strip(),
            "notice": "这个店铺还没有下载 Ozon 商品信息和搜索词。",
            "write_api_calls": 0,
            "inventory_api_calls": 0,
        })
    if store_id:
        _atomic_write_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
    return success_response(data={
        **plan,
        "available": bool(plan.get("available", True)),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    })


@market_intelligence_bp.route("/market-intelligence/search-visibility/sync", methods=["POST"])
@handle_errors
@require_login
def market_intelligence_search_visibility_sync():
    """只读同步：用 ERP 店铺凭据调 Ozon 卖家搜索可见度，生成本地计划(不写 Ozon)。"""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return error_response("同步请求需要 JSON 对象", 400)
    period_days = max(1, min(30, int(payload.get("period_days") or 15)))
    order_period_days = max(1, min(90, int(payload.get("order_period_days") or 90)))
    requested_limit = payload.get("product_limit")
    product_limit = 0 if requested_limit in {None, "", 0, "0", "all", "ALL"} else max(1, int(requested_limit))
    store_id = str(payload.get("store_id") or "").strip()
    client_id, api_key = _search_shop_credentials(store_id)
    if not client_id or not api_key:
        return error_response("没有可用的 Ozon 店铺只读凭据", 422)
    date_from, date_to = _seller_search_window(period_days)
    order_date_from, order_date_to = _seller_order_window(order_period_days)
    shop_id = store_id or "default"
    previous_plan = _load_search_visibility_plan(shop_id)
    client = OzonAnalyticsReadOnlyClient(client_id, api_key)
    try:
        source = collect_seller_search_visibility(
            client,
            shop_id=shop_id,
            date_from=date_from,
            date_to=date_to,
            order_date_from=order_date_from,
            order_date_to=order_date_to,
            order_period_days=order_period_days,
            page_size=1000,
            max_products=product_limit,
            period_days=period_days,
        )
        plan = build_search_visibility_plan(source)
        if previous_plan:
            preserved = {}
            for action in (previous_plan.get("actions") or []):
                pid = str(action.get("product_id") or "").strip()
                if pid:
                    preserved[pid] = {k: action.get(k) for k in
                                      ("last_upload", "last_yandex_wordstat_import",
                                       "last_ozon_product_query_import", "last_seerfar_keyword_import")
                                      if isinstance(action.get(k), dict)}
            for action in plan.get("actions") or []:
                pid = str(action.get("product_id") or "").strip()
                if pid in preserved:
                    action.update(preserved[pid])
        product_count = len(source.get("items") or [])
        query_count = sum(int(((a.get("evidence") or {}).get("totals") or {}).get("query_count") or 0)
                          for a in plan.get("actions") or [])
        if query_count > 0:
            source_state = "connected"
            notice = ("已从 Ozon 读取 %d 个商品、%d 条搜索词；没有提交 Ozon 信息，没有调用写接口。"
                      % (product_count, query_count))
        elif product_count > 0:
            source_state = "product_info_only"
            notice = "已读取 %d 个商品信息，但搜索词未拉取；没有调用写接口。" % product_count
        else:
            source_state = "empty"
            notice = "当前店铺没有返回商品信息。没有提交 Ozon 信息，没有调用写接口。"
        plan.update({
            "mode": "read_only",
            "available": product_count > 0,
            "shop_id": shop_id,
            "date_from": date_from,
            "date_to": date_to,
            "order_period_days": order_period_days,
            "order_date_from": order_date_from,
            "order_date_to": order_date_to,
            "source_status": {"state": source_state, "details": {
                "query_count": query_count,
                "product_count": product_count,
                "product_limit": "all" if product_limit == 0 else product_limit,
                "order_period_days": order_period_days,
                "order_read_api_calls": source.get("order_read_api_calls") or 0,
            }},
            "notice": notice,
            "write_api_calls": 0,
            "inventory_api_calls": 0,
        })
        plan.setdefault("safety", {}).update({"read_only": True, "write_api_calls": 0, "inventory_api_calls": 0})
    except OzonAnalyticsPermissionError as exc:
        premium = "premium" in str(exc).lower()
        plan = _unavailable_search_visibility_plan(
            shop_id, period_days, date_from, date_to,
            "premium_required" if premium else "permission_denied",
            "Ozon 搜索可见度需要 Premium 权限，当前店铺没有分析权限；没有提交 Ozon 信息。",
            {"http_status": exc.status_code or 403},
        )
    except (OzonAnalyticsApiError, ValueError) as exc:
        plan = _unavailable_search_visibility_plan(
            shop_id, period_days, date_from, date_to,
            "error",
            "Ozon 搜索词读取失败；没有提交 Ozon 信息，没有调用写接口。",
            {"error_type": type(exc).__name__},
        )
    _write_search_visibility_plan(plan)
    return success_response(data=plan, msg="搜索可见度计划已生成(只读)")


@market_intelligence_bp.route("/market-intelligence/search-visibility/dry-run", methods=["POST"])
@handle_errors
@require_login
def market_intelligence_search_visibility_dry_run():
    """dry-run：把调用方提供的采集结果本地构建计划，不触发任何 Ozon 调用。"""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return error_response("dry-run 请求需要 JSON 对象", 400)
    plan = build_search_visibility_plan(payload)
    plan["available"] = True
    plan["notice"] = "本地生成搜索可见度优化计划；没有提交 Ozon 信息，没有调用写接口。"
    # dry-run 仅返回结果，不覆盖已保存的最新计划（避免清空真实关键词数据）
    plan["persisted"] = False
    return success_response(data=plan, msg="dry-run 计划已生成(未覆盖最新方案)")


@market_intelligence_bp.route("/market-intelligence/traffic-performance/latest", methods=["GET"])
@handle_errors
@require_login
def market_intelligence_traffic_performance_latest():
    if not MARKET_TRAFFIC_PERFORMANCE_PLAN_PATH.is_file():
        return success_response(data={
            "schema_version": "1.0.0",
            "available": False,
            "notice": "还没有流量表现 dry-run 方案。",
            "write_api_calls": 0,
            "inventory_api_calls": 0,
            "ad_budget_api_calls": 0,
        })
    return success_response(data={
        **load_optional_json(MARKET_TRAFFIC_PERFORMANCE_PLAN_PATH, {}),
        "available": True,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
        "ad_budget_api_calls": 0,
    })
