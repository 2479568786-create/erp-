"""
路由注册模块
集中管理所有蓝图路由的注册
"""
from flask import Blueprint
from routes.product_routes import product_bp
from routes.publish_routes import publish_bp
from routes.publish_record_routes import publish_record_bp
from routes.notice_routes import notice_bp
from routes.auth_routes import auth_bp
from routes.store_routes import store_bp
from routes.category_routes import category_bp
from routes.model_routes import model_bp
from routes.pricing_routes import pricing_bp
from routes.ozon_data_routes import ozon_data_bp
from routes.watermark_routes import watermark_bp
from routes.exchange_rate_routes import exchange_rate_bp
from routes.selection_routes import selection_bp
from routes.favorite_routes import favorite_bp
from routes.online_product_routes import online_product_bp
from routes.dashboard_routes import dashboard_bp
from routes.gallery_routes import gallery_bp
from routes.order_routes import order_bp
from routes.research_routes import research_bp
from routes.my_selection_routes import my_selection_bp
from routes.insight_routes import insight_bp
from routes.compat_routes import compat_bp
from routes.dashboard_control_routes import dashboard_control_bp
from routes.product_card_routes import product_card_bp
from routes.auto_save_routes import auto_save_bp
from routes.ai_results_routes import ai_results_bp
from routes.product_analysis_routes import product_analysis_bp
from routes.seller_insights_routes import seller_insights_bp
from routes.public_notification_routes import public_notification_bp
from routes.pixpix_routes import pixpix_bp

try:
    from routes.ad_monitor_routes import ad_monitor_bp
except ModuleNotFoundError:
    ad_monitor_bp = None
try:
    from routes.promo_routes import promo_bp
except ModuleNotFoundError:
    promo_bp = None


def register_blueprints(app):
    """注册所有蓝图路由到 Flask 应用"""
    app.register_blueprint(product_bp, url_prefix='/api')
    app.register_blueprint(publish_bp, url_prefix='/api')
    app.register_blueprint(publish_record_bp, url_prefix='/api')
    app.register_blueprint(notice_bp, url_prefix='/api')
    app.register_blueprint(auth_bp, url_prefix='/api')
    app.register_blueprint(store_bp, url_prefix='/api')
    app.register_blueprint(category_bp, url_prefix='/api')
    app.register_blueprint(model_bp, url_prefix='/api')
    app.register_blueprint(pricing_bp, url_prefix='/api')
    app.register_blueprint(ozon_data_bp, url_prefix='/api')
    app.register_blueprint(watermark_bp, url_prefix='/api')
    app.register_blueprint(exchange_rate_bp, url_prefix='/api')
    app.register_blueprint(selection_bp, url_prefix='/api')
    app.register_blueprint(favorite_bp, url_prefix='/api')
    app.register_blueprint(online_product_bp, url_prefix='/api')
    app.register_blueprint(dashboard_bp, url_prefix='/api')
    app.register_blueprint(gallery_bp, url_prefix='/api')
    app.register_blueprint(order_bp, url_prefix='/api')
    app.register_blueprint(research_bp, url_prefix='/api')
    app.register_blueprint(my_selection_bp, url_prefix='/api')
    app.register_blueprint(insight_bp, url_prefix='/api')
    app.register_blueprint(compat_bp, url_prefix='/api')
    app.register_blueprint(dashboard_control_bp, url_prefix='/api')
    app.register_blueprint(product_card_bp, url_prefix='/api')
    app.register_blueprint(auto_save_bp, url_prefix='/api')
    app.register_blueprint(ai_results_bp, url_prefix='/api')
    app.register_blueprint(product_analysis_bp, url_prefix='/api')
    app.register_blueprint(seller_insights_bp, url_prefix='/api')
    app.register_blueprint(public_notification_bp, url_prefix='/api')
    if ad_monitor_bp:
        app.register_blueprint(ad_monitor_bp, url_prefix='/api')
    if promo_bp:
        app.register_blueprint(promo_bp, url_prefix='/api')
    app.register_blueprint(pixpix_bp, url_prefix='/api')
