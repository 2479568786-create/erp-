"""Deterministic JLC-style pricing profile for the ERP.

This keeps the existing ERP pricing profile intact and exposes the JLC assumptions
as an explicit profile. Rates are configurable through the request or ERP pricing
configuration; no workbook or network call is required at runtime.
"""
from __future__ import annotations

import math
from typing import Any


ROUTES = (
    ("RETS Extra Small Economy", None, 500, None, 1500, 60, 90),
    ("RETS Budget Economy", 500, 30000, None, 1500, 60, 150),
    ("RETS Small Economy", None, 2000, 1500, 7000, 60, 150),
    ("RETS Big Standard", 2000, 30000, 1500, 7000, 150, 250),
    ("RETS Premium Small Economy", None, 5000, 7000, 250000, 150, 250),
    ("RETS Premium Big Economy", 5000, 30000, 7000, 250000, 150, 310),
)


def _eligible(weight_g: float, value_rub: float, dimensions: dict[str, float], route: tuple) -> bool:
    _, min_weight, max_weight, min_value, max_value, max_side, max_sum = route
    return ((min_weight is None or weight_g > min_weight) and weight_g <= max_weight
            and (min_value is None or value_rub > min_value) and value_rub <= max_value
            and max(dimensions.values()) <= max_side and sum(dimensions.values()) <= max_sum)


def calculate_jlc_price(*, cost_cny: float, weight_g: float, length_cm: float, width_cm: float,
                        height_cm: float, exchange_rate: float = 12.0, target_margin: float = 0.5,
                        commission_rate: float = 0.18, logistics_commission_rate: float = 0.02,
                        acquiring_fee_rate: float = 0.02, withdrawal_fee_rate: float = 0.012,
                        packing_fee_cny: float = 2.0, shipping_rate_per_kg: float = 80.0,
                        shipping_base_fee_cny: float = 0.0, volumetric_divisor: float = 6000.0,
                        competitor_price_min: float | None = None, competitor_price_max: float | None = None) -> dict[str, Any]:
    dimensions = {"length": max(float(length_cm or 0), 0), "width": max(float(width_cm or 0), 0), "height": max(float(height_cm or 0), 0)}
    actual_weight = max(float(weight_g or 0), 0)
    volumetric_weight = (dimensions["length"] * dimensions["width"] * dimensions["height"] / volumetric_divisor * 1000) if volumetric_divisor > 0 else 0
    billable = max(actual_weight, volumetric_weight)
    variable_rate = commission_rate + logistics_commission_rate + acquiring_fee_rate + withdrawal_fee_rate
    if variable_rate + target_margin >= 1:
        raise ValueError("JLC 费率与目标利润率之和必须小于 100%")
    candidates = []
    for route in ROUTES:
        # RETS route fees are represented by the ERP's configured per-kg rate.
        shipping_cny = shipping_base_fee_cny + shipping_rate_per_kg * billable / 1000
        fixed_cny = float(cost_cny or 0) + shipping_cny + packing_fee_cny
        raw_rub = fixed_cny * exchange_rate / (1 - variable_rate - target_margin)
        if _eligible(billable, raw_rub, dimensions, route):
            candidates.append((shipping_cny, route, raw_rub))
    if not candidates:
        raise ValueError("没有符合 JLC RETS 重量、金额和尺寸限制的线路")
    shipping_cny, route, raw_rub = min(candidates, key=lambda item: (item[0], item[1][0]))
    suggested = math.ceil(raw_rub / exchange_rate) * exchange_rate
    old_price = suggested * 1.2
    competitor_status = None
    if competitor_price_min is not None and competitor_price_max is not None:
        competitor_status = "below_market" if suggested < competitor_price_min else "above_market" if suggested > competitor_price_max else "in_range"
    total_fee = suggested * variable_rate
    profit = suggested - float(cost_cny or 0) * exchange_rate - shipping_cny * exchange_rate - packing_fee_cny * exchange_rate - total_fee
    return {
        "profile": "jlc_global_rets",
        "suggested_price_rub": round(suggested, 2),
        "old_price_rub": round(old_price, 2),
        "profit_rub": round(profit, 2),
        "profit_rate": round(profit / suggested if suggested else 0, 4),
        "competitor_status": competitor_status,
        "shipping": {"route_name": route[0], "actual_weight_g": round(actual_weight, 2), "volumetric_weight_g": round(volumetric_weight, 2), "billable_weight_g": round(billable, 2), "shipping_cost_cny": round(shipping_cny, 2), "source": "erp_pricing_config"},
        "fees": {"commission_rate": commission_rate, "logistics_commission_rate": logistics_commission_rate, "acquiring_fee_rate": acquiring_fee_rate, "withdrawal_fee_rate": withdrawal_fee_rate, "packing_fee_cny": packing_fee_cny},
        "inputs": {"cost_cny": cost_cny, "exchange_rate": exchange_rate, "target_margin": target_margin, "dimensions_cm": dimensions},
    }
