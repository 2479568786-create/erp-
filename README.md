# GeekOzon ERP 系统

面向 Ozon 电商平台的个人 ERP 工具项目，包含商品采集、AI 商品卡、刊登、订单、财务、图片和选品等工作区代码。

> **给初学者：** 这个仓库主要用于学习和参考，不是开箱即用的生产 SaaS。代码来自一份本地快照，独立 JLC 工作台完整源码不在本仓库；接入真实店铺前，请先阅读安全提醒和避坑指南，并在测试数据上验证。

## 公开获取，不限粉丝

仓库面向所有想学习和参考的朋友开放，不需要关注频道、加入粉丝群或在评论区留言才能访问。视频里说“评论区发 666，我发您”只是互动方式；如果你看到视频时仓库仍为公开状态，可以直接打开 [GitHub 仓库](https://github.com/2479568786-create/erp-) 下载 ZIP，或运行：

```bash
git clone https://github.com/2479568786-create/erp-.git
```

如果你是从视频或他人转发找到这个项目，建议先看下方的新手指南和工作区索引。项目仍在整理中，快照不完整的部分会在文档里注明。

> **关于使用授权：** 公开可见和可下载不等于已授予任意复制、分发或商用许可。请先检查仓库根目录是否有 `LICENSE` 文件；在明确许可证之前，不要默认项目可以商用或重新发布。项目也基于第三方开源框架发展，使用时还需要遵守上游许可证。

## 从哪里开始

- [技术小白上手与避坑指南](docs/技术小白上手与避坑指南.md)：运行项目、拆任务、检查 API/类目/图片/自动化和公开仓库安全。
- [开发历程与踩坑复盘](docs/开发历程与踩坑复盘.md)：项目为什么开始、为什么换框架、哪些问题曾经卡住，以及普通卖家如何用 AI 降低成本。
- [工作区代码索引](docs/WORKSPACE_MAP.md)：按财务、采集、定价、在线商品、订单等工作区查找前后端文件，并注明当前缺失的独立 JLC 工作台。

## 系统架构

```
geekozon-erp/
├── frontend/          # 前端页面 (HTML5/CSS3/原生JS)
├── backend/           # Python Flask 后端服务
├── extension/         # Chrome 浏览器扩展 (Manifest V3)
└── README.md          # 项目说明文档
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | HTML5 + CSS3 + 原生 JavaScript (ES2020+) |
| 后端 | Python Flask + SQLite (WAL 模式) |
| 扩展 | Chrome Extension Manifest V3 |
| AI | DeepSeek / 通义千问 (OpenAI 兼容接口) |
| 存储 | 本地文件 / 七牛云对象存储（可选） |

## 快速启动

### 1. 启动后端服务

```bash
cd backend
pip install flask flask-cors requests
python app.py
```

> **安全提示**：首次部署请编辑 `backend/.env`，配置 `SECRET_KEY`（生成命令：`python -c "import secrets; print(secrets.token_hex(32))"`）。生产环境保持 `FLASK_DEBUG=0` 并按需收敛 `CORS_ALLOWED_ORIGINS`。

服务地址：`http://localhost:5000`

### 2. 打开前端页面

直接在浏览器打开 `frontend/index.html`，或使用 VS Code Live Server 插件。

### 3. 加载浏览器扩展

1. 打开 Chrome，访问 `chrome://extensions/`
2. 开启右上角「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择本项目 `extension/` 目录

## 功能模块

- **首页仪表盘**：欢迎页、功能卡片快捷入口、系统公告
- **商品采集**：通过浏览器扩展或 URL 批量采集 Ozon 商品
- **AI 撰写**：智能生成标题、描述优化、关键词推荐
- **上架管理**：批量发布到 Ozon、状态同步追踪
- **选品分析**：数据分析与市场洞察
- **浏览器扩展**：页面数据抓取、一键采集、快速刊登

## 接口说明

后端 RESTful API 基础地址：`http://localhost:5000/api`。路由按功能拆分为多个 Flask Blueprint，实际入口见 `backend/routes/__init__.py`。

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/notices` | 获取公告列表 |
| GET | `/api/products` | 获取商品列表（分页/筛选/搜索） |
| POST | `/api/products` | 新增商品采集 |
| POST | `/api/publish` | 提交发布任务（异步） |
| GET | `/api/publish/tasks` | 查询发布任务状态 |
| POST | `/api/categories/ai-match` | AI 类目匹配 |
| GET | `/api/stores` | 店铺管理 |
| POST | `/api/auth/login` | 账户登录 |
| GET | `/api/ai/models` | AI 模型配置管理 |
| GET | `/api/health` | 健康检查 |

启动和安全注意事项请先阅读 [技术小白上手与避坑指南](docs/技术小白上手与避坑指南.md)。连接 Ozon 的接口字段和规则可能变化，开发时请以 Ozon 当时的官方文档为准。
