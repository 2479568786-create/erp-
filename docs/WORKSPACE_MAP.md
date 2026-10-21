# 工作区代码索引

此仓库整理的是 2026-09-06 本地快照。各工作区主要代码仍按前后端职责存放，以下索引便于按功能定位；本次没有重构或改写业务代码。

| 工作区 | 前端入口 | 后端主要实现 |
|---|---|---|
| 财务中心 | `frontend/js/pages/finance-center.js` | `backend/services/finance_service.py`、`backend/routes/dashboard_control_routes.py` |
| 采集箱 | `frontend/js/pages/collect.js` | `backend/routes/compat_routes.py`、`backend/routes/product_routes.py` |
| 价格计算器 | `frontend/js/pages/calculator.js` | `backend/routes/pricing_routes.py`、`backend/services/pricing_service.py`、`backend/services/jlc_pricing_service.py` |
| 在线商品及编辑 | `frontend/js/pages/online-products.js`、`frontend/js/pages/product-cards.js` | `backend/routes/online_product_routes.py`、`backend/routes/product_card_routes.py`、`backend/services/publish_service.py` |
| 订单管理 | `frontend/js/pages/orders.js` | `backend/routes/order_routes.py`、`backend/services/order_service.py`、`backend/models/order.py` |
| AI 询盘助手 | `frontend/js/pages/insights.js` | `backend/routes/insight_routes.py` |
| 商品数据分析 | `frontend/js/pages/seller-insights.js` | `backend/routes/seller_insights_routes.py`、`backend/services/product_analysis_service.py` |
| JLC 研究 | 本快照没有独立 JLC 研究工作区前端或完整研究模块；仅含计算器中的 JLC RETS 定价和相关后端服务。 | `backend/services/jlc_pricing_service.py` |
| 图片管理 | `frontend/js/pages/media.js` | `backend/routes/gallery_routes.py`、`backend/routes/watermark_routes.py`、`backend/routes/pixpix_routes.py`、`backend/services/storage_service.py` |
| ERP 整体框架 | `frontend/index.html`、`frontend/js/app.js`、`frontend/js/router.js`、`frontend/js/api.js` | `backend/app.py`、`backend/config.py`、`backend/db.py`、`backend/schema.sql`、`backend/models/`、`backend/routes/`、`backend/services/` |

## 发布范围说明

- 历史 `.bak*` 文件、Python 字节码缓存、本地环境配置和本地启动脚本未纳入公开仓库。
- 本快照不包含独立 JLC 研究工作区；该部分需从其独立项目/部署源另行整理，不能由本索引代替。
- 启动部署前请自行配置密钥、Ozon 店铺凭证、AI 服务凭证及对象存储凭证；不要提交真实凭证或生产数据。
