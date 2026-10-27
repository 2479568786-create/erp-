function renderJlcWorkspacePage() {
  const page = document.createElement('div');
  page.style.cssText = 'height:calc(100vh - 120px);min-height:620px;';
  page.innerHTML = '<div style="padding:24px;color:#64748b;">正在安全连接 JLC 工作台…</div>';
  fetch('/api/jlc-workspace/session', {
    method: 'POST',
    headers: { Authorization: `Bearer ${Store.getState('token')}` },
  }).then((response) => {
    if (!response.ok) throw new Error(response.status === 403 ? '当前账号没有访问权限。' : '工作台授权失败，请重新登录后重试。');
    page.innerHTML = '<iframe title="JLC 上架研究工作台" src="/jlc-workspace/command-center?v=2026-08-01-ui-state-v1" style="width:100%;height:100%;border:0;border-radius:8px;background:#fff;"></iframe>';
  }).catch((error) => {
    page.innerHTML = `<div class="empty-state"><p>${error.message}</p></div>`;
  });
  return page;
}

Router.register('/jlc-workspace', renderJlcWorkspacePage);
