/**
 * 财务中心（简版）- Finance Center Page
 * 替代老 ERP 财务板块：合计 / 按大类 / 按店铺 / 交易明细分页
 * 数据源：GET /api/finance/center、GET /api/finance/transactions、POST /api/finance/sync
 */
let financeState = {
  days: 30, storeId: '', parentType: '',
  page: 1, pageSize: 50, total: 0, center: null, loading: false,
};

function renderFinancePage() {
  return `
    <div style="animation: pageEnter 0.35s ease;">
      <div class="online-page-header">
        <div>
          <h2 class="online-page-title">财务中心</h2>
          <p class="online-page-desc">Ozon 财务交易明细逐笔台账（佣金 / 服务费 / 物流 / 退货扣款），点「同步财务明细」从 Ozon 拉取</p>
        </div>
        <button class="btn btn-xs btn-primary" id="finCenterSyncBtn" onclick="financeSyncAll()">
          <i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 同步财务明细
        </button>
      </div>

      <div class="batch-action-bar">
        <div class="batch-action-left">
          <select id="finStoreSelect" class="form-select" style="width:auto;height:34px;font-size:13px;"
                  onchange="financeState.storeId=this.value;financeState.page=1;loadFinanceCenter()">
            <option value="">全部店铺</option>
          </select>
          <select class="form-select" style="width:auto;height:34px;font-size:13px;margin-left:8px;"
                  onchange="financeState.days=Number(this.value);financeState.page=1;loadFinanceCenter()">
            <option value="7">近 7 天</option>
            <option value="30" selected>近 30 天</option>
            <option value="90">近 90 天</option>
            <option value="365">近 1 年</option>
          </select>
        </div>
      </div>

      <div id="finSummary"></div>

      <div class="card" style="padding:16px;margin-top:12px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <div style="font-weight:600;">交易明细</div>
          <select class="form-select" style="width:auto;height:30px;font-size:12px;"
                  onchange="financeState.parentType=this.value;financeState.page=1;loadFinTransactions()">
            <option value="">全部大类</option>
            <option value="Marketplace">平台结算</option>
            <option value="Payout">打款</option>
            <option value="Refund">退款</option>
            <option value="Chargeback">拒付</option>
            <option value="Service">服务费</option>
            <option value="Compensation">赔付</option>
            <option value="LogisticServices">物流服务</option>
            <option value="Promo">推广</option>
          </select>
        </div>
        <div id="finTxnBody"><div style="text-align:center;padding:30px;color:var(--text-tertiary);">加载中...</div></div>
      </div>
    </div>
  `;
}

async function initFinancePage() {
  try {
    const resp = await API.get('/api/stores');
    const list = (resp && resp.data && (Array.isArray(resp.data) ? resp.data : resp.data.list)) || [];
    const sel = document.getElementById('finStoreSelect');
    if (sel) {
      sel.innerHTML = '<option value="">全部店铺</option>' +
        list.map(s => `<option value="${s.store_id}">${s.alias || s.store_id}</option>`).join('');
      sel.value = financeState.storeId || '';
    }
  } catch (e) { /* ignore */ }
  await loadFinanceCenter();
}

