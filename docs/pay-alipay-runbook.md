# 支付宝充值 Runbook（A4）

> 版本 v1 · 2026-11-06 · 维护：ERP改造
> 配套方案：`ERP改造\specs\alipay-adapter-plan_v1.md`；代码在 `backend/services/alipay_*.py`、`pay_reconcile.py`、`routes/pay_routes.py`。
> 当前状态：A1 签名 / A2 回调 / A3 渠道状态 / A4 对账补偿 + 退款占位表 **已写完并在分支验证**；**均未部署**（`ALIPAY_ENABLED=0`）。A5 预下单待沙箱凭证。

## 1. 一句话口径

- 1 点 = 1 元 = 100 分；档位来自 `PAY_TIERS`（未配则回退 `WECHATPAY_TIERS`）。
- 订单表 `pay_orders`（`channel` 区分 `wxpay`/`alipay`），事件表 `pay_events`，退款表 `pay_refunds`（占位）。
- 状态机：`created → paid → credited`；`created → closed`。入账幂等靠 `credit_order` 的 `WHERE status='paid'`。
- 主通道由 `PAY_PRIMARY_CHANNEL` 决定（默认 `alipay`），`/api/pay/status` 会给出 `active_channel`（真正能用的那个）。

## 2. 环境变量（只放路径与开关）

```ini
PAY_PRIMARY_CHANNEL=alipay
PAY_TIERS=10,30,50,100,200
ALIPAY_ENABLED=1                     # 上线才置 1，先留 0
ALIPAY_APPID=                        # 开放平台应用 AppID
ALIPAY_PRIVATE_KEY_PATH=/etc/geekozon-erp-pay/alipay/app_private_key.pem   # PKCS#8
ALIPAY_PUBLIC_KEY_PATH=/etc/geekozon-erp-pay/alipay/alipay_public_key.pem  # 支付宝公钥（不是应用公钥）
ALIPAY_NOTIFY_URL=https://<your-domain>/api/pay/alipay/notify
ALIPAY_GATEWAY=https://openapi.alipay.com/gateway.do                       # 沙箱换成 openapi.alipaydev.com
```
改动 env 后：`systemctl restart geekozon-erp`（`kill -HUP` 不会重读 `EnvironmentFile`）。
凭证落位：`/etc/geekozon-erp-pay/alipay/`，`chmod 600 geekozon:geekozon`，sftp 上传，**不进 git、不贴群、不回显**。

## 3. 日常检查（只读）

```bash
# 渠道状态（管理员：能看到 missing 明细与 notify_url）
curl -s -H "Authorization: Bearer $JWT" http://127.0.0.1:<port>/api/pay/status | python -m json.tool

# 最近事件（只读库）
sqlite3 /srv/<app>/backend/data/geekozon.db \
  "SELECT created_at, out_trade_no, event, substr(detail,1,80) FROM pay_events ORDER BY id DESC LIMIT 20;"

# 已支付未入账（应长期为空）
sqlite3 …/geekozon.db "SELECT out_trade_no, amount_cents, points, status FROM pay_orders WHERE status='paid';"

# 建单超时仍未付（对账脚本会查单）
sqlite3 …/geekozon.db "SELECT out_trade_no, channel, created_at FROM pay_orders WHERE status='created' ORDER BY id DESC LIMIT 20;"
```

## 4. 对账补偿（回调丢了也能补）

```bash
cd /srv/<app>/backend
PYTHONPATH=/srv/<app>/backend .venv/bin/python -m services.pay_reconcile
```
输出：`{checked, credited, skipped, failed, credit_retried, queried, details[]}`。

两类补偿：
- **A 类**：`paid` 但未入账（入账时余额服务抖动）→ 直接重试，不查渠道；
- **B 类**：`created` 且超过 120 秒 → `alipay.trade.query` 查单；`TRADE_SUCCESS/TRADE_FINISHED` 且金额严格相等 → `mark_paid`（digest=`recon:<trade_no>`）+ 入账；未付/金额不符/业务错误只记事件。

建议 cron（每 10 分钟，避开整点）：

```cron
*/10 * * * * cd /srv/<app>/backend && PYTHONPATH=/srv/<app>/backend .venv/bin/python -m services.pay_reconcile >> /var/log/<app>-pay-reconcile.log 2>&1
```
**注意**：脚本只处理 `channel='alipay'` 的订单，微信订单不会被它碰。

## 5. 回调处理速查（`POST /api/pay/alipay/notify`）

| 情形 | HTTP | 响应体 | 入库动作 |
|---|---|---|---|
| 验签通过 + 状态成功 + 金额一致 | 200 | `success` | `paid → credited`，加点一次 |
| 重复回调（同原文） | 200 | `success` | 只记 `alipay_notify_duplicate` |
| 验签失败 / 缺 sign | 401 | `fail` | `alipay_notify_bad_sig` |
| `app_id` / `seller_id` 不符 | 403 | `fail` | `alipay_notify_bad_appid` / `bad_seller` |
| 金额不一致 | 200 | `success` | `alipay_notify_bad_amount`，**不入账** |
| 未知订单号 | 200 | `success` | `alipay_notify_unknown_order`（停止重试） |
| 公钥缺失/损坏 | 503 | `fail` | `alipay_notify_verify_error`（**配置故障，不是被攻击**） |

**应答必须是纯文本 `success`**：返回 JSON 会被支付宝判失败并重试 25 小时。

## 5.5 预下单（A5，当面付 `alipay.trade.precreate`）

