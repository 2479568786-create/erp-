/**
 * 中控大屏 - Dashboard Control Page
 * 从老 ERP 迁移改造：营业额 / 各店铺营收条 / 今日取消退货 / 财务大类 chip / 上架人员业绩榜
 * 数据源：GET /api/dashboard/control（订单聚合 + Ozon 交易明细逐笔同步结果）
 */
let controlState = { days: 30, data: null, syncing: false };

function renderControlPage() {
  return `
    <div style="animation: pageEnter 0.35s ease;">
      <div class="online-page-header">
        <div>
          <h2 class="online-page-title">中控大屏</h2>
          <p class="online-page-desc">全店营收、财务扣费与上架人员业绩总览（财务数据来自 Ozon 交易明细逐笔同步）</p>
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
          <select class="form-select" style="width:auto;height:34px;font-size:13px;"
                  onchange="controlState.days=Number(this.value);loadControlDashboard()">
            <option value="7">近 7 天</option>
            <option value="30" selected>近 30 天</option>
            <option value="90">近 90 天</option>
          </select>
          <button class="btn btn-xs btn-primary" id="finSyncBtn" onclick="triggerFinanceSync()">
            <i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 同步财务明细
          </button>
        </div>
      </div>

      <div id="controlBody">
        <div class="card" style="text-align:center;padding:60px;color:var(--text-tertiary);">加载中...</div>
      </div>
    </div>
  `;
}

async function initControlPage() {
  await loadControlDashboard();
}

async function loadControlDashboard() {
  const body = document.getElementById('controlBody');
  if (!body) return;
  body.innerHTML = '<div class="card" style="text-align:center;padding:60px;color:var(--text-tertiary);">加载中...</div>';
  try {
    const resp = await API.get('/api/dashboard/control', { days: controlState.days });
    controlState.data = (resp && resp.data) || null;
    renderControlBody();
  } catch (e) {
    body.innerHTML = `<div class="card" style="text-align:center;padding:40px;color: #f87171;">加载失败: ${e.message || e}</div>`;
  }
}

async function triggerFinanceSync() {
  if (controlState.syncing) return;
  controlState.syncing = true;
  const btn = document.getElementById('finSyncBtn');
  if (btn) { btn.disabled = true; btn.innerHTML = '同步中...'; }
  try {
    const resp = await API.post('/api/finance/sync', {});
    window.Toast && Toast.show(resp.msg || '财务明细同步完成', 'success');
  } catch (e) {
    window.Toast && Toast.show('同步失败: ' + (e.message || e), 'error');
  } finally {
    controlState.syncing = false;
    if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 同步财务明细'; lucideRefresh(); }
    await loadControlDashboard();
  }
}

