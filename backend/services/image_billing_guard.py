"""生图计费守卫（应用层，与具体路由实现解耦）。

为什么放在应用层
----------------
2026-10-28 生图链路被重构（新增 `services/jlc_media_service.py` + `/api/ai/suite/jlc`），
原写在 `routes/gallery_routes.py` 里的计费闸门和扣点钩子被整份覆盖，导致 AI 生图一度
「出了图但不扣点」。放到 before/after_request 上以后，路由怎么改都不会把计费弄丢。

行为
----
- 生成前：按「本次张数 × 单价」校验 `accounts.image_enabled` 与余额，不足直接 403，
  不调用凌科 AI（不花第三方钱）。
- 生成后：按响应里 `data.assets` 的**实际成功张数**扣点，写 `image_usage` 明细。
- admin 不受余额/开关限制（`image_billing_service.can_generate` 内处理），但仍记流水。
- 任何异常都不影响生图响应本身，只在服务日志打一行，避免计费把出图搞挂。
"""
from flask import g, request

from services import image_billing_service as billing
from utils.auth import get_current_user
from utils.response import error_response

# 需要计费的生图端点：路径 -> 本次请求张数（None 表示按响应里的实际张数判定）
IMAGE_ENDPOINTS = {
    '/api/ai/images/generate': 'count',   # 单张生成（1~4 张）
    '/api/ai/suite/jlc': 'single',        # 套图，每个模块一次 1 张
}

_CHARGED_FLAG = '_image_billing_charged'
DEFAULT_IMAGE_MODEL = 'banana-2.1'


def _requested_model():
    """本次请求选的生图模型（前端下拉传 model），缺省用 banana-2.1。"""
    try:
        body = request.get_json(silent=True) or {}
        model = str(body.get('model') or '').strip()
    except Exception:
        model = ''
    return model or DEFAULT_IMAGE_MODEL


def _response_model(response):
    try:
        payload = response.get_json(silent=True) or {}
        return str((payload.get('data') or {}).get('model') or '').strip()
    except Exception:
        return ''


def _requested_count(rule):
    if rule == 'single':
        return 1
    body = request.get_json(silent=True) or {}
    try:
        return max(1, min(int(body.get('count') or 1), 4))
    except (TypeError, ValueError):
        return 1


def _assets_in(response):
    try:
        payload = response.get_json(silent=True) or {}
        assets = (payload.get('data') or {}).get('assets')
        return assets if isinstance(assets, list) else []
    except Exception:
        return []


def register_image_billing_guard(app):
    """在 Flask app 上注册生图计费守卫（幂等：重复注册会被忽略）。"""

    if getattr(app, '_image_billing_guard_registered', False):
        return

    @app.before_request
    def _image_billing_gate():
        rule = IMAGE_ENDPOINTS.get(request.path)
        if request.method != 'POST' or not rule:
            return None
        try:
            user = get_current_user()
            model = _requested_model()
            allowed, reason = billing.can_generate(user, images=_requested_count(rule), model=model)
        except Exception as exc:  # 计费不可用时不阻断出图
            print(f'[生图计费] 预检异常，本次放行：{exc}')
            return None
        if not allowed:
            return error_response(reason, 403)
        try:
            g.user_id_for_billing = (user or {}).get('id')
            g.image_model_for_billing = model
        except Exception:
            pass
        return None

    @app.after_request
    def _image_billing_charge(response):
        if request.method != 'POST' or request.path not in IMAGE_ENDPOINTS:
            return response
        if response.status_code != 200:
            return response
        if getattr(g, _CHARGED_FLAG, False):
            return response
        user_id = getattr(g, 'user_id_for_billing', None)
        if not user_id:  # 预检异常放行时不在事后补扣，避免误扣
            return response
        try:
            assets = _assets_in(response)
            if not assets:
                return response
            model = (_response_model(response) or getattr(g, 'image_model_for_billing', '')
                     or DEFAULT_IMAGE_MODEL)
            info = billing.charge(user_id, len(assets), provider='lingkeai',
                                  model=model, source='ai')
            setattr(g, _CHARGED_FLAG, True)
            print('[生图计费] 账号 %s 出图 %d 张（%s），扣 %s 点，余 %s 点'
                  % (user_id, len(assets), model, info.get('cost_points'),
                     info.get('balance_after')))
        except Exception as exc:
            print(f'[生图计费] 扣点异常：{exc}')
        return response

    app._image_billing_guard_registered = True
