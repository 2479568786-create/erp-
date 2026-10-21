/**
 * 订单管理页 - Orders Page
 * 管理 FBS/FBO 发货单：列表筛选、状态 Tab、详情、同步、FBS 发货出库
 */
let ordersPageState = {
  fulfillment: 'FBS',
  status: '',
  keyword: '',
  page: 1,
  pageSize: 20,
  total: 0,
  storeId: '',
  counts: {},
  loading: false,
};

function renderOrdersPage(route) {
  // 从路由参数恢复fulfillment状态（FBS/FBO间切换）
  if (route && route.query && route.query.type) {
    ordersPageState.fulfillment = route.query.type.toUpperCase() === 'FBO' ? 'FBO' : 'FBS';
    ordersPageState.status = '';
    ordersPageState.page = 1;
  }
  return `
    <div style="animation: pageEnter 0.35s ease;">
      <div class="online-page-header">
        <div>
          <h2 class="online-page-title">订单管理</h2>
          <p class="online-page-desc">管理 Ozon 店铺的 FBS / FBO 发货单：状态跟踪、详情查看、发货出库</p>
        </div>
      </div>

      <!-- 同步/操作栏 -->
      <div class="batch-action-bar">
        <div class="batch-action-left">
          <button class="btn btn-xs btn-primary" onclick="syncOrders()">
            <i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 同步订单
          </button>
          <select id="orderStoreSelect" class="form-select" style="width:auto;height:34px;font-size:13px;"
                  onchange="ordersPageState.storeId=this.value;refreshOrdersView()">
            <option value="">全部店铺</option>
          </select>
          <label class="sync-days-label" style="margin-left:8px;font-size:13px;color:var(--text-secondary);">
            近 <input type="number" id="orderSyncDays" value="30" min="1" max="90"
             style="width:56px;height:30px;text-align:center;"> 天
          </label>
        </div>
        <div class="batch-action-right">
          <input type="text" id="orderKeyword" class="form-input" placeholder="搜索单号/客户/城市"
                 style="width:200px;height:34px;font-size:13px;"
                 onkeydown="if(event.key==='Enter'){ordersPageState.keyword=this.value;ordersPageState.page=1;refreshOrdersView();}">
          <button class="btn btn-xs btn-ghost" onclick="ordersPageState.keyword=document.getElementById('orderKeyword').value;ordersPageState.page=1;refreshOrdersView();">搜索</button>
        </div>
      </div>

      <!-- 类型 Tab（FBS / FBO） -->
      <div class="online-tabs">
        <button class="online-tab ${ordersPageState.fulfillment === 'FBS' ? 'active' : ''}" data-type="FBS"
                onclick="switchOrderType(this)">FBS 卖家发货</button>
        <button class="online-tab ${ordersPageState.fulfillment === 'FBO' ? 'active' : ''}" data-type="FBO"
                onclick="switchOrderType(this)">FBO 平台仓发货</button>
      </div>

      <!-- 状态 Tab -->
      <div class="online-tabs" id="orderStatusTabs">
        <button class="online-tab ${ordersPageState.status === '' ? 'active' : ''}" data-status=""
                onclick="switchOrderStatus(this)">全部</button>
        <button class="online-tab ${ordersPageState.status === 'awaiting_packaging' ? 'active' : ''}" data-status="awaiting_packaging"
                onclick="switchOrderStatus(this)">待打包</button>
        <button class="online-tab ${ordersPageState.status === 'awaiting_deliver' ? 'active' : ''}" data-status="awaiting_deliver"
                onclick="switchOrderStatus(this)">待发货</button>
        <button class="online-tab ${ordersPageState.status === 'delivering' ? 'active' : ''}" data-status="delivering"
                onclick="switchOrderStatus(this)">配送中</button>
        <button class="online-tab ${ordersPageState.status === 'delivered' ? 'active' : ''}" data-status="delivered"
                onclick="switchOrderStatus(this)">已送达</button>
        <button class="online-tab ${ordersPageState.status === 'cancelled' ? 'active' : ''}" data-status="cancelled"
                onclick="switchOrderStatus(this)">已取消</button>
        <button class="online-tab ${('arbitration'===ordersPageState.status||'client_arbitration'===ordersPageState.status) ? 'active' : ''}" data-status="arbitration"
                onclick="ordersPageState.status='arbitration';refreshOrdersView();">仲裁中</button>
      </div>

      <!-- 数据表 -->
      <div class="collect-table-wrap">
        <table class="collect-table" id="ordersTable">
          <thead>
            <tr>
              <th><input type="checkbox" class="table-check-all" onclick="toggleSelectAllOrders(this)"></th>
              <th>发货单号 / 客户</th>
              <th>商品</th>
              <th>金额</th>
              <th>类型</th>
              <th>状态</th>
              <th>接单时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="ordersTableBody">
            <tr><td colspan="8" style="text-align:center;padding:40px;color:var(--text-tertiary);">
              加载中...</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 分页 -->
      <div class="pagination-bar" id="ordersPagination" style="display:flex;justify-content:space-between;align-items:center;margin-top:16px;">
        <div style="font-size:13px;color:var(--text-secondary);" id="ordersPageInfo">共 0 条</div>
        <div>
          <button class="btn btn-xs btn-ghost" onclick="ordersPageState.page=Math.max(1,ordersPageState.page-1);refreshOrdersView();">上一页</button>
          <button class="btn btn-xs btn-ghost" onclick="ordersPageState.page+=1;refreshOrdersView();">下一页</button>
        </div>
      </div>
    </div>
  `;
}