function fmtMoney(v) {
  const n = Number(v || 0);
  return '₽' + n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function lucideRefresh() { if (window.lucide) lucide.createIcons(); }

function renderControlBody() {
  const body = document.getElementById('controlBody');
  const d = controlState.data;
  if (!d) return;

  const maxRev = Math.max(1, ...(d.store_revenue || []).map(s => s.revenue));
  const finChips = (d.finance && d.finance.summary) || [];
  const recent = (d.finance && d.finance.recent) || [];
  const pubs = d.publisher_rank || [];
  const syncs = d.sync_state || [];

  body.innerHTML = `
    <!-- 顶部统计卡 -->
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:14px;">
      <div class="card" style="padding:16px;">
        <div style="font-size:12px;color:var(--text-secondary);">营业额（已付款·近${d.period_days}天）</div>
        <div style="font-size:22px;font-weight:700;color:var(--primary,#465fff);margin-top:4px;">${fmtMoney(d.total_revenue)}</div>
        <div style="font-size:12px;color:var(--text-tertiary);margin-top:2px;">${d.total_orders} 单</div>
      </div>
      <div class="card" style="padding:16px;">
        <div style="font-size:12px;color:var(--text-secondary);">财务净额（近${d.period_days}天交易明细）</div>
        <div style="font-size:22px;font-weight:700;margin-top:4px;color:${d.finance.net_amount >= 0 ? '#067647' : '#d92d20'};">${fmtMoney(d.finance.net_amount)}</div>
        <div style="font-size:12px;color:var(--text-tertiary);margin-top:2px;">正=入账 负=扣费</div>
      </div>
      <div class="card" style="padding:16px;">
        <div style="font-size:12px;color:var(--text-secondary);">今日取消</div>
        <div style="font-size:22px;font-weight:700;color: #f87171;margin-top:4px;">${d.today.cancelled}</div>
        <div style="font-size:12px;color:var(--text-tertiary);margin-top:2px;">累计 ${d.today.total_cancelled_all} 单</div>
      </div>
      <div class="card" style="padding:16px;">
        <div style="font-size:12px;color:var(--text-secondary);">今日退货/仲裁</div>
        <div style="font-size:22px;font-weight:700;color: #fbbf24;margin-top:4px;">${d.today.returned}</div>
        <div style="font-size:12px;color:var(--text-tertiary);margin-top:2px;">累计 ${d.today.total_returned_all} 单</div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px;">
      <!-- 各店铺营收条 -->
      <div class="card" style="padding:16px;">
        <div style="font-weight:600;margin-bottom:12px;"><i data-lucide="store" style="width:16px;height:16px;vertical-align:-3px;"></i> 各店铺营收（近${d.period_days}天）</div>
        ${(d.store_revenue || []).length === 0
          ? '<div style="color:var(--text-tertiary);font-size:13px;padding:20px 0;text-align:center;">暂无已付款订单数据（先在订单页同步订单）</div>'
          : (d.store_revenue || []).map(s => `
            <div style="margin-bottom:10px;">
              <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px;">
                <span>${s.alias || s.store_id}</span>
                <span style="font-weight:600;">${fmtMoney(s.revenue)} · ${s.orders}单</span>
              </div>
              <div style="height:8px;background: #1c2434;border-radius:4px;overflow:hidden;">
                <div style="height:100%;width:${Math.round(s.revenue / maxRev * 100)}%;background:linear-gradient(90deg,#465fff,#7c3aed);border-radius:4px;"></div>
              </div>
            </div>`).join('')}
      </div>

      <!-- 上架人员业绩榜 -->
      <div class="card" style="padding:16px;">
        <div style="font-weight:600;margin-bottom:12px;"><i data-lucide="trophy" style="width:16px;height:16px;vertical-align:-3px;"></i> 上架人员业绩榜（近${d.period_days}天）</div>
        ${pubs.length === 0
          ? '<div style="color:var(--text-tertiary);font-size:13px;padding:20px 0;text-align:center;">暂无上架记录</div>'
          : pubs.map((p, i) => `
            <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border,#f0f0f0);">
              <span style="width:24px;height:24px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;${i < 3 ? 'background:#fef0c7;color: #fbbf24;' : 'background: #1c2434;color: #c2c9d6;'}">${i + 1}</span>
              <div style="flex:1;">
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                  <span style="font-size:14px;font-weight:600;">${p.publisher}</span>
                  <span style="font-size:12px;font-weight:700;color:${p.net_profit >= 0 ? '#4ade80' : '#f87171'};">净业绩 ${fmtMoney(p.net_profit)}</span>
                </div>
                <div style="font-size:12px;color:var(--text-tertiary);">预估利润 ${fmtMoney(p.est_profit)} − 退货损失 ${fmtMoney(p.loss_total)}</div>
                <div style="font-size:12px;color:var(--text-tertiary);">成功 ${p.published} / 共 ${p.total} 件 · 成功率 ${p.success_rate}%${p.stores && p.stores.length ? ' · ' + p.stores.join('/') : ''}</div>
              </div>
              ${p.failed > 0 ? `<span style="font-size:12px;color: #f87171;">失败 ${p.failed}</span>` : ''}
            </div>`).join('')}
      </div>
    </div>

    <!-- 财务 chip + 最近交易 -->
    <div class="card" style="padding:16px;margin-bottom:14px;">
      <div style="font-weight:600;margin-bottom:12px;"><i data-lucide="receipt" style="width:16px;height:16px;vertical-align:-3px;"></i> 财务大类聚合（近${d.period_days}天）</div>
      ${finChips.length === 0
        ? '<div style="color:var(--text-tertiary);font-size:13px;padding:16px 0;text-align:center;">暂无财务明细 — 点右上角「同步财务明细」拉取 Ozon 交易流水</div>'
        : `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;">
            ${finChips.map(c => `
              <span style="display:inline-flex;gap:6px;align-items:center;padding:6px 12px;border-radius:16px;font-size:13px;background:${c.amount >= 0 ? '#ecfdf3' : '#fef3f2'};color:${c.amount >= 0 ? '#067647' : '#b42318'};border:1px solid ${c.amount >= 0 ? '#a6f4c5' : '#fecdca'};">
                ${c.label} <b>${fmtMoney(c.amount)}</b> <span style="opacity:.7;">(${c.count}笔)</span>
              </span>`).join('')}
          </div>
          <div style="font-size:13px;color:var(--text-secondary);margin-bottom:6px;">最近交易（30 笔）</div>
          <div style="max-height:260px;overflow:auto;">
            <table class="data-table" style="width:100%;font-size:12px;">
              <thead><tr><th>时间</th><th>店铺</th><th>大类</th><th>类型</th><th>发货单</th><th style="text-align:right;">金额</th></tr></thead>
              <tbody>
                ${recent.map(t => `
                  <tr>
                    <td>${(t.date || '').slice(0, 16).replace('T', ' ')}</td>
                    <td>${t.alias}</td>
                    <td>${t.parent_label}</td>
                    <td style="color:var(--text-tertiary);">${t.operation_type_name || t.operation_type}</td>
                    <td style="color:var(--text-tertiary);">${t.posting_number || '-'}</td>
                    <td style="text-align:right;font-weight:600;color:${t.amount >= 0 ? '#067647' : '#b42318'};">${fmtMoney(t.amount)}</td>
                  </tr>`).join('')}
              </tbody>
            </table>
          </div>`}
    </div>

    <!-- 财务同步水位 -->
    <div class="card" style="padding:16px;">
      <div style="font-weight:600;margin-bottom:12px;"><i data-lucide="database-zap" style="width:16px;height:16px;vertical-align:-3px;"></i> 各店财务同步水位</div>
      ${syncs.length === 0
        ? '<div style="color:var(--text-tertiary);font-size:13px;">尚未同步过财务明细</div>'
        : `<div style="display:flex;flex-wrap:wrap;gap:8px;">
            ${syncs.map(s => `
              <span style="padding:6px 12px;border-radius:8px;font-size:12px;background: #17191d;border:1px solid var(--border,#eee);">
                <b>${s.alias}</b> 至 ${s.last_to || '-'} · ${s.transaction_count} 笔 · ${s.synced_at || ''}
              </span>`).join('')}
          </div>`}
    </div>
  `;
  lucideRefresh();
}

// 路由自注册
Router.register('/dashboard-control', (route) => {
  const html = renderControlPage();
  setTimeout(() => initControlPage(), 50);
  return html;
});
