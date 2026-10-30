"""发布队列健康检查（2026-10-30 OSS 卡死事故后新增）。

GET /api/health/publish（仅管理员 JWT）返回队列堆积与图片线程池状态，
供外部 cron 判活：pending 超过 180s 或 processing 超过 300s 告警/重启。
"""
from datetime import datetime, timezone

from flask import Blueprint

from models.product import PublishTask
from services import publish_service
from utils.auth import require_admin
from utils.response import success_response

publish_health_bp = Blueprint('publish_health', __name__)

PENDING_ALERT_SEC = 180
PROCESSING_ALERT_SEC = 300


def _parse_ts(value):
    """解析任务时间戳（ISO 字符串或 epoch 秒），失败返回 None。"""
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip().replace('Z', '+00:00')
    for parser in (datetime.fromisoformat,):
        try:
            dt = parser(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except Exception:
        return None


def _age_seconds(now, task):
    """任务「停留时长」：优先 updatedAt，其次 createdAt。"""
    for key in ('updatedAt', 'updated_at', 'createdAt', 'created_at'):
        dt = _parse_ts(task.get(key))
        if dt:
            return max(0, int((now - dt).total_seconds()))
    return 0


@publish_health_bp.route('/health/publish', methods=['GET'])
@require_admin
def publish_queue_health():
    now = datetime.now(timezone.utc)
    tasks = PublishTask.find_all() or []
    pending = [t for t in tasks if str(t.get('status') or '') == 'pending']
    processing = [t for t in tasks if str(t.get('status') or '') == 'processing']

    pending_age = max([_age_seconds(now, t) for t in pending], default=0)
    processing_age = max([_age_seconds(now, t) for t in processing], default=0)
    stuck = []
    if pending and pending_age > PENDING_ALERT_SEC:
        stuck.append('pending 最久已 %ds（阈值 %ds）' % (pending_age, PENDING_ALERT_SEC))
    if processing and processing_age > PROCESSING_ALERT_SEC:
        stuck.append('processing 最久已 %ds（阈值 %ds）' % (processing_age, PROCESSING_ALERT_SEC))

    executor = publish_service.image_executor_stats()
    return success_response(data={
        'pending': len(pending),
        'processing': len(processing),
        'oldest_pending_age_sec': pending_age,
        'oldest_processing_age_sec': processing_age,
        'image_executor_threads': executor['threads_alive'],
        'image_executor_max_workers': executor['max_workers'],
        'image_process_timeout_sec': executor['timeout_sec'],
        'thresholds': {'pending_sec': PENDING_ALERT_SEC, 'processing_sec': PROCESSING_ALERT_SEC},
        'ok': not stuck,
        'problems': stuck,
        'checked_at': now.isoformat(),
    }, msg='发布队列正常' if not stuck else '发布队列堆积')