async function initOrdersPage() {
  await loadOrderStores();
  await refreshOrdersView();
}

/** 加载店铺下拉 */
async function loadOrderStores() {
  try {
    const resp = await API.get('/api/stores');
    if (resp && resp.data) {
      const list = (Array.isArray(resp.data) ? resp.data : (resp.data.list || [])) || [];
      const sel = document.getElementById('orderStoreSelect');
      if (sel) {
        const opts = list.map(s =>
          `<option value="${s.store_id}">${s.alias || s.store_id}</option>`
        ).join('');
        sel.innerHTML = `<option value="">全部店铺</option>` + opts;
      }
    }
  } catch (e) { console.warn('[Orders] 加载店铺失败', e); }
}

async function refreshOrdersView() {
  const state = ordersPageState;
  state.loading = true;
  const body = document.getElementById('ordersTableBody');
  if (body) body.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:40px;">加载中...</td></tr>';

  // 并行拉列表 + 统计
  const [listResp, statsResp] = await Promise.all([
    API.get('/api/orders', {
      fulfillment: state.fulfillment,
      status: (state.status === 'arbitration') ? '' : state.status,
      keyword: state.keyword || '',
      store_id: state.storeId || '',
      page: state.page,
      pageSize: state.pageSize,
    }),
    API.get('/api/orders/stats', { fulfillment: state.fulfillment, store_id: state.storeId || '' }),
  ]).catch(() => [null, null]);

  if (listResp && listResp.data) {
    state.total = listResp.data.total || 0;
    renderOrdersTable(listResp.data.list || []);
  } else {
    if (body) body.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:40px;color:var(--text-tertiary);">暂无数据，请先同步订单</td></tr>';
  }
  if (statsResp && statsResp.data) {
    state.counts = statsResp.data.counts || {};
    renderOrderStatBadges();
  }
  const info = document.getElementById('ordersPageInfo');
  if (info) info.textContent = `共 ${state.total} 条 · 第 ${state.page} 页`;
  state.loading = false;
}

