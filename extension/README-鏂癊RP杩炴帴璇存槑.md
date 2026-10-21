# OZON ERP 采集插件（正式版）

由老 ERP（OZON精铺助手）采集插件 v1.3.1 改造，**已彻底断联老 ERP，唯一数据链路为本地新 ERP**。老 ERP 自 2026-09-06 起降级为测试款、不再维护。本目录（`ozon-erp/extension`）为工程内正式版；同级 `ozon-erp-extension-连接新ERP` 为同内容副本。

## v1.4.0 新增：商品图片采集

采集面板 SKU 列表下方新增「商品图片」网格区：
- 自动列出页面主图/规格图/详情图候选（缩略图网格，来源角标标注）
- 点选图片（首次打开默认选中全部主图），支持 全选 / 仅主图 / 清空
- 点击「🖼 采集图片到 ERP 图库」→ 由 **ERP 服务端下载**（绕开 1688/淘宝 CDN 防盗链与页面 CSP），落库到 ERP「我的图库」，标签自动带商品标题
- 后端端点：`POST /api/collect/images`（最多 20 张/次，单张 ≤8MB，超时 120s）

## 断联清单（相对老插件）

| 项 | 状态 |
|---|---|
| `config.js` API 地址 | ✅ 指向 `http://localhost:5000`（老云服务器 `103.40.14.172:47165` 已移除） |
| `manifest.json` host_permissions | ✅ 仅保留 1688/淘宝/天猫 + localhost:5000，云服务器与 8877/8878 端口权限全部删除 |
| 离线兜底端口 8877（老桌面软件） | ✅ 改为 5000，service 标识改为 `ozon-erp` |
| 全部界面/通知/日志文案 | ✅ 「精铺助手」「桌面软件」统一改为「OZON ERP / 本地 ERP」 |
| 版本号 | 1.4.0，插件名「OZON ERP 采集插件 - 1688/淘宝」，与老插件可区分共存 |

## 后端配套

新 ERP 已新增兼容端点（`backend/routes/compat_routes.py`），响应结构对齐老 FastAPI 工作台：
- `GET  /api/config` — 在线状态 / 图片过滤参数
- `GET  /api/auth/me` — 登录状态
- `GET  /api/assignment/stores` — 采集归属店铺列表
- `POST /api/collection-contexts` — 确认采集归属批次
- `POST /api/collect/from-extension` — 采集入库（字段自动映射到新 ERP 采集箱，同链接幂等合并）

## 安装步骤

1. 先启动新 ERP（`start.bat`，确认 http://localhost:5000 可访问）
2. Chrome 打开 `chrome://extensions` → 开发者模式 → 加载已解压的扩展程序 → 选择本文件夹
3. 若装过老插件，建议先禁用老插件（两者数据链路不同，避免混淆）
4. 打开 1688 / 淘宝 / 天猫商品页 → 页面浮动条「一键采集」→ 数据进入新 ERP 采集箱

## 注意事项

- 如果之前装过老插件且改过 API 地址（chrome.storage 里的 `desktop_api_url`），新插件 popup 顶部的 API 地址会沿用旧值——手动改成 `http://localhost:5000` 即可
- 采集归属店铺在插件 popup 中选择，对应新 ERP「店铺管理」里添加的店铺
- 老 ERP 的「操作员/团队」概念未迁移，新 ERP 本地单用户，采集人默认「涂总」
