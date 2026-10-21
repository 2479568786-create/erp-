# OZON ERP（精铺增强版）

> 交付物：结合开源底子 GeekOzon OZON-ERP（Apache-2.0）与涂总自有「OZON 精铺助手」文案规范融合而成的新 ERP。
> 定位：适配 OZON 精铺卖家的全链路运营系统（上架 / 店铺数据 / 订单 / AI 商品卡片 / 多店铺 / 选品大盘）。
> 状态：本地运行版（2026-09-06），尚未部署云端，待租用新云主机后上线。

---

## 一、六项需求覆盖

| # | 需求 | 状态 | 说明 |
|---|---|---|---|
| 1 | 顺利上架 | ✅ 完整 | 采集 → AI 类目匹配 → 属性填表 → 图片上传 → 发布 → 状态轮询 → 价格库存更新（底子自带，生产级） |
| 2 | 读取店铺数据 | ✅ 增强 | 在线商品 / 价格 / 库存（底子）；**订单数据（本次新增）** |
| 3 | 管理订单 | ✅ 新增 | **FBS/FBO 订单列表 / 详情 / 状态筛选 / 同步 / FBS 发货出库** |
| 4 | AI 编写商品卡片 | ✅ 融合 | 融合自有精铺助手规范：**完整中俄双语 Listing**、标题（≤130 字符）、描述、关键词、翻译 |
| 5 | 多店铺管理 | ✅ 完整 | 多店铺 API Key 加密管理、分组、授权状态（底子自带） |
| 6 | 选品大盘 | ⚠️ 基础 | 选品分析、收藏夹（底子自带框架，榜单/类目深度分析列为后续迭代） |

---

## 二、本次新增/改动清单

### 后端
| 文件 | 内容 |
|---|---|
| `schema.sql` | 追加 `orders` 表（posting_number 唯一、店铺、状态、金额、商品明细 JSON、财务 JSON） |
| `models/order.py` | 订单模型：幂等 upsert、多条件查询、状态统计、中文状态映射 |
| `services/ozon_api.py` | 追加 FBS/FBO 发货单 API：`list_fbs_postings` / `get_fbs_posting_detail` / `ship_fbs_postings` / `list_fbo_postings` / `get_fbo_posting_detail` |
| `services/order_service.py` | 订单同步服务：拉取→解析→入库（幂等）、单条状态刷新、FBS 发货 |
| `routes/order_routes.py` | 订单 API：列表 / 统计 / 详情 / 同步 / 刷新 / 发货 / 批量删除 |
| `routes/__init__.py` | 注册订单蓝图 |
| `services/ai_service.py` | 注入 OZON 平台规范（标题 ≤130 字符、标签下划线、禁极限词）；新增 `tags`/`listing` 类型 |
| `routes/model_routes.py` | 新增 `/api/ai/generate`（通用 AI 入口，**修复底子前后端断链**）与 `/api/ai/generate-listing`（中俄双语完整卡片的 `listing` 类型） |
| `app.py` | debug 开关改为跟随 `FLASK_DEBUG`（生产部署可用） |

### 前端
| 文件 | 内容 |
|---|---|
| `js/pages/orders.js`（新） | 订单管理页：FBS/FBO Tab、状态筛选、搜索、同步、详情抽屉、发货弹窗 |
| `index.html` | 侧栏「订单管理」菜单（FBS/FBO 子项）+ 引入 orders.js |

---

## 三、快速开始（本地）

```bash
# Windows（推荐）
双击 start.bat          # 或手动执行下方命令

# 手动
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
set SECRET_KEY=your-secret-key   # 生产必配固定值
set FLASK_DEBUG=0
.venv\Scripts\python.exe app.py
```

浏览器打开 **http://127.0.0.1:5000**

> 登录：开发模式下输入任意用户名/密码即自动创建管理员账号（本地方便；云主机部署时建议收敛为固定账号）。

---

## 四、首次配置（三步上手）

### 1. 配置店铺（读取店铺数据 / 同步订单前置）
- 侧栏 → **店铺管理** → 添加店铺
- 填 Ozon **Client-Id** 与 **Api-Key**（卖家后台 设置→API Keys 生成）
- 授权状态变为 active 后，即可同步在线商品与订单

### 2. 同步/管理订单（需求 2、3）
- 侧栏 → **订单管理** → 右上角「同步订单」（可选店铺、近 N 天，默认 FBS+FBO）
- 列表按状态筛选（待打包/待发货/配送中/已送达/已取消/仲裁）
- 详情抽屉查看客户、地址、商品明细与金额
- **待打包/待发货**的 FBS 订单可直接「发货出库」（写回 Ozon `/v3/posting/fbs/ship`）