function renderOrdersTable(list) {
  const body = document.getElementById('ordersTableBody');
  if (!body) return;
  if (!list || list.length === 0) {
    body.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:40px;color:var(--text-tertiary);">暂无订单</td></tr>';
    return;
  }
  body.innerHTML = list.map(o => {
    let products = [];
    try { products = JSON.parse(o.products_json || '[]'); } catch (e) {}
    const names = products.slice(0, 2).map(p => (p.name || '') + (p.quantity > 1 ? ` ×${p.quantity}` : '')).join('、');
    const more = products.length > 2 ? ` 等${products.length}件` : '';
    const statusColor = o.status === 'delivered' ? 'var(--success)' :
                        o.status === 'cancelled' ? 'var(--danger)' :
                        (o.status === 'awaiting_packaging' || o.status === 'awaiting_deliver') ? 'var(--warning)' : 'var(--info)';
    return `
      <tr data-posting="${o.posting_number}">
        <td><input type="checkbox" class="order-check" data-id="${o.id}"></td>
        <td>
          <div style="font-weight:500;">${o.posting_number}</div>
          <div style="font-size:12px;color:var(--text-secondary);">${o.customer_name || '-'}${o.customer_city ? ' · ' + o.customer_city : ''}</div>
        </td>
        <td style="max-width:280px;"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;" title="${(names||'').replace(/"/g,'&quot;')}">${names || '-'}${more}</div></td>
        <td>${o.price_amount ? '¥' : ''}${o.price_amount ? Number(o.price_amount).toFixed(2) : '-'}<div style="font-size:11px;color:var(--text-tertiary);">${o.price_currency}</div></td>
        <td><span class="tag tag-${o.fulfillment_type === 'FBO' ? 'blue' : 'green'}">${o.fulfillment_type}</span></td>
        <td><span style="color:${statusColor};font-weight:500;">${o.status_label || o.status}</span></td>
        <td style="font-size:12px;">${(o.in_process_at || '').replace('T', ' ').slice(0, 16)}</td>
        <td>
          <button class="btn btn-xs btn-ghost" onclick="showOrderDetail('${o.posting_number}')">详情</button>
          ${isLossOrder(o.status) ? `<button class="btn btn-xs btn-ghost" style="color:var(--danger);" onclick="fillOrderLoss('${o.posting_number}')">填损失</button>` : ''}
          ${o.fulfillment_type === 'FBS' && (o.status === 'awaiting_packaging' || o.status === 'awaiting_deliver') ?
            `<button class="btn btn-xs btn-primary" onclick="showShipModal('${o.posting_number}')">发货</button>` : ''}
        </td>
      </tr>`;
  }).join('');
  // 重建图标
  if (window.lucide) lucide.createIcons();
}

function renderOrderStatBadges() {
  const tabs = document.querySelectorAll('#orderStatusTabs .online-tab');
  tabs.forEach(tab => {
    const st = tab.getAttribute('data-status');
    const cnt = st ? (ordersPageState.counts[st] || 0) : Object.values(ordersPageState.counts).reduce((a, b) => a + b, 0);
    tab.innerHTML = tab.textContent.replace(/\s*\(\d+\)\s*$/, '') + ` (${cnt})`;
  });
}

function switchOrderType(btn) {
  const type = btn.getAttribute('data-type');
  if (type === ordersPageState.fulfillment) return;
  ordersPageState.fulfillment = type;
  ordersPageState.status = '';
  ordersPageState.page = 1;
  // 更新 URL hash 便于分享
  window.location.hash = `#/orders?type=${type}`;
}

function switchOrderStatus(btn) {
  const st = btn.getAttribute('data-status');
  ordersPageState.status = st;
  ordersPageState.page = 1;
  refreshOrdersView();
}

function toggleSelectAllOrders(checkbox) {
  document.querySelectorAll('.order-check').forEach(cb => cb.checked = checkbox.checked);
}

