/** 公共通知工作区：管理员发布，所有登录账号接收，业务员只读。 */
function _publicNoticeEscape(value) {
  return String(value || '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}
function _publicNoticeTime(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}
function _publicNoticePriority(priority) { return { urgent: '紧急', important: '重要', normal: '普通' }[priority] || '普通'; }

function renderPublicNotificationsPage() {
  const isAdmin = Store.getUser()?.role === 'admin';
  return `<div style="max-width:1100px;margin:0 auto;">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:22px;">
      <div><div style="font-size:12px;color:#64748b;margin-bottom:6px;">工作区 / 公共通知</div><h1 style="margin:0;font-size:28px;color:#0f172a;">公共通知</h1><p style="margin:8px 0 0;color:#64748b;font-size:14px;">管理员发布的业务信息会同步给所有已登录账号。</p></div>
      <span id="publicNoticeUnread" style="display:inline-flex;padding:7px 12px;border-radius:999px;background:#eff6ff;color:#2563eb;font-size:13px;">未读 0</span>
    </div>
    ${isAdmin ? `<section style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:18px;margin-bottom:18px;">
      <div style="font-weight:700;color:#0f172a;margin-bottom:14px;">发布通知</div><form id="publicNoticeForm">
        <div style="display:grid;grid-template-columns:minmax(0,1fr) 150px;gap:12px;margin-bottom:12px;"><input id="publicNoticeTitle" maxlength="120" required placeholder="通知标题" style="height:40px;border:1px solid #cbd5e1;border-radius:8px;padding:0 12px;font-size:14px;"><select id="publicNoticePriority" style="height:40px;border:1px solid #cbd5e1;border-radius:8px;padding:0 10px;font-size:14px;background:#fff;"><option value="normal">普通</option><option value="important">重要</option><option value="urgent">紧急</option></select></div>
        <textarea id="publicNoticeContent" maxlength="5000" required rows="4" placeholder="请输入要推送给业务员和其他工作人员的内容" style="width:100%;box-sizing:border-box;border:1px solid #cbd5e1;border-radius:8px;padding:10px 12px;font-size:14px;resize:vertical;"></textarea><div style="display:flex;justify-content:flex-end;margin-top:12px;"><button type="submit" class="btn btn-primary">发布通知</button></div>
      </form></section>` : ''}
    <section id="publicNoticeList" style="display:grid;gap:12px;"><div style="padding:36px;text-align:center;color:#94a3b8;background:#fff;border:1px solid #e2e8f0;border-radius:12px;">正在加载通知…</div></section>
  </div>`;
}

function _renderPublicNoticeList(items, unreadCount) {
  const list = document.getElementById('publicNoticeList');
  const badge = document.getElementById('publicNoticeUnread');
  if (badge) badge.textContent = `未读 ${unreadCount || 0}`;
  if (!list) return;
  if (!items.length) { list.innerHTML = '<div style="padding:42px;text-align:center;color:#94a3b8;background:#fff;border:1px solid #e2e8f0;border-radius:12px;">暂无公共通知</div>'; return; }
  list.innerHTML = items.map((item) => {
    const color = item.priority === 'urgent' ? '#dc2626' : item.priority === 'important' ? '#d97706' : '#2563eb';
    return `<article style="background:#fff;border:1px solid ${item.is_read ? '#e2e8f0' : color};border-left:4px solid ${color};border-radius:10px;padding:16px 18px;"><div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;"><div><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;"><strong style="font-size:16px;color:#0f172a;">${_publicNoticeEscape(item.title)}</strong><span style="font-size:11px;color:${color};background:${color}18;padding:3px 7px;border-radius:999px;">${_publicNoticePriority(item.priority)}</span>${item.is_read ? '' : '<span style="font-size:11px;color:#2563eb;">未读</span>'}</div><div style="font-size:12px;color:#94a3b8;margin-top:6px;">${_publicNoticeEscape(item.author_name)} · ${_publicNoticeEscape(_publicNoticeTime(item.published_at))}</div></div>${item.is_read ? '' : `<button class="btn btn-xs" data-public-notice-read="${item.id}">标记已读</button>`}</div><div style="white-space:pre-wrap;line-height:1.7;color:#334155;font-size:14px;margin-top:12px;">${_publicNoticeEscape(item.content)}</div></article>`;
  }).join('');
  list.querySelectorAll('[data-public-notice-read]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    const result = await Api.markPublicNotificationRead(button.dataset.publicNoticeRead);
    if (result?.code === 200) loadPublicNotifications(); else { button.disabled = false; Toast.show(result?.msg || '标记失败', 'error'); }
  }));
}

async function loadPublicNotifications() {
  const result = await Api.getPublicNotifications({ pageSize: 50 });
  if (result?.code !== 200 || !result.data) { Toast.show(result?.msg || '通知加载失败', 'error'); return; }
  _renderPublicNoticeList(result.data.list || [], result.data.unreadCount || 0);
}
async function submitPublicNotification(event) {
  event.preventDefault();
  const result = await Api.createPublicNotification({ title: document.getElementById('publicNoticeTitle')?.value.trim(), content: document.getElementById('publicNoticeContent')?.value.trim(), priority: document.getElementById('publicNoticePriority')?.value });
  if (result?.code !== 200) { Toast.show(result?.msg || '发布失败', 'error'); return; }
  document.getElementById('publicNoticeForm').reset(); Toast.show('通知已发布，业务员登录后即可查看', 'success'); loadPublicNotifications();
}
function initPublicNotificationsPage() { document.getElementById('publicNoticeForm')?.addEventListener('submit', submitPublicNotification); loadPublicNotifications(); }
Router.register('/public-notifications', () => { const html = renderPublicNotificationsPage(); setTimeout(initPublicNotificationsPage, 0); return html; });
