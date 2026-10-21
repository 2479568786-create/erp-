from flask import Blueprint, request

from models.product import Product
from services.product_analysis_service import analyze_product
from utils.auth import require_login
from utils.response import error_response, handle_errors, success_response

product_analysis_bp = Blueprint("product_analysis", __name__)


@product_analysis_bp.route("/product-analysis", methods=["POST"])
@require_login
@handle_errors
def analyze_route():
    body = request.get_json(silent=True) or {}
    product = body.get("product")
    if not isinstance(product, dict) and body.get("product_id"):
        product = Product.find_by_id(str(body["product_id"]))
    if not isinstance(product, dict):
        return error_response("请提供 product 或 product_id", 400)
    result = analyze_product(product)
    if body.get("persist") and product.get("id"):
        Product.update(product["id"], {"productAnalysis": result})
    return success_response(data=result, msg="商品分析完成")


@product_analysis_bp.route("/products/<product_id>/analysis", methods=["GET"])
@require_login
@handle_errors
def product_analysis_by_id(product_id):
    product = Product.find_by_id(product_id)
    if not product:
        return error_response("商品不存在", 404)
    return success_response(data=analyze_product(product), msg="商品分析完成")