/** 同步订单 */
async function syncOrders() {
  const body = document.getElementById('orderStoreSelect');
  const days = document.getElementById('orderSyncDays');
  const storeId = body ? body.value : '';
  const dayVal = days ? parseInt(days.value || '30') : 30;

  if (window.Modal) {
    Modal.show({
      title: '同步订单',
      content: `<div style="padding:12px;font-size:14px;">正在从 Ozon 拉取订单（${storeId ? '指定店铺' : '全部店铺'}，近 ${dayVal} 天）...</div>`,
      showFooter: false,
    });
  }
  try {
    const resp = await API.post('/api/orders/sync', {
      store_id: storeId || null,
      fulfillment: ordersPageState.fulfillment,
      days: dayVal,
    });
    if (resp && resp.code === 200) {
      const msg = `${ordersPageState.fulfillment} 同步完成：拉取 ${resp.data.totalFetched} 条，写入 ${resp.data.totalSaved} 条`
        + (resp.data.hasError ? '，部分店铺失败，请检查店铺凭证' : '');
      if (window.Modal) Modal.show({ title: '同步结果', content: `<div style="padding:12px;font-size:14px;">${msg}</div>` });
      else alert(msg);
    } else {
      throw new Error((resp && resp.msg) || '同步失败');
    }
  } catch (e) {
    if (window.Modal) Modal.show({ title: '同步失败', content: `<div style="padding:12px;font-size:14px;color:var(--danger);">${e.message || e}</div>` });
    else alert('同步失败：' + (e.message || e));
  }
  await refreshOrdersView();
}

/** 是否为退货/取消订单（可登记损失） */
const LOSS_STATUSES = [
  'cancelled', 'cancelled_by_seller', 'cancelled_by_client',
  'arbitration', 'client_arbitration', 'in_arbitration',
  'client_returned_delivering', 'client_returned_delivered',
  'client_returned_unknown', 'not_accepted',
];
function isLossOrder(status) { return LOSS_STATUSES.includes(status); }

/** 填写订单损失金额（弹窗） */
async function fillOrderLoss(postingNumber) {
  let current = null;
  try {
    const resp = await API.get(`/api/orders/${postingNumber}`);
    current = (resp && resp.data) || null;
  } catch (e) { /* 忽略 */ }
  const curAmount = current ? (current.loss_amount || 0) : 0;
  const curPublisher = current ? (current.loss_publisher || '') : '';
  if (!window.Modal) { alert('需要 Modal 组件'); return; }
  Modal.show({
    title: `登记损失金额 · ${postingNumber}`,
    body: `
      <div style="padding:8px 4px;">
        <div class="form-group"><label class="form-label">损失金额 (₽)</label>
          <input type="number" step="0.01" min="0" class="form-input" id="lossAmountInput"
                 value="${curAmount}" placeholder="退货/取消造成的实际损失"></div>
        <div class="form-group" style="margin-top:12px;"><label class="form-label">归属业务员（业绩扣减）</label>
          <input type="text" class="form-input" id="lossPublisherInput"
                 value="${curPublisher}" placeholder="如：张三"></div>
      </div>`,
    footer: [
      { text: '取消', class: 'btn-ghost' },
      { text: '保存', class: 'btn-primary', onClick: async () => {
        const loss_amount = parseFloat(document.getElementById('lossAmountInput').value) || 0;
        const loss_publisher = document.getElementById('lossPublisherInput').value.trim();
        if (!loss_amount) { Toast.show('损失金额不能为 0，若无需登记请点取消', 'warning'); return; }
        try {
          const resp = await API.put(`/api/orders/${postingNumber}/loss`, { loss_amount, loss_publisher });
          if (resp && resp.code === 200) {
            Modal.close();
            Toast.show('损失金额已登记', 'success');
            refreshOrdersView();
          } else {
            Toast.show((resp && resp.msg) || '保存失败', 'error');
          }
        } catch (e) {
          Toast.show('保存失败: ' + (e.message || e), 'error');
        }
      }},
    ],
  });
}