### 3. 配置 AI 撰写（需求 4）
- 侧栏 → **AI 撰写** → 首次使用需配置 AI 提供商（DeepSeek 或通义千问）
- 底子支持 OpenAI 兼容接口：`base_url`（如 `https://api.deepseek.com`）+ `model`（如 `deepseek-chat`）+ `api_key`
- 输入商品信息 → 选类型（**完整 Listing** / 标题优化 / 描述生成 / 关键词 / 翻译）→ AI 生成
- 完整 Listing 输出：俄语标题 + 中文标题 + 主题标签 + 俄语/中文简介 + 俄语/中文详情，符合 OZON 平台规范

### 上架链路（需求 1）
- 采集箱（URL/批量/浏览器插件采集）→ 商品信息 → AI 类目匹配 → 属性填写 → 发布上架 → 上架记录跟踪

---

## 五、云主机部署指引（新云主机到位后）

准备好：Windows/Linux 云主机（建议 2C4G 起步），域名可选。

```bash
# Linux 部署任选其一（示例用 gunicorn）
cd /opt/ozon-erp
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt gunicorn
export SECRET_KEY='生产环境强随机密钥'
export FLASK_DEBUG=0
# 后端生产跑（前台先验证）
.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 --chdir backend 'app:create_app()'

# systemd 托管（推荐）
# /etc/systemd/system/ozon-erp.service:
#   [Service]
#   WorkingDirectory=/opt/ozon-erp/backend
#   Environment=SECRET_KEY=xxx FLASK_DEBUG=0
#   ExecStart=/opt/ozon-erp/.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 app:create_app()
#   Restart=always
```

部署注意事项：
1. **SECRET_KEY 必须配置固定强随机值**（否则会话重启即失效）
2. 数据库为 SQLite（`backend/data/geekozon.db`），**务必做定期备份**（直接复制该文件即可）
3. 前端 `index.html` 引用了 CDN（tailwindcss / lucide），云主机需保证能访问；若国内云主机访问受限，将这两个资源本地化（后续可做）
4. 类目树首次同步需联网调 Ozon API；同步后本地缓存 30 天
5. 对外暴露需加 HTTPS（Nginx/Caddy）并收紧 `CORS_ALLOWED_ORIGINS`
6. 登录为「任意账号自动建号」的开发模式，正式对外建议先加访问控制（如防火墙 IP 白名单）或改造注册流程

---

## 六、技术栈与合规

- **后端**：Python 3.x + Flask + SQLite（WAL 模式）+ 原生 urllib（无重型依赖）
- **前端**：原生 JS SPA（Hash 路由），零框架依赖（Tailwind/Lucide 由 CDN 提供）
- **合规**：底子为 Apache-2.0（可商用、可闭源衍生，保留版权声明即可）；订单模块新写代码归本项目
- **License**：保留原 `backend/LICENSE`（Apache-2.0）

---

## 七、已知限制与后续迭代

| 项 | 现状 | 计划 |
|---|---|---|
| 订单财务（应计费用/对账） | 未接入 | 后续（保留 financial_json 字段可扩展） |
| FBS 面单/商品码标签打印 | 未接入（/act 系接口未封装） | 后续迭代 |
| 选品大盘深度（榜单/类目分析） | 基础框架 | 后续迭代 |
| AI 生图（白底/场景/卖点图） | 未接入（涂总明确本轮不做） | 可复用自有 Seedream 链路 |
| CDN 本地化 | 未做 | 云主机遇到访问问题再做 |

---

## 附：关键接口速查

```
GET    /api/orders                       订单列表
GET    /api/orders/stats                 订单状态统计
GET    /api/orders/<posting_number>      订单详情
POST   /api/orders/sync                  同步订单（store_id/fulfillment/days）
POST   /api/orders/<posting_number>/ship FBS 发货出库
POST   /api/ai/generate                  通用 AI 生成（title/description/keywords/translate/tags/listing）
POST   /api/ai/generate-listing          中俄双语完整 Listing
POST   /api/health                       健康检查

# ===== 三功能移植新增（选品池/询盘助手/采集插件） =====
GET    /api/product-research             选品池列表（kind/status/keyword）
POST   /api/product-research             加入选品（自动算蓝海评分）
POST   /api/product-research/extract-mpstats  MPStats 截图识别指标
GET    /api/ai-insights                  AI 询盘助手（订单+店铺+商品+上架记录）
# 老 Chrome 采集插件兼容端点（返回原始 JSON，非 {code,msg,data}）
GET    /api/config                       插件运行时配置
GET    /api/auth/me                      登录状态
GET    /api/assignment/stores            采集归属店铺
POST   /api/collection-contexts          确认采集批次
POST   /api/collect/from-extension       老插件采集入库
# 选品池存量迁移：python scripts/migrate_research_pool.py
```