```
POST /api/pay/orders   {"points": 10, "channel": "alipay"}
→ 200 {"out_trade_no":"RC…","code_url":"https://qr.alipay.com/…","channel":"alipay",
       "channel_label":"支付宝扫码","points":10,"amount_cents":1000,"expires_in":900}
```
- 不传 `channel` 时用主通道（`PAY_PRIMARY_CHANNEL`）；传了不认识的值直接 **400**；
- biz_content：`out_trade_no` / `total_amount`（**元，两位小数**）/ `subject`（截 256）/ `timeout_express=15m`（与本地 TTL 一致）；
- 错误映射：渠道未启用或缺配置 → **503**；支付宝业务码非 10000 → **502**（带 `code/sub_code`）；网关不可达 → **502**；私钥读不到 → **503**；
- **任何下单失败都会把刚建的本地订单置 `closed`** 并记 `create_fail` 事件，不留 `created` 僵尸单；
- 沙箱联调：`ALIPAY_GATEWAY=https://openapi.alipaydev.com/gateway.do` + 沙箱凭证（`/etc/geekozon-erp-pay/alipay-sandbox/`），沙箱扫码是虚拟钱但**会真加点数**，只能用专用测试账号并在跑完后按 `out_trade_no` 冲销。

## 6. 退款（M3 占位，当前路由仍返回 501）

表已就绪：`pay_refunds(id, out_trade_no, channel, out_request_no UNIQUE, amount_cents, reason, status, trade_no, refund_no, notify_digest, created_at, updated_at)`。

```python
from services import pay_service
r = pay_service.create_refund(out_trade_no, amount_cents=500, reason='多充了')
pay_service.mark_refund(r['out_request_no'], 'succeeded', refund_no='…', trade_no='…')
```
- `out_request_no` 是**我方生成的退款幂等键**（`RFyyyyMMddHHMMSS######`），真实调用 `alipay.trade.refund` 时必须原样带上；
- 校验：订单必须 `paid/credited`，退款金额 > 0 且 ≤ 订单金额，重复幂等键会被唯一索引拒绝。

## 7. 故障排查

| 现象 | 先看什么 | 常见原因 |
|---|---|---|
| 回调一直 401 | `pay_events.event=alipay_notify_bad_sig` | 用的是「应用公钥」而不是「支付宝公钥」；或把表单参数重新 URL 编码后再拼 |
| 回调一直 503 | `alipay_notify_verify_error` | 公钥文件路径错/权限不足（`600 geekozon:geekozon`） |
| 回调 200 但没加点 | `alipay_notify_bad_amount` / `channel_mismatch` / `trade_no_mismatch` | 金额单位错（元/分）、订单 channel 不是 alipay、同单号出现两个 `trade_no` |
| 查单报「响应缺少 sign」 | `recon_query_fail` | 网关返回的是 HTML/错误页，或 `ALIPAY_GATEWAY` 配错 |
| 查单报 InvalidSignature | `recon_query_fail` | 响应节点被重新序列化过（空格/转义差异）；必须签**原始子串** |
| 已付款但订单一直 `created` | `alipay_notify_unknown_order` | 本地订单号与下单时不一致（脏数据），或通知打到了别的环境 |
| 订单 `paid` 但余额没加 | `SELECT * FROM pay_orders WHERE status='paid'` | 入账时余额服务失败；跑一次对账脚本 A 类即可 |

## 8. `pay_events` 事件字典（对账/排障用）

`create` / `create_fail` / `close` / `refund_created` / `refund_requested` /
`credit_ok` / `credit_fail` / `notify_ok` / `notify_duplicate` / `notify_state` / `notify_bad_sig` /
`notify_bad_amount` / `notify_unknown_order` / `notify_channel_mismatch` / `notify_trade_no_mismatch` /
`alipay_notify_ok` / `alipay_notify_bad_sig` / `alipay_notify_bad_appid` / `alipay_notify_bad_seller` /
`alipay_notify_bad_amount` / `alipay_notify_bad_amount_format` / `alipay_notify_duplicate` /
`alipay_notify_state` / `alipay_notify_closed` / `alipay_notify_unknown_order` /
`alipay_notify_channel_mismatch` / `alipay_notify_trade_no_mismatch` / `alipay_notify_verify_error` /
`alipay_notify_mark_paid_dup` / `alipay_notify_too_large` /
`recon_query_fail` / `recon_query_biz_error` / `recon_amount_mismatch` / `recon_bad_amount_format` /
`recon_reconciled` / `recon_credit_fail` / `recon_mark_paid_dup`

## 9. 上线顺序（A5 时执行）

1. 沙箱：凭证落 `/etc/geekozon-erp-pay/alipay-sandbox/`，`ALIPAY_GATEWAY=…alipaydev…`，`ALIPAY_ENABLED=1`，`restart`；
2. 沙箱用**专用测试账号**充 1 次（沙箱虚拟钱也会真加点到该账号），跑完按 `out_trade_no` 冲销；
3. 切生产凭证 + 正式网关 + `ALIPAY_ENABLED=1`，`restart`；
4. 真实 10 元联调：扫码付 → 核对 `pay_orders.status=credited`、`image_ops_log.operator=alipay:<单号>`、余额三处一致；
5. 观察 24h：`pay_events` 无 `bad_*` 堆积、`status='paid'` 长期为 0；
6. 回滚：`ALIPAY_ENABLED=0` + `restart`（订单与已入账点数不受影响），必要时 `PAY_PRIMARY_CHANNEL=wxpay`。