/** 订单详情 */
async function showOrderDetail(postingNumber) {
  try {
    const resp = await API.get(`/api/orders/${postingNumber}`);
    if (!resp || !resp.data) throw new Error('订单不存在');
    const o = resp.data;
    let products = [];
    try { products = JSON.parse(o.products_json || '[]'); } catch (e) {}
    const productRows = products.map(p => `
      <tr>
        <td style="padding:6px 8px;"><div style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${p.name || '-'}</div></td>
        <td style="padding:6px 8px;">${p.offer_id || p.sku || '-'}</td>
        <td style="padding:6px 8px;">×${p.quantity || 1}</td>
      </tr>
    `).join('');
    const content = `
      <div style="padding:4px 8px;font-size:14px;line-height:1.8;">
        <div style="display:flex;justify-content:space-between;">
          <span>发货单号</span><strong>${o.posting_number}</strong>
        </div>
        <div style="display:flex;justify-content:space-between;">
          <span>类型</span><strong>${o.fulfillment_type}</strong>
        </div>
        <div style="display:flex;justify-content:space-between;">
          <span>状态</span><strong>${o.status_label || o.status}</strong>
        </div>
        <div style="display:flex;justify-content:space-between;">
          <span>金额</span><strong>${o.price_amount ? Number(o.price_amount).toFixed(2) + ' ' + o.price_currency : '-'}</strong>
        </div>
        <div style="display:flex;justify-content:space-between;">
          <span>接单时间</span><span>${(o.in_process_at || '-').replace('T', ' ').slice(0, 19)}</span>
        </div>
        <div style="display:flex;justify-content:space-between;">
          <span>发货时间</span><span>${(o.ship_date || '-').replace('T', ' ').slice(0, 19)}</span>
        </div>
        <div style="display:flex;justify-content:space-between;">
          <span>客户</span><span>${o.customer_name || '-'}${o.customer_city ? ' · ' + o.customer_city : ''}</span>
        </div>
        ${o.customer_address ? `<div style="display:flex;justify-content:space-between;"><span>地址</span><span>${o.customer_address}</span></div>` : ''}
        <hr style="margin:10px 0;border-color:var(--border-secondary);">
        <div style="font-weight:500;margin-bottom:6px;">商品明细（${products.length} 件）</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead><tr style="background:var(--bg-secondary);"><th style="padding:6px 8px;text-align:left;">商品</th><th style="padding:6px 8px;text-align:left;">SKU</th><th style="padding:6px 8px;text-align:left;">数量</th></tr></thead>
          <tbody>${productRows || '<tr><td colspan="3" style="padding:8px;text-align:center;">无</td></tr>'}</tbody>
        </table>
        ${isLossOrder(o.status) ? `
        <hr style="margin:10px 0;border-color:var(--border-secondary);">
        <div style="font-weight:500;margin-bottom:6px;">退货/取消损失登记</div>
        <div style="display:flex;justify-content:space-between;font-size:13px;">
          <span>已登记损失</span><strong style="color:var(--danger);">${o.loss_amount ? '₽' + Number(o.loss_amount).toLocaleString() : '未填写'}</strong>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:13px;">
          <span>归属业务员</span><strong>${o.loss_publisher || '-'}</strong>
        </div>
        <div style="margin-top:10px;">
          <button class="btn btn-xs btn-ghost" style="color:var(--danger);" onclick="Modal.close();fillOrderLoss('${o.posting_number}')">填写/修改损失金额</button>
        </div>` : ''}
        <div style="margin-top:14px;display:flex;gap:8px;">
          <button class="btn btn-xs btn-ghost" onclick="refreshOrderDetail('${o.posting_number}')">刷新状态</button>
          ${o.fulfillment_type === 'FBS' && (o.status === 'awaiting_packaging' || o.status === 'awaiting_deliver') ?
            `<button class="btn btn-xs btn-primary" onclick="Modal.close();showShipModal('${o.posting_number}')">发货出库</button>` : ''}
        </div>
      </div>`;
    if (window.Modal) Modal.show({ title: '订单详情', content });
    else alert(content);
  } catch (e) {
    alert('加载详情失败：' + (e.message || e));
  }
}