function fmtM(v) {
  const n = Number(v || 0);
  return '₽' + n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

async function loadFinanceCenter() {
  const el = document.getElementById('finSummary');
  if (!el) return;
  el.innerHTML = '<div class="card" style="text-align:center;padding:30px;color:var(--text-tertiary);">加载中...</div>';
  try {
    const params = { days: financeState.days };
    if (financeState.storeId) params.store_id = financeState.storeId;
    const resp = await API.get('/api/finance/center', params);
    financeState.center = (resp && resp.data) || null;
    renderFinSummary();
    await loadFinTransactions();
  } catch (e) {
    el.innerHTML = `<div class="card" style="padding:24px;color:#d92d20;">加载失败: ${e.message || e}</div>`;
  }
}

function renderFinSummary() {
  const el = document.getElementById('finSummary');
  const c = financeState.center;
  if (!c) return;
  el.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;">
      <div class="card" style="padding:14px;">
        <div style="font-size:12px;color:var(--text-secondary);">净额（近${c.period_days}天）</div>
        <div style="font-size:20px;font-weight:700;margin-top:4px;color:${c.net_amount >= 0 ? '#067647' : '#d92d20'};">${fmtM(c.net_amount)}</div>
        <div style="font-size:12px;color:var(--text-tertiary);">${c.total_count} 笔交易</div>
      </div>
      <div class="card" style="padding:14px;">
        <div style="font-size:12px;color:var(--text-secondary);">入账合计</div>
        <div style="font-size:20px;font-weight:700;color:#067647;margin-top:4px;">${fmtM(c.inflow)}</div>
      </div>
      <div class="card" style="padding:14px;">
        <div style="font-size:12px;color:var(--text-secondary);">扣费合计</div>
        <div style="font-size:20px;font-weight:700;color:#d92d20;margin-top:4px;">${fmtM(c.outflow)}</div>
      </div>
      <div class="card" style="padding:14px;grid-column:span 1;">
        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px;">按大类</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px;">
          ${(c.by_type || []).slice(0, 6).map(t => `
            <span style="font-size:11px;padding:2px 8px;border-radius:10px;background:${t.amount >= 0 ? '#ecfdf3' : '#fef3f2'};color:${t.amount >= 0 ? '#067647' : '#b42318'};"
                  title="${t.count} 笔">${t.label} ${fmtM(t.amount)}</span>`).join('') || '<span style="font-size:12px;color:var(--text-tertiary);">暂无数据</span>'}
        </div>
      </div>
    </div>
    ${(c.by_store || []).length ? `
    <div class="card" style="padding:14px;margin-top:12px;">
      <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">按店铺净额</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;">
        ${c.by_store.map(s => `
          <span style="font-size:12px;padding:4px 10px;border-radius:8px;background:#f8fafc;border:1px solid var(--border,#eee);">
            <b>${s.alias}</b> <span style="color:${s.amount >= 0 ? '#067647' : '#b42318'};font-weight:600;">${fmtM(s.amount)}</span> · ${s.count}笔
          </span>`).join('')}
      </div>
    </div>` : ''}
    ${(c.sync_state || []).length ? `
    <div class="card" style="padding:14px;margin-top:12px;">
      <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">同步水位</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;">
        ${c.sync_state.map(s => `
          <span style="font-size:12px;padding:4px 10px;border-radius:8px;background:#f8fafc;border:1px solid var(--border,#eee);">
            ${s.alias} 至 ${s.last_to || '-'}（${s.transaction_count} 笔）
          </span>`).join('')}
      </div>
    </div>` : ''}
  `;
}

async function loadFinTransactions() {
  const el = document.getElementById('finTxnBody');
  if (!el) return;
  el.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-tertiary);">加载中...</div>';
  const params = { days: financeState.days, page: financeState.page, page_size: financeState.pageSize };
  if (financeState.storeId) params.store_id = financeState.storeId;
  if (financeState.parentType) params.parent_type = financeState.parentType;
  try {
    const resp = await API.get('/api/finance/transactions', params);
    const d = (resp && resp.data) || { items: [], total: 0, page: 1 };
    financeState.total = d.total || 0;
    if (!d.items || d.items.length === 0) {
      el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-tertiary);font-size:13px;">暂无交易明细 — 点右上角「同步财务明细」拉取</div>';
      return;
    }
    const totalPages = Math.max(1, Math.ceil(d.total / financeState.pageSize));
    el.innerHTML = `
      <div style="overflow:auto;">
        <table class="data-table" style="width:100%;font-size:12px;">
          <thead><tr>
            <th>时间</th><th>店铺</th><th>大类</th><th>操作类型</th><th>发货单</th><th style="text-align:right;">金额</th><th style="text-align:right;">结算</th>
          </tr></thead>
          <tbody>
            ${d.items.map(t => `
              <tr>
                <td>${(t.date || '').slice(0, 16).replace('T', ' ')}</td>
                <td>${t.alias}</td>
                <td>${t.parent_label}</td>
                <td style="color:var(--text-tertiary);">${t.operation_type_name || t.operation_type}</td>
                <td style="color:var(--text-tertiary);">${t.posting_number || '-'}</td>
                <td style="text-align:right;font-weight:600;color:${t.amount >= 0 ? '#067647' : '#b42318'};">${fmtM(t.amount)}</td>
                <td style="text-align:right;color:var(--text-tertiary);">${t.payout != null ? fmtM(t.payout) : '-'}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;font-size:13px;">
        <span style="color:var(--text-tertiary);">共 ${d.total} 笔</span>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="btn btn-xs" ${d.page <= 1 ? 'disabled' : ''} onclick="financeState.page--;loadFinTransactions()">上一页</button>
          <span>${d.page} / ${totalPages}</span>
          <button class="btn btn-xs" ${d.page >= totalPages ? 'disabled' : ''} onclick="financeState.page++;loadFinTransactions()">下一页</button>
        </div>
      </div>
    `;
  } catch (e) {
    el.innerHTML = `<div style="padding:24px;color:#d92d20;">加载失败: ${e.message || e}</div>`;
  }
}

async function financeSyncAll() {
  const btn = document.getElementById('finCenterSyncBtn');
  if (btn) { btn.disabled = true; btn.textContent = '同步中...'; }
  try {
    const resp = await API.post('/api/finance/sync', {});
    showFinanceSyncResult(resp);
  } catch (e) {
    window.Toast && Toast.show('同步失败: ' + (e.message || e), 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 同步财务明细'; if (window.lucide) lucide.createIcons(); }
    await loadFinanceCenter();
  }
}

/**
 * 按同步结果选择提示级别。
 *
 * 【为什么需要这个】此前这里写死 Toast.show(msg, 'success')，于是后端返回
 * 「同步完成：成功 0 店，失败 1 店」时，界面照样弹一个绿色对勾 —— 自相矛盾，
 * 也掩盖了「一家店都没同步上」这个事实。现在按实际结果分级：
 *   接口失败 / 全店失败 → error；部分失败 → warning；全部成功 → success
 */
function showFinanceSyncResult(resp) {
  if (!window.Toast) return;
  const code = resp && resp.code;
  const msg = (resp && resp.msg) || '同步完成';
  if (code !== 200) { Toast.show(msg, 'error'); return; }
  const d = (resp && resp.data) || {};
  const ok = Number(d.ok || 0);
  const failed = Number(d.failed || 0);
  if (failed > 0 && ok === 0) Toast.show(msg, 'error');
  else if (failed > 0) Toast.show(msg, 'warning');
  else Toast.show(msg, 'success');
}

// 路由自注册
Router.register('/finance-center', (route) => {
  const html = renderFinancePage();
  setTimeout(() => initFinancePage(), 50);
  return html;
});