async function refreshOrderDetail(postingNumber) {
  try {
    const resp = await API.post(`/api/orders/${postingNumber}/refresh`, {});
    if (window.Modal) Modal.show({ title: '刷新结果', content: `<div style="padding:12px;font-size:14px;">${resp && resp.code === 200 ? '订单状态已刷新' : (resp && resp.msg || '刷新失败')}</div>` });
    await refreshOrdersView();
  } catch (e) { alert('刷新失败：' + (e.message || e)); }
}

/** 发货弹窗 */
function showShipModal(postingNumber) {
  const order = window._ordersCache && window._ordersCache[postingNumber];
  let productRows = '';
  if (order) {
    let products = [];
    try { products = JSON.parse(order.products_json || '[]'); } catch (e) {}
    productRows = products.map(p => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:8px;border:1px solid var(--border-secondary);border-radius:6px;margin-bottom:6px;">
        <div style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;">${p.name || '商品'} <span style="color:var(--text-tertiary);">(SKU ${p.sku || '-'})</span></div>
        <div style="display:flex;align-items:center;gap:4px;">
          数量 <input type="number" class="ship-qty" data-sku="${p.product_id || p.sku || ''}" value="${p.quantity || 1}" min="1" max="${p.quantity || 1}" style="width:60px;height:30px;text-align:center;">
        </div>
      </div>
    `).join('') || '<div style="color:var(--text-tertiary);font-size:13px;padding:6px 0;">请先刷新订单详情以获取商品 SKU</div>';
  } else {
    productRows = '<div style="color:var(--warning);font-size:13px;padding:6px 0;">未缓存商品明细，请先打开详情再发货</div>';
  }
  if (window.Modal) {
    Modal.show({
      title: `发货出库 - ${postingNumber}`,
      content: `
        <div style="padding:6px;font-size:14px;">
          <p style="margin-bottom:10px;color:var(--text-secondary);font-size:13px;">确认出库商品与数量（不可超过订单数量）：</p>
          <div id="shipProductsContainer">${productRows}</div>
          <p style="margin-top:8px;color:var(--text-tertiary);font-size:12px;">发货后 Ozon 将流转为待揽收状态，请在卖家后台同步物流单号。</p>
        </div>`,
      onConfirm: () => confirmShip(postingNumber),
    });
  } else {
    const qty = prompt('请输入发货数量');
    if (qty) confirmShip(postingNumber, [{ product_id: 0, quantity: parseInt(qty) }]);
  }
}

async function confirmShip(postingNumber) {
  const qtyInputs = document.querySelectorAll('.ship-qty');
  const products = [];
  qtyInputs.forEach(input => {
    const sku = input.getAttribute('data-sku');
    const qty = parseInt(input.value || '1');
    if (sku && qty > 0) products.push({ product_id: Number(sku), quantity: qty });
  });
  if (products.length === 0) {
    if (window.Modal) Modal.show({ title: '提示', content: '<div style="padding:12px;">未填写有效的出库商品</div>' });
    return;
  }
  try {
    const resp = await API.post(`/api/orders/${postingNumber}/ship`, { products });
    if (window.Modal) Modal.show({
      title: resp && resp.code === 200 ? '发货成功' : '发货失败',
      content: `<div style="padding:12px;font-size:14px;">${resp && resp.code === 200 ? '已提交发货，订单状态已刷新' : (resp && resp.msg || '发货失败，请检查商品 SKU 是否正确')}</div>`,
    });
    await refreshOrdersView();
  } catch (e) {
    if (window.Modal) Modal.show({ title: '发货失败', content: `<div style="padding:12px;font-size:14px;color:var(--danger);">${e.message || e}</div>` });
  }
}

// 路由自注册
Router.register('/orders', (route) => {
  const html = renderOrdersPage(route);
  setTimeout(() => initOrdersPage(), 50);
  return html;
});