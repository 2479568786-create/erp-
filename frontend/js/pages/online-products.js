/**
 * 在线商品管理页 - Online Products Page
 * 支持商品状态管理、批量操作、监控、同步等功能
 */
function renderOnlineProductsPage(route) {
  return `
    <div style="animation: pageEnter 0.35s ease;">
      <!-- 页面标题 -->
      <div class="online-page-header">
        <div>
          <h2 class="online-page-title">在线商品</h2>
          <p class="online-page-desc">管理所有已发布的在线商品，支持状态跟踪、同步、监控等操作</p>
        </div>
        <div style="display:flex;align-items:center;gap:10px;">
          <!-- 店铺选择器（多店铺：默认全部店铺，选择单店后筛选该店在线商品） -->
          <select id="onlineStoreFilter" class="form-input" style="width:200px;height:36px;padding:0 10px;border-radius:8px;border:1px solid var(--border-color);background:var(--bg-white);color:var(--text-primary);font-size:13px;cursor:pointer;" onchange="onOnlineStoreChange(this)">
            <option value="">全部店铺</option>
            <option value="__loading" disabled>加载店铺中...</option>
          </select>
        </div>
      </div>

      <!-- 批量操作栏 -->
      <div class="batch-action-bar" id="batchActionBar">
        <div class="batch-action-left">

          <button class="btn btn-xs btn-ghost" onclick="batchEditProducts()">批量编辑</button>
          <button class="btn btn-xs btn-ghost" onclick="batchSetGroup()">分组</button>
          <div class="batch-more-dropdown">
            <button class="btn btn-xs btn-ghost" onclick="toggleBatchMore(this)">更多 <i data-lucide="chevron-down" style="width:12px;height:12px;"></i></button>
            <div class="batch-more-menu" style="display:none;">
              <a href="#" onclick="showDailyLog()">日档管理</a>
            </div>
          </div>
        </div>
        <div class="batch-action-right">
          <button class="btn btn-xs btn-ghost" onclick="syncContentRating()">同步内容评级</button>
          <div class="batch-more-dropdown">
            <button class="btn btn-xs btn-ghost" onclick="toggleImportExport(this)">导入导出 <i data-lucide="chevron-down" style="width:12px;height:12px;"></i></button>
            <div class="batch-more-menu" style="display:none;">
              <a href="#" onclick="importProducts()">导入产品</a>
              <a href="#" onclick="exportProducts()">导出产品</a>
            </div>
          </div>
          <button class="btn btn-xs btn-ghost" onclick="syncProducts()">同步产品</button>
        </div>
      </div>

      <!-- 状态Tab筛选栏 -->
      <div class="online-tabs" id="onlineTabs">
        <button class="online-tab active" data-status="" onclick="switchOnlineTab(this)">全部</button>
        <button class="online-tab" data-status="onsale" onclick="switchOnlineTab(this)">在售中</button>
        <button class="online-tab" data-status="ready" onclick="switchOnlineTab(this)">准备销售</button>
        <button class="online-tab" data-status="reviewing" onclick="switchOnlineTab(this)">审核中</button>
        <button class="online-tab" data-status="rejected" onclick="switchOnlineTab(this)">审核不通过</button>
        <button class="online-tab" data-status="offline" onclick="switchOnlineTab(this)">已下架</button>
        <button class="online-tab" data-status="archived" onclick="switchOnlineTab(this)">已归档</button>
        <button class="online-tab" data-market-tab="1" onclick="switchOnlineTab(this)">市场情报</button>
      </div>

      <!-- 表格区域 -->
      <div class="collect-table-wrap">
        <table class="collect-table" id="onlineTable">
          <thead>
            <tr>
              <th><input type="checkbox" class="table-check-all" onclick="toggleSelectAllOnline(this)"></th>
              <th>产品信息</th>
              <th>分组</th>
              <th>内容评分</th>
              <th>产品状态</th>
              <th>售价(₽)</th>
              <th>原价(₽)</th>
              <th>销量</th>
              <th>库存</th>
              <th>类目</th>
              <th>所属店铺</th>
              <th>发布人员</th>
              <th>时间</th>
              <th>备注</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="onlineTableBody"></tbody>
        </table>

        <!-- 空状态 -->
        <div class="empty-state" id="onlineEmptyState" style="display:none;">
          <div class="empty-icon">&#128722;</div>
          <p>暂无在线商品</p>
          <p style="font-size:12px;margin-top:4px;" id="onlineEmptyHint">通过「创建产品」或从采集箱发布商品到在线列表</p>
        </div>
      </div>

      <!-- 市场情报面板（从 JLC 工作台迁移到 ERP 在线商品） -->
      <div id="marketIntelPanel" style="display:none;">
        <div style="display:flex;align-items:center;gap:12px;padding:12px 4px;border-bottom:1px solid #e5e7eb;">
          <span style="font-weight:700;font-size:15px;color:#111827;">市场情报</span>
          <span id="marketIntelNotice" style="font-size:12px;color:#9ca3af;"></span>
        </div>
        <div style="display:flex;align-items:center;gap:10px;padding:12px 4px;flex-wrap:wrap;">
          <select id="marketCategorySelect" onchange="loadMarketIntelProducts();loadMarketIntelKeywords();" style="height:34px;padding:0 10px;border-radius:8px;border:1px solid #d1d5db;background:#fff;color:#111827;font-size:13px;"></select>
          <select id="marketRankingSelect" onchange="loadMarketIntelProducts();" style="height:34px;padding:0 10px;border-radius:8px;border:1px solid #d1d5db;background:#fff;color:#111827;font-size:13px;">
            <option value="hot">热销榜</option>
            <option value="rising">飙升榜</option>
          </select>
          <button class="btn btn-xs btn-ghost" onclick="loadMarketIntelProducts();loadMarketIntelKeywords();">刷新</button>
        </div>
        <div id="marketIntelGrid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;padding:8px 4px;"></div>
        <div id="marketIntelKeywords" style="padding:14px 4px;"></div>
        <div id="marketIntelSearchVisibility" style="padding:14px 4px;"></div>
      </div>

      <!-- 底部操作栏 -->
      <div class="collect-footer-bar" id="onlineFooterBar" style="display:flex;">
        <div class="collect-footer-left">
          <span class="select-info">已选 <strong id="onlineSelectedCount">0</strong> 条</span>
          <button class="btn btn-sm btn-ghost" onclick="batchEditProducts()">批量编辑</button>
          <button class="btn btn-sm btn-ghost danger" onclick="batchDeleteOnline()">批量删除</button>
        </div>
        <div class="collect-footer-right">
          <span class="total-info">共 <strong id="onlineTotalCount">0</strong> 条数据</span>
        </div>
      </div>
    </div>
  `;
}

// ===== 数据层 =====

let allOnlineProducts = [];
let filteredOnline = [];
let currentOnlineStatus = '';
// 当前店铺筛选（'' = 全部店铺）
let currentOnlineStoreId = '';
// 店铺列表缓存（多店铺）
let _onlineStoresCache = null;
// 后端不可用时不展示伪造数据，也不允许本地假更新
let isBackendUnavailable = false;
// 数据加载状态
let _onlineLoading = false;

/** 加载店铺列表并填充筛选下拉（多店铺） */
async function loadOnlineStoreFilter() {
  const sel = document.getElementById('onlineStoreFilter');
  if (!sel) return;
  // 已有缓存直接填充
  if (_onlineStoresCache && _onlineStoresCache.length >= 0) {
    renderOnlineStoreOptions(sel, _onlineStoresCache);
    return;
  }
  try {
    const res = await Api.getStores({ authStatus: '' });
    const stores = (res && res.code === 200 && res.data && Array.isArray(res.data.list)) ? res.data.list : [];
    _onlineStoresCache = stores;
    renderOnlineStoreOptions(sel, stores);
  } catch (e) {
    console.warn('[在线商品] 加载店铺列表失败:', e);
    sel.innerHTML = '<option value="">全部店铺</option>';
  }
}

function renderOnlineStoreOptions(sel, stores) {
  const active = stores.filter(s => (s.authStatus === 'active' || !s.authStatus));
  const opts = ['<option value="">全部店铺</option>'];
  if (active.length === 0) {
    opts.push('<option value="" disabled>暂无已授权店铺</option>');
  } else {
    active.forEach(s => {
      // 列表筛选与同步接口使用本地 stores.id，不是 Ozon 的 store_id。
      const sid = s.id || s.store_id || s.storeId;
      const name = s.alias || s.name || s.store_name || sid;
      opts.push(`<option value="${escapeAttr(sid)}">${escapeHtml(name)}</option>`);
    });
  }
  sel.innerHTML = opts.join('');
}

/** 店铺下拉变更：切换到单店/全部店铺并重新拉取 */
function onOnlineStoreChange(sel) {
  currentOnlineStoreId = sel.value || '';
  filteredOnline = [];
  // 清空状态Tab与本地搜索（保持一致性）
  document.querySelectorAll('.online-tab').forEach(t => t.classList.remove('active'));
  document.querySelector('.online-tab[data-status=""]')?.classList.add('active');
  currentOnlineStatus = '';
  const kw = document.getElementById('onlineSearchInput');
  if (kw) kw.value = '';
  reloadOnlineProducts();
}

/** 从后端加载在线商品列表 */
async function reloadOnlineProducts() {
  if (_onlineLoading) return;
  _onlineLoading = true;
  try {
    // 店铺筛选：透传 storeId，后端按店过滤；'' = 全部店铺（保持现状）
    const res = await Api.getOnlineProducts({ page: 1, pageSize: 1000, storeId: currentOnlineStoreId });
    if (res && res.code === 200 && res.data && Array.isArray(res.data.list)) {
      allOnlineProducts = res.data.list;
      isBackendUnavailable = false;
      const statsRes = await Api.getOnlineProductStats(currentOnlineStoreId);
      if (statsRes && statsRes.code === 200) renderOnlineTabCounts(statsRes.data);
    } else {
      allOnlineProducts = [];
      isBackendUnavailable = true;
    }
  } catch (e) {
    console.warn('[在线商品] 后端加载失败:', e);
    allOnlineProducts = [];
    isBackendUnavailable = true;
  } finally {
    _onlineLoading = false;
  }
  filteredOnline = [...allOnlineProducts];
  renderOnlineTable();
}

function renderOnlineTable() {
  const tbody = document.getElementById('onlineTableBody');
  const emptyEl = document.getElementById('onlineEmptyState');
  if (!tbody) return;

  if (filteredOnline.length === 0) {
    tbody.innerHTML = '';
    emptyEl.style.display = '';
    const hint = document.getElementById('onlineEmptyHint');
    if (currentOnlineStoreId) {
      if (hint) hint.textContent = '该店铺暂无在线商品，可尝试「同步产品」拉取';
    } else if (hint) {
      hint.textContent = '通过「创建产品」或从采集箱发布商品到在线列表';
    }
    document.getElementById('onlineFooterBar').style.display = 'none';
    return;
  }

  emptyEl.style.display = 'none';
  document.getElementById('onlineFooterBar').style.display = 'flex';

  tbody.innerHTML = filteredOnline.map((p) => `
    <tr data-id="${p.id}">
      <td><input type="checkbox" class="table-check-item online-check" value="${p.id}"></td>
      <td>
        <div class="product-info-cell">
          <img class="product-thumb" src="${escapeAttr(proxyImage(typeof p.image === 'string' ? p.image : ''))}" data-original-image="${escapeAttr(typeof p.image === 'string' ? p.image : '')}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="if(this.dataset.originalImage&&this.dataset.originalImage!==this.src){const imageUrl=this.dataset.originalImage;this.dataset.originalImage='';this.src=imageUrl;}else{this.onerror=null;this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2260%22 height=%2260%22%3E%3Crect width=%22100%25%22 height=%22100%25%22 fill=%22%23f5f5f5%22/%3E%3Ctext x=%2250%25%22 y=%2255%25%22 text-anchor=%22middle%22 fill=%22%23ccc%22 font-size=%2211%22%3E无图%3C/text%3E%3C/svg%3E'}">
          <div class="product-text">
            <div class="product-name">${p.title}</div>
            <div class="product-source-row">
              <span class="source-line">产品ID：<span class="source-id">${p.productId || '-'}</span></span>
              <span class="source-line">合并编号：<span class="source-id">${p.mergeNo || '-'}</span></span>
              <span class="source-line">SKU ID：<span class="source-id">${p.skuId || '-'}</span></span>
              <span class="source-line">平台SKU：<span class="source-id">${p.platformSku || '-'}</span></span>
              <span class="source-link-text">货源：<span class="source-id">${p.sourceId ? (p.sourceId.startsWith('(') ? p.sourceId : '(' + p.sourceId + ')') : '-'}</span>${p.sourceLink && p.sourceLink !== '#' ? '<span class="source-link-wrap" style="position:relative;display:inline-block;">' +
                '<a class="source-link" href="' + p.sourceLink + '" target="_blank">' + (p.sourceName || '1688分销') + ' +</a>' +
                '<div class="source-popover">' +
                  '<span class="source-popover-label">' + (p.sourceName || '1688分销') + '</span>' +
                  '<a class="source-popover-url" href="' + p.sourceLink + '" target="_blank">' + p.sourceLink + '</a>' +
                '</div>' +
              '</span>' : ''}</span>
            </div>
          </div>
        </div>
      </td>
      <td><span class="group-badge">${p.group || '-'}</span></td>
      <td>${renderContentScore(p.contentScore)}</td>
      <td>${getOnlineStatusBadge(p.status)}</td>
      <td class="price-cell">${p.price ? '₽' + p.price.toLocaleString() : '-'}</td>
      <td class="original-price-cell">${p.originalPrice ? '₽' + p.originalPrice.toLocaleString() : '-'}</td>
      <td class="sales-cell">${p.sales > 0 ? p.sales : '-'}</td>
      <td class="${p.stock <= 10 && p.stock > 0 ? 'stock-warning' : ''}">${p.stock > 0 ? p.stock : '<span style="color:#DC2626;">缺货</span>'}</td>
      <td><span class="category-text">${p.category.split('|')[1] || p.category}</span></td>
      <td><span class="store-badge">${p.store}</span></td>
      <td>${p.publisher || '-'}</td>
      <td class="time-cell">${p.time}</td>
      <td class="note-cell"><span class="note-text" title="${p.note || ''}">${p.note || '-'}</span></td>
      <td>
        <div class="action-btns">
          <button class="action-link" onclick="editOnlineProductCard('${p.id}')">编辑商品</button>
          <button class="action-link" onclick="startRelistOnlineProduct('${p.id}')">老链接新上</button>
          <button class="action-link" onclick="editOnlineProduct('${p.id}')">价格库存</button>
          <button class="action-link" onclick="syncProduct('${p.id}')">同步</button>
          <button class="action-link more-action" onclick="toggleOnlineMore(this)">更多 &#9662;</button>
        </div>
        <div class="more-actions-menu" style="display:none;" data-pid="${p.id}">
          <a href="#" onclick="viewProductDetail('${p.id}');return false;">查看详情</a>
          <a href="#" class="danger" onclick="deleteOnlineProduct('${p.id}');return false;">删除商品</a>
        </div>
      </td>
    </tr>
  `).join('');

  document.getElementById('onlineTotalCount').textContent = filteredOnline.length;
  updateOnlineSelection();
  updateOnlineTabCounts();

  if (window.lucide) lucide.createIcons();
  bindOnlineSourcePopover();
}

/** 在线商品页 - 绑定 1688分销+ 悬浮弹窗 */
let _onlinePopoverTimer = null;

function bindOnlineSourcePopover() {
  var wraps = document.querySelectorAll('#onlineTableBody .source-link-wrap');
  wraps.forEach(function (wrap) {
    var popover = wrap.querySelector('.source-popover');
    if (!popover) return;
    wrap.addEventListener('mouseenter', function () {
      clearTimeout(_onlinePopoverTimer);
      var allOpen = document.querySelectorAll('#onlineTableBody .source-popover');
      allOpen.forEach(function (p) { p.style.display = 'none'; });
      // 计算位置（fixed 定位，居中对齐）
      var rect = wrap.getBoundingClientRect();
      popover.style.display = 'block';
      popover.style.top = (rect.bottom + 6) + 'px';
      var popWidth = popover.offsetWidth;
      var centerLeft = rect.left + rect.width / 2 - popWidth / 2;
      if (centerLeft < 10) centerLeft = 10;
      if (centerLeft + popWidth > window.innerWidth - 10) {
        centerLeft = window.innerWidth - popWidth - 10;
      }
      popover.style.left = centerLeft + 'px';
      var arrowLeft = rect.left + rect.width / 2 - centerLeft - 5;
      popover.style.setProperty('--arrow-left', arrowLeft + 'px');
    });
    wrap.addEventListener('mouseleave', function () {
      _onlinePopoverTimer = setTimeout(function () { popover.style.display = 'none'; }, 200);
    });
    popover.addEventListener('mouseenter', function () {
      clearTimeout(_onlinePopoverTimer);
    });
    popover.addEventListener('mouseleave', function () {
      _onlinePopoverTimer = setTimeout(function () { popover.style.display = 'none'; }, 200);
    });
  });
}

/** Ozon 后端内容评分；未返回时不伪造等级。 */
function renderContentScore(score) {
  if (score === null || score === undefined || score === '') {
    return '<span style="color:var(--text-muted)">未评分</span>';
  }
  const value = Number(score);
  if (!Number.isFinite(value)) return '<span style="color:var(--text-muted)">未评分</span>';
  const color = value >= 80 ? '#16803c' : value >= 50 ? '#b36b00' : '#c73535';
  return `<div title="Ozon 内容评分" style="min-width:76px"><strong style="color:${color}">${value.toFixed(value % 1 ? 1 : 0)}</strong><div style="height:4px;background:var(--border-color);margin-top:5px"><i style="display:block;height:100%;width:${Math.max(0, Math.min(100, value))}%;background:${color}"></i></div></div>`;
}

/** 在线商品状态徽章 */
function getOnlineStatusBadge(status) {
  const map = {
    onsale: { text: '在售中', cls: 'status-onsale' },
    ready: { text: '准备销售', cls: 'status-ready' },
    reviewing: { text: '审核中', cls: 'status-reviewing' },
    rejected: { text: '审核不通过', cls: 'status-rejected' },
    offline: { text: '已下架', cls: 'status-offline' },
    archived: { text: '已归档', cls: 'status-archived' },
  };
  const item = map[status] || { text: status, cls: '' };
  return `<span class="online-status-badge ${item.cls}">${item.text}</span>`;
}

// ===== Tab & 筛选 =====

/** 切换状态Tab */
function switchOnlineTab(btn) {
  document.querySelectorAll('.online-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  if (btn.dataset.marketTab === '1') {
    currentOnlineStatus = '';
    showMarketIntelligence();
    return;
  }
  hideMarketIntelligence();
  currentOnlineStatus = btn.dataset.status;
  filterOnlineProducts();
}

function renderOnlineTabCounts(data) {
  const stats = (data && data.stats) || {};
  document.querySelectorAll('.online-tab').forEach(tab => {
    if (tab.dataset.marketTab === '1') return;
    const value = tab.dataset.status ? (stats[tab.dataset.status] || 0) : (data.total || 0);
    let countEl = tab.querySelector('.tab-count');
    if (!countEl) {
      countEl = document.createElement('span');
      countEl.className = 'tab-count';
      tab.appendChild(countEl);
    }
    countEl.textContent = `(${value})`;
  });
}

/** 更新Tab计数 */
function updateOnlineTabCounts() {
  // 服务端统计代表完整数据集；表格本地过滤不能覆盖它。
  if (!isBackendUnavailable) return;
  const counts = { '': allOnlineProducts.length };
  ['onsale','ready','reviewing','rejected','offline','archived'].forEach(s => {
    counts[s] = allOnlineProducts.filter(p => p.status === s).length;
  });
  document.querySelectorAll('.online-tab').forEach(tab => {
    if (tab.dataset.marketTab === '1') return;
    const st = tab.dataset.status;
    const countEl = tab.querySelector('.tab-count');
    if (!countEl) tab.innerHTML += ` <span class="tab-count">(${counts[st]})</span>`;
    else countEl.textContent = `(${counts[st]})`;
  });
}

/** 筛选 */
function filterOnlineProducts() {
  const groupVal = document.getElementById('filterGroup')?.value || '';
  const storeVal = document.getElementById('filterStore')?.value || '';
  const keyword = (document.getElementById('onlineSearchInput')?.value || '').trim().toLowerCase();

  filteredOnline = allOnlineProducts.filter(p => {
    // 状态筛选
    if (currentOnlineStatus && p.status !== currentOnlineStatus) return false;
    // 分组
    if (groupVal && p.group !== groupVal) return false;
    // 店铺
    if (storeVal && p.store !== storeVal) return false;
    // 关键词
    if (keyword) {
      const fields = [p.title, p.sku, p.note, p.category].join(' ').toLowerCase();
      if (!fields.includes(keyword)) return false;
    }
    return true;
  });

  renderOnlineTable();
}

/** 重置筛选 */
function resetOnlineFilters() {
  const el = (id) => document.getElementById(id);
  if (el('filterGroup')) el('filterGroup').value = '';
  if (el('filterStore')) el('filterStore').value = '';
  if (el('onlineSearchInput')) el('onlineSearchInput').value = '';
  // 同步重置店铺下拉到"全部店铺"
  const storeSel = el('onlineStoreFilter');
  if (storeSel && storeSel.value) { storeSel.value = ''; }
  currentOnlineStoreId = '';
  currentOnlineStatus = '';
  document.querySelectorAll('.online-tab').forEach(t => t.classList.remove('active'));
  document.querySelector('.online-tab[data-status=""]')?.classList.add('active');
  filteredOnline = [...allOnlineProducts];
  renderOnlineTable();
}

// ===== 选择 & 批量操作 =====

function toggleSelectAllOnline(cb) {
  document.querySelectorAll('.online-check').forEach(i => i.checked = cb.checked);
  updateOnlineSelection();
}
function updateOnlineSelection() {
  const checked = document.querySelectorAll('.online-check:checked');
  document.getElementById('onlineSelectedCount').textContent = checked.length;
  const allCb = document.querySelector('.table-check-all');
  const total = document.querySelectorAll('.online-check');
  if (allCb) allCb.checked = total.length > 0 && checked.length === total.length;
}
function getSelectedOnlineIds() {
  return [...document.querySelectorAll('.online-check:checked')].map(el => el.value);
}

/** 批量编辑 */
function batchEditProducts() {
  const ids = getSelectedOnlineIds();
  if (!ids.length) { Toast.show('请先选择商品', 'warning'); return; }
  Modal.show({
    title: '批量编辑 - 已选择 ' + ids.length + ' 条',
    size: 'lg',
    body: `
      <div class="form-row-2">
        <div class="form-group"><label class="form-label">统一售价调整</label>
          <select class="form-select" id="batchPriceMode">
            <option value="">不修改</option>
            <option value="percent-10">降价 10%</option>
            <option value="percent-20">降价 20%</option>
            <option value="percent+10">涨价 10%</option>
            <option value="fixed">设置固定价格</option>
          </select>
        </div>
        <div class="form-group"><label class="form-label">固定价格(₽)</label>
          <input type="number" class="form-input" id="batchFixedPrice" placeholder="仅当选择"设置固定价格"时生效"></div>
      </div>
      <div class="form-row-2">
        <div class="form-group"><label class="form-label">设置分组</label>
          <select class="form-select" id="batchNewGroup">
            <option value="">不修改</option>
            <option value="3C数码">3C数码</option>
            <option value="家居家纺">家居家纺</option>
            <option value="穿戴设备">穿戴设备</option>
            <option value="美妆工具">美妆工具</option>
          </select>
        </div>
        <div class="form-group"><label class="form-label">设置状态</label>
          <select class="form-select" id="batchNewStatus">
            <option value="">不修改</option>
            <option value="onsale">在售中</option>
            <option value="offline">已下架</option>
          </select>
        </div>
      </div>
      <div class="form-group"><label class="form-label">批量备注</label>
        <textarea class="form-textarea" id="batchNote" rows="2" placeholder="追加备注内容..."></textarea></div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost' },
      { text: '应用修改', class: 'btn-primary', onClick: async () => {
        if (isBackendUnavailable) {
          Toast.show('后端未连接，无法修改在线商品', 'warning');
          return;
        }
        const mode = document.getElementById('batchPriceMode').value;
        const fixedPrice = parseInt(document.getElementById('batchFixedPrice').value);
        const newGroup = document.getElementById('batchNewGroup').value;
        const newStatus = document.getElementById('batchNewStatus').value;
        const batchNote = document.getElementById('batchNote').value.trim();

        // 本地预览更新
        ids.forEach(id => {
          const p = allOnlineProducts.find(x => x.id === id); if (!p) return;
          if (mode.startsWith('percent')) { const pct = parseFloat(mode.split('-')[1]); p.price = Math.round(p.price * (1 + pct / 100)); }
          else if (mode === 'fixed') { if (fixedPrice) p.price = fixedPrice; }
          if (newGroup) p.group = newGroup;
          if (newStatus) p.status = newStatus;
          if (batchNote) p.note = (p.note ? p.note + ' | ' : '') + batchNote;
        });

        const updates = {};
        if (newGroup) updates.group = newGroup;
        if (newStatus) updates.status = newStatus;
        if (batchNote) updates.note = batchNote;
        if (Object.keys(updates).length > 0) {
          try {
            const res = await Api.batchUpdateOnlineProducts(ids, updates);
            if (res && res.code === 200) {
              Toast.show(`已持久化 ${res.data.affected || ids.length} 条商品的分组/状态/备注`, 'success');
            }
          } catch (e) {
            console.warn('[在线商品] 批量更新后端失败:', e);
          }
        }
        if (mode !== '') {
          Toast.show('价格调整为本地预览，如需同步到 Ozon 请逐个编辑并推送', 'info');
        }

        Modal.close(); filterOnlineProducts(); Toast.show(`已更新 ${ids.length} 条商品`, 'success');
      }},
    ],
  });
}

/** 添加监控 */
function batchAddMonitor() {
  const ids = getSelectedOnlineIds();
  if (!ids.length) { Toast.show('请先选择商品', 'warning'); return; }
  Modal.show({
    title: '添加监控 - ' + ids.length + ' 条商品',
    body: `
      <div class="form-group"><label class="form-label">监控类型</label>
        <div style="display:flex;gap:12px;margin-top:6px;">
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer;"><input type="checkbox" checked> 库存预警</label>
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer;"><input type="checkbox" checked> 价格变动</label>
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer;"><input type="checkbox"> 销量异常</label>
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer;"><input type="checkbox"> 排名变化</label>
        </div>
      </div>
      <div class="form-row-2">
        <div class="form-group"><label class="form-label">库存阈值（低于此值提醒）</label>
          <input type="number" class="form-input" value="20"></div>
        <div class="form-group"><label class="form-label">通知方式</label>
          <select class="form-select"><option selected>站内消息</option><option>邮件通知</option><option>Webhook</option></select></div>
      </div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost' },
      { text: '确认添加', class: 'btn-primary', onClick: () => { Modal.close(); Toast.show(`已为 ${ids.length} 条商品设置监控规则`, 'success'); }},
    ],
  });
}
function addMonitor(id) {
  batchAddMonitor();
}

/** 复制产品 */
function batchCopyProducts() {
  const ids = getSelectedOnlineIds();
  if (!ids.length) { Toast.show('请先选择商品', 'warning'); return; }
  let count = 0;
  ids.forEach(id => {
    const orig = allOnlineProducts.find(p => p.id === id);
    if (!orig) return;
    const copy = JSON.parse(JSON.stringify(orig));
    copy.id = 'OP-COPY-' + Date.now() + '-' + (++count);
    copy.sku = orig.sku + '-COPY';
    copy.title = '[复制] ' + orig.title;
    copy.sales = 0;
    copy.time = new Date().toLocaleString('zh-CN').replace(/\//g,'-');
    copy.status = 'ready';
    allOnlineProducts.unshift(copy);
  });
  resetOnlineFilters();
  Toast.show(`已复制 ${count} 个商品`, 'success');
}
function copyProduct(id) {
  const orig = allOnlineProducts.find(p => p.id === id);
  if (!orig) return;
  const copy = JSON.parse(JSON.stringify(orig));
  copy.id = 'OP-COPY-' + Date.now();
  copy.sku = orig.sku + '-COPY';
  copy.title = '[复制] ' + orig.title;
  copy.sales = 0;
  copy.time = new Date().toLocaleString('zh-CN').replace(/\//g,'-');
  copy.status = 'ready';
  allOnlineProducts.unshift(copy);
  resetOnlineFilters();
  Toast.show('商品已复制', 'success');
}

/** 设分组 */
function batchSetGroup() {
  const ids = getSelectedOnlineIds();
  if (!ids.length) { Toast.show('请先选择商品', 'warning'); return; }
  Modal.show({
    title: '批量设置分组 - ' + ids.length + ' 条',
    body: `
      <div class="form-group"><label class="form-label">目标分组</label>
        <select class="form-select" id="targetGroup">
          <option value="3C数码">3C数码</option><option value="家居家纺">家居家纺</option><option value="穿戴设备">穿戴设备</option>
          <option value="美妆工具">美妆工具</option><option value="运动户外">运动户外</option><option value="未分组">未分组</option>
        </select></div>`,
    footer: [
      { text: '取消', class: 'btn-ghost' },
      { text: '确认', class: 'btn-primary', onClick: async () => {
        if (isBackendUnavailable) {
          Toast.show('后端未连接，无法设置分组', 'warning');
          return;
        }
        const g = document.getElementById('targetGroup').value;
        ids.forEach(id => { const p = allOnlineProducts.find(x=>x.id===id); if(p)p.group=g; });
        Modal.close(); filterOnlineProducts(); Toast.show(`已将 ${ids.length} 个商品移至「${g}」`,'success');
        try {
          const res = await Api.batchUpdateOnlineProducts(ids, { group: g });
          if (res && res.code === 200) {
            Toast.show(`已同步 ${res.data.affected || ids.length} 条商品分组到后端`, 'success');
          } else {
            console.warn('[在线商品] 批量分组后端返回异常:', res);
          }
        } catch (e) {
          console.warn('[在线商品] 批量分组后端失败:', e);
        }
      }},
    ],
  });
}

/** 归档 */
function batchArchive() {
  const ids = getSelectedOnlineIds();
  if (!ids.length) { Toast.show('请先选择商品', 'warning'); return; }
  Modal.confirm(`确定要归档选中的 ${ids.length} 个商品吗？`).then(async (ok) => {
    if (!ok) return;
    if (isBackendUnavailable) {
      Toast.show('后端未连接，无法归档商品', 'warning');
      return;
    }
    ids.forEach(id => { const p=allOnlineProducts.find(x=>x.id===id);if(p)p.status='archived';});
    filterOnlineProducts(); Toast.show(`已归档 ${ids.length} 个商品`,'success');
    try {
      const res = await Api.batchUpdateOnlineProducts(ids, { status: 'archived' });
      if (res && res.code === 200) {
        Toast.show(`已同步 ${res.data.affected || ids.length} 条归档状态到后端`, 'success');
      }
    } catch (e) {
      console.warn('[在线商品] 批量归档后端失败:', e);
    }
  });
}
function archiveProduct(id) {
  const p = allOnlineProducts.find(x => x.id === id);
  if (!p) return;
  Modal.confirm(`确定归档商品「${p.sku}」吗？`).then(async (ok) => {
    if (!ok) return;
    if (isBackendUnavailable) {
      Toast.show('后端未连接，无法归档商品', 'warning');
      return;
    }
    p.status = 'archived'; filterOnlineProducts(); Toast.show('商品已归档','success');
    try {
      const res = await Api.updateOnlineProduct(id, { status: 'archived' });
      if (res && res.code === 200 && res.data) {
        const idx = allOnlineProducts.findIndex(x => x.id === id);
        if (idx >= 0) allOnlineProducts[idx] = res.data;
        filterOnlineProducts();
      }
    } catch (e) {
      console.warn('[在线商品] 归档后端失败:', e);
    }
  });
}

/** 生成仓库商品 */
function generateWarehouseProducts() {
  const ids = getSelectedOnlineIds();
  if (!ids.length) { Toast.show('请先选择商品', 'warning'); return; }
  Toast.show(`正在为 ${ids.length} 个商品生成仓库版本...`,'info');
  setTimeout(() => { Toast.show(`${ids.length} 个仓库商品已生成`,'success'); }, 1500);
}
function createWarehouseFromProduct(id) {
  Toast.show('正在生成仓库商品...','info');
  setTimeout(() => Toast.show('仓库商品已生成','success'), 1200);
}

/** 同步内容评级 */
async function syncContentRating() {
  if (isBackendUnavailable) {
    Toast.show('后端未连接，无法同步内容评分', 'warning');
    return;
  }
  Toast.show('正在从 Ozon 同步内容评分...', 'info');
  const response = await Api.syncOnlineProductContentScores();
  if (!response || response.code !== 200) {
    Toast.show(response?.msg || '内容评分同步失败', 'error');
    return;
  }
  await reloadOnlineProducts();
  filterOnlineProducts();
  const data = response.data || {};
  Toast.show(`内容评分同步完成：更新 ${data.updated || 0}，未评分 ${data.unrated || 0}，失败 ${data.failed || 0}`, 'success');
}

/** 切换「更多」下拉 */
function toggleBatchMore(btn) {
  const menu = btn.nextElementSibling;
  const visible = menu.style.display !== 'none';
  menu.style.display = visible ? 'none' : 'block';
  if (!visible && window.lucide) lucide.createIcons();
}
/** 切换「导入导出」下拉 */
function toggleImportExport(btn) {
  const menu = btn.nextElementSibling;
  const visible = menu.style.display !== 'none';
  menu.style.display = visible ? 'none' : 'block';
  if (!visible && window.lucide) lucide.createIcons();
}

/** 创建产品 */
function createProduct() {
  Toast.show('打开创建产品', 'info');
  // TODO: 打开创建产品弹窗/页面
}
/** 导入产品 */
function importProducts() {
  Toast.show('打开导入产品', 'info');
  toggleImportExport(event.target.closest('.batch-more-dropdown').querySelector('button'));
}
/** 导出产品 */
function exportProducts() {
  Toast.show('导出产品中...', 'info');
  toggleImportExport(event.target.closest('.batch-more-dropdown').querySelector('button'));
}
/** 同步产品 - 从 Ozon 店铺全量拉取并 upsert 到本地 */
async function syncProducts() {
  if (isBackendUnavailable) {
    Toast.show('后端未连接，无法同步。请先启动后端服务并配置 Ozon API 凭证', 'warning');
    return;
  }
  Toast.show(currentOnlineStoreId ? '正在同步当前店铺商品，可能需要几分钟...' : '正在同步全部店铺商品，可能需要几分钟...', 'info');
  try {
    let storesToSync;
    if (currentOnlineStoreId) {
      storesToSync = [currentOnlineStoreId];
    } else {
      await loadOnlineStoreFilter();
      storesToSync = (_onlineStoresCache || [])
        .filter(store => store.authStatus === 'active' || !store.authStatus)
        .map(store => store.id || store.store_id || store.storeId)
        .filter(Boolean);
    }
    if (!storesToSync.length) {
      Toast.show('没有可同步的已授权店铺', 'warning');
      return;
    }
    const totals = { total: 0, inserted: 0, updated: 0, failed: 0, orphanDeleted: 0 };
    for (const storeId of storesToSync) {
      const res = await Api.syncOnlineProducts({ storeId });
      if (!res || res.code !== 200) throw new Error(res?.msg || `店铺 ${storeId} 同步失败`);
      const data = res.data || {};
      Object.keys(totals).forEach(key => { totals[key] += Number(data[key] || 0); });
    }
    Toast.show(`同步完成：共 ${totals.total} 个，新增 ${totals.inserted}，更新 ${totals.updated}，失败 ${totals.failed}${totals.orphanDeleted ? `，清理残留 ${totals.orphanDeleted}` : ''}`, 'success');
    await reloadOnlineProducts();
  } catch (e) {
    Toast.show('同步异常：' + (e && e.message ? e.message : e), 'error');
  }
}

/** 日档管理 */
function showDailyLog() {
  Modal.show({
    title: '日档管理',
    size: 'lg',
    body: `
      <div style="font-size:13px;color:#6B7280;margin-bottom:12px;">查看商品的日常运营档案记录（销量、调价、状态变更等）</div>
      <table class="collect-table" style="font-size:12.5px;">
        <thead><tr><th>日期</th><th>商品</th><th>操作</th><th>变更前</th><th>变更后</th><th>操作人</th></tr></thead>
        <tbody>
          <tr><td>2026-06-20</td><td>OZ-PET-BRUSH-001</td><td>调价</td><td>₽1299</td><td>₽1199</td><td>卓老师</td></tr>
          <tr><td>2026-06-19</td><td>OZ-AUDIO-BT-002</td><td>上架</td><td>-</td><td>在售中</td><td>李敏茹</td></tr>
          <tr><td>2026-06-18</td><td>OZ-KB-RGB-003</td><td>创建</td><td>-</td><td>准备销售</td><td>王强</td></tr>
          <tr><td>2026-06-17</td><td>OZ-WATCH-SM-005</td><td>库存预警</td><td>50</td><td>89(补货)</td><td>系统</td></tr>
        </tbody>
      </table>
    `,
    footer: [{ text: '关闭', class: 'btn-ghost', onClick: () => Modal.close() }],
  });
}
function openDailyLog(id) { showDailyLog(); }

// ===== 单项操作 =====

/** 从 Ozon 反向读取，并使用共享商品编辑器打开。 */
async function editOnlineProductCard(id) {
  Toast.show('正在从 Ozon 读取完整商品信息...', 'info');
  const response = await Api.getOnlineProductEditData(id);
  if (!response || response.code !== 200 || !response.data) {
    Toast.show(response?.msg || 'Ozon 商品读取失败', 'error');
    return;
  }
  await editProduct(String(id), {
    product: response.data,
    editorMode: 'online',
    skipFetch: true,
  });
}

/** 在线编辑保存器：只更新 Ozon，不写入采集箱。 */
async function saveOnlineEditorProduct(product, id) {
  const buttons = document.querySelectorAll('.modal-footer .btn');
  buttons.forEach(button => { button.disabled = true; });
  try {
    collectEditFormToProduct(product, { skipValidation: true });
    const originalOfferId = String(product.offerId || '').trim();
    if (!originalOfferId) throw new Error('未读取到原 Ozon SKU 编码，无法安全更新');
    if (!Array.isArray(product.skus) || product.skus.length !== 1) {
      throw new Error('在线编辑只支持更新当前 SKU；新增 SKU 请使用“老链接新上”');
    }
    product.skus[0].skuCode = originalOfferId;
    product.skus[0].offerId = originalOfferId;
    product.skus[0].offer_id = originalOfferId;
    product.offerId = originalOfferId;
    const validation = ProductMapping.validateForPublish(product);
    if (!validation.valid) {
      Toast.show('更新校验失败：\n' + validation.errors.join('\n'), 'error');
      return;
    }
    const response = await Api.saveOnlineProductEditData(id, product);
    if (!response || response.code !== 200) {
      throw new Error(response?.msg || 'Ozon 更新失败');
    }
    await Modal.forceClose();
    // 线上编辑保存成功：清理可能残留的编辑草稿（草稿机制定义在 collect.js）
    if (typeof _clearEditDraft === 'function') _clearEditDraft(id);
    await reloadOnlineProducts();
    filterOnlineProducts();
    Toast.show('已提交 Ozon 更新；Ozon 处理完成后点击“同步”回读最新状态', 'success');
  } catch (error) {
    Toast.show('更新失败：' + (error?.message || error), 'error');
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}

/**
 * 老链接新上：以在线商品数据为模板，在采集箱创建一条独立的新商品记录。
 * 从在线商品列表直接读取完整商品数据，避免先进入原商品编辑器。
 */
async function startRelistOnlineProduct(id) {
  Toast.show('正在读取原商品信息...', 'info');
  try {
    const response = await Api.getOnlineProductEditData(id);
    if (!response || response.code !== 200 || !response.data) {
      Toast.show(response?.msg || '读取原商品失败', 'error');
      return;
    }
    await cloneOnlineProductAsNewLink(response.data);
  } catch (error) {
    Toast.show('读取原商品失败：' + (error?.message || error), 'error');
  }
}

/** 从在线编辑器收集当前修改，并进入跨店铺 AI 重制流程。 */
async function relistOnlineEditorProduct() {
  const product = window._editingProduct;
  if (!product || product._editorMode !== 'online') {
    Toast.show('请先打开在线商品编辑器', 'warning');
    return;
  }
  try {
    await Promise.allSettled(Array.from(_editImageUploads || []));
    collectEditFormToProduct(product, { skipValidation: true });
    await cloneOnlineProductAsNewLink(product, { closeEditor: true });
  } catch (error) {
    Toast.show('读取当前编辑内容失败：' + (error?.message || error), 'error');
  }
}

async function cloneOnlineProductAsNewLink(p, options = {}) {
  const confirmRes = await Modal.confirm(
    `将以「${(p.title || '').slice(0, 30)}」为模板创建一条新的采集记录。` +
    `\n\n原在线商品不会被修改。创建后可编辑商品信息，并在发布时选择新的 Ozon 店铺。`
  );
  if (!confirmRes) return;
  const targetStore = await chooseRelistTargetStore(p.storeId || p.store_id);
  if (!targetStore) return;
  const skus = (Array.isArray(p.skus) ? p.skus : []).filter(sku => sku && typeof sku === 'object').map(sku => {
    const copy = { ...sku };
    delete copy.sku;
    delete copy.skuId;
    return copy;
  });
  const payload = {
    title: p.title || '',
    platform: 'ozon',
    description: p.description || '',
    price: p.price || '',
    oldPrice: p.oldPrice || p.originalPrice || '',
    images: Array.isArray(p.images) ? p.images : [],
    detailImages: Array.isArray(p.detailImages) ? p.detailImages : [],
    category: p.category || '',
    categoryId: p.categoryId || '',
    typeId: p.typeId || '',
    descriptionCategoryId: p.descriptionCategoryId || '',
    weight: p.weight || '',
    length: p.length || '',
    width: p.width || '',
    height: p.height || '',
    productId: '',
    mergeCode: p.mergeNo || p.mergeCode || '',
    attributes: Array.isArray(p.attributes) ? p.attributes : [],
    skuAttrs: Array.isArray(p.skuAttrs) ? p.skuAttrs : [],
    skus,
    storeId: targetStore.id,
    store: targetStore.name,
  };
  if (options.closeEditor) await Modal.forceClose();
  let resp;
  try {
    resp = await Api.collectProduct(payload);
  } catch (error) {
    Toast.show('创建新记录失败：' + (error?.message || error), 'error');
    return;
  }
  if (resp && resp.code === 200) {
    const newId = resp.data?.id || resp.data?.product?.id || resp.data;
    Toast.show('重制副本已创建：可在新编辑器中使用 AI 改图、生成商品卡并发布', 'success');
    // 跳转采集箱：列表会自动加载。轮询等待新记录进入 allProducts 后自动打开编辑；
    // 避免直接 editProduct(newId) 时内存中还没有该记录而报「未找到商品」。
    if (newId) {
      if (!location.hash.startsWith('#/collect')) Router.navigate('/collect');
      const tryOpen = (attempt) => {
        try {
          const found = typeof allProducts !== 'undefined' && allProducts.find(p => String(p.id) === String(newId));
          if (found && typeof editProduct === 'function') {
            editProduct(String(newId), { skipFetch: true });
            return;
          }
        } catch (e) { /* 继续等待下一轮 */ }
        if (attempt < 25) setTimeout(() => tryOpen(attempt + 1), 400);
      };
      setTimeout(() => tryOpen(0), 800);
    } else {
      Router.navigate('/collect');
    }
  } else {
    Toast.show(resp?.msg || '创建新记录失败', 'error');
  }
}

async function chooseRelistTargetStore(sourceStoreId) {
  let stores = window._cachedStores || [];
  if (!stores.length) {
    let response;
    try {
      response = await Api.getStores();
    } catch (error) {
      Toast.show('读取店铺列表失败：' + (error?.message || error), 'error');
      return null;
    }
    if (response?.code !== 200) {
      Toast.show(response?.msg || '读取店铺列表失败', 'error');
      return null;
    }
    stores = response.data?.list || [];
    window._cachedStores = stores;
  }
  const sourceIds = new Set([String(sourceStoreId || '')]);
  const candidates = stores.filter(store => {
    if (store.authStatus !== 'active') return false;
    const ids = [store.id, store.store_id, store.storeId].filter(value => value != null).map(String);
    return !ids.some(id => sourceIds.has(id));
  });
  if (!candidates.length) {
    Toast.show('没有可用的新店铺；请先绑定并授权另一家 Ozon 店铺', 'warning');
    return null;
  }
  return new Promise(resolve => {
    const defaultStore = candidates[0];
    const defaultId = defaultStore.id || defaultStore.store_id || defaultStore.storeId;
    const options = candidates.map((store, index) => {
      const id = store.id || store.store_id || store.storeId;
      return `<label class="store-publish-option" style="display:flex;align-items:center;gap:10px;padding:12px 14px;border:1px solid var(--border-color);border-radius:8px;cursor:pointer;">
        <input type="radio" name="relistTargetStore" value="${escapeAttr(id)}" ${index === 0 ? 'checked' : ''}>
        <span>${escapeHtml(store.alias || store.name || store.store_id || id)}</span>
      </label>`;
    }).join('');
    Modal.show({
      title: '选择新店铺',
      size: 'sm',
      body: `<p style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">复制后的商品将关联到所选店铺，来源店铺已排除。</p><div style="display:flex;flex-direction:column;gap:8px;">${options}</div>`,
      footer: [
        { text: '取消', class: 'btn-ghost', onClick: () => { Modal.close(); resolve(null); } },
        { text: '确定', class: 'btn-primary', onClick: () => {
          const selected = document.querySelector('input[name="relistTargetStore"]:checked');
          const store = candidates.find(item => String(item.id || item.store_id || item.storeId) === selected?.value);
          Modal.close();
          resolve(store ? {
            id: store.id || store.store_id || store.storeId,
            name: store.alias || store.name || store.store_id || '',
          } : { id: defaultId, name: defaultStore.alias || defaultStore.name || '' });
        } },
      ],
    });
  });
}

/** 编辑商品 */
async function editOnlineProduct(id) {
  let p = allOnlineProducts.find(x => x.id === id);
  if (!p) return;
  try {
    const fresh = await Api.getOnlineProduct(id);
    if (fresh && fresh.code === 200 && fresh.data) p = fresh.data;
  } catch (e) {
    Toast.show('读取在线商品最新数据失败', 'error');
    return;
  }
  Modal.show({
    title: '快速修改价格与库存 - ' + p.sku,
    size: 'xl',
    body: `
      <div class="edit-main"><div class="edit-tabs">
        <button class="edit-tab active" data-tab="basic" onclick="switchEditTab(this)">基本信息</button>
        <button class="edit-tab" data-tab="price" onclick="switchEditTab(this)">价格与库存</button>
      </div>
      <div class="edit-content scroll-tabs-mode">
        <div class="edit-panel active" data-panel="basic">
          <div class="edit-panel-box">
            <div class="form-group"><label class="form-label">产品标题（来自 Ozon）</label><textarea class="form-textarea" rows="3" disabled>${p.title}</textarea></div>
            <div class="form-row-2">
              <div class="form-group"><label class="form-label">SKU</label><input type="text" class="form-input" value="${p.sku}" disabled style="background:var(--bg-input)"></div>
              <div class="form-group"><label class="form-label">分组</label><select class="form-select" id="epGroup"><option${p.group==='3C数码'?' selected':''}>3C数码</option><option${p.group==='家居家纺'?' selected':''}>家居家纺</option><option${p.group==='穿戴设备'?' selected':''}>穿戴设备</option><option${p.group==='美妆工具'?' selected':''}>美妆工具</option></select></div>
            </div>
            <div class="form-row-2">
              <div class="form-group"><label class="form-label">所属店铺</label><input class="form-input" value="${p.store || '-'}" disabled></div>
              <div class="form-group"><label class="form-label">产品状态（来自 Ozon）</label><div style="padding-top:8px">${getOnlineStatusBadge(p.status)}</div></div>
            </div>
            <div class="form-group"><label class="form-label">备注</label><textarea class="form-textarea" id="epNote" rows="2">${p.note||''}</textarea></div>
          </div>
        </div>
        <div class="edit-panel" data-panel="price">
          <div class="edit-panel-box">
            <div class="form-row-2">
              <div class="form-group"><label class="form-label">售价 (₽)</label><input type="number" class="form-input" id="epPrice" value="${p.price}"></div>
              <div class="form-group"><label class="form-label">原价 (₽)</label><input type="number" class="form-input" id="epOrigPrice" value="${p.originalPrice||''}"></div>
            </div>
            <div class="form-row-2">
              <div class="form-group"><label class="form-label">库存</label><input type="number" class="form-input" id="epStock" value="${p.stock}"></div>
              <div class="form-group"><label class="form-label">类目</label><input type="text" class="form-input" value="${p.category}" disabled style="background:var(--bg-input)"></div>
            </div>
          </div>
        </div>
      </div></div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost' },
      { text: '保存修改', class: 'btn-primary', onClick: async () => {
        if (isBackendUnavailable) {
          Toast.show('后端未连接，无法保存在线商品', 'warning');
          return;
        }
        const newGroup = document.getElementById('epGroup').value;
        const newNote = document.getElementById('epNote').value || '';
        const newPrice = Number(document.getElementById('epPrice').value);
        const newOrigPrice = Number(document.getElementById('epOrigPrice').value || 0);
        const newStock = Number(document.getElementById('epStock').value);
        if (!Number.isFinite(newPrice) || newPrice < 0 || !Number.isInteger(newStock) || newStock < 0) {
          Toast.show('价格和库存格式不正确', 'warning'); return;
        }

        // 记录旧值（用于检测价格/库存变化）
        const oldPrice = p.price;
        const oldOrigPrice = p.originalPrice;
        const oldStock = p.stock;

        try {
          const localRes = await Api.updateOnlineProduct(p.id, { group: newGroup, note: newNote });
          if (!localRes || localRes.code !== 200) throw new Error(localRes?.msg || '本地信息保存失败');
          if (newPrice !== oldPrice || newOrigPrice !== oldOrigPrice) {
            const priceRes = await Api.updateOnlineProductPrice(p.id, { price: newPrice, oldPrice: newOrigPrice });
            if (!priceRes || priceRes.code !== 200) throw new Error(priceRes?.msg || 'Ozon 价格更新失败');
          }
          if (newStock !== oldStock) {
            const stockRes = await Api.updateOnlineProductStock(p.id, { stock: newStock });
            if (!stockRes || stockRes.code !== 200) throw new Error(stockRes?.msg || 'Ozon 库存更新失败');
          }
          await Api.syncOnlineProduct(p.id);
          Modal.close();
          await reloadOnlineProducts();
          filterOnlineProducts();
          Toast.show('商品已保存，并从 Ozon 回读确认', 'success');
        } catch (e) {
          Toast.show('保存失败：' + (e?.message || e), 'error');
        }
      }},
    ],
  });
}

/** 同步商品 - 从 Ozon 拉取单个商品最新信息并更新本地 */
async function syncProduct(id) {
  const p = allOnlineProducts.find(x => x.id === id);
  if (!p) return;
  if (isBackendUnavailable) {
    Toast.show('后端未连接，无法同步', 'warning');
    return;
  }
  Toast.show(`正在同步「${p.sku}」的最新信息...`,'info');
  try {
    const res = await Api.syncOnlineProduct(id);
    if (res && res.code === 200) {
      // 用返回的更新数据替换本地记录
      const idx = allOnlineProducts.findIndex(x => x.id === id);
      if (idx >= 0 && res.data) allOnlineProducts[idx] = res.data;
      filterOnlineProducts();
      Toast.show('商品信息同步成功','success');
    } else {
      Toast.show('同步失败：' + (res && res.msg ? res.msg : '未知错误'), 'error');
    }
  } catch (e) {
    Toast.show('同步异常：' + (e && e.message ? e.message : e), 'error');
  }
}

/** 创建产品 */
function createProduct() {
  Modal.show({title:'创建新产品',size:'lg',body:`
    <div class="form-group"><label class="form-label">产品标题 <span class="required">*</span></label><textarea class="form-textarea" id="newProdTitle" rows="3" placeholder="输入产品标题..."></textarea></div>
    <div class="form-row-2"><div class="form-group"><label class="form-label">售价 (₽)</label><input type="number" class="form-input" id="newProdPrice"></div><div class="form-group"><label class="form-label">原价 (₽)</label><input type="number" class="form-input" id="newProdOrigPrice"></div></div>
    <div class="form-row-2"><div class="form-group"><label class="form-label">分组</label><select class="form-select" id="newProdGroup"><option>3C数码</option><option>家居家纺</option><option>穿戴设备</option></select></div><div class="form-group"><label class="form-label">店铺</label><select class="form-select" id="newProdStore"><option>Ozon v2.0</option><option>Ozon v3.0</option></select></div></div>
    <div class="form-row-2"><div class="form-group"><label class="form-label">库存</label><input type="number" class="form-input" id="newProdStock" value="100"></div><div class="form-group"><label class="form-label">类目</label><input type="text" class="form-input" id="newProdCat" placeholder="如：宠物用品|美容护理"></div></div>
  `,footer:[
    {text:'取消',class:'btn-ghost'},
    {text:'创建并发布',class:'btn-primary',onClick:()=>{
      const title=document.getElementById('newProdTitle').value.trim();
      if(!title){Toast.show('请输入产品标题','warning');return;}
      const np={id:'OP-'+Date.now(),sku:'OZ-NEW-'+String(allOnlineProducts.length+1).padStart(3,'0'),title:title,image:'',group:document.getElementById('newProdGroup').value,rating:'B',status:'onsale',price:parseInt(document.getElementById('newProdPrice').value)||0,originalPrice:parseInt(document.getElementById('newProdOrigPrice').value)||0,sales:0,stock:parseInt(document.getElementById('newProdStock').value)||0,category:document.getElementById('newProdCat').value||'未分类',store:document.getElementById('newProdStore').value,publisher:'当前用户',time:new Date().toLocaleString('zh-CN').replace(/\//g,'-'),note:''};
      allOnlineProducts.unshift(np);Modal.close();resetOnlineFilters();Toast.show('产品创建成功','success');
    }},
  ]});
}

/** 导入导出 */
function importProducts(){Toast.show('导入功能开发中...','info');}
function exportProducts(){
  const csv=['ID,SKU,标题,分组,内容评分,状态,售价,原价,销量,库存,店铺,人员,时间'];
  filteredOnline.forEach(p=>csv.push([p.id,p.sku,p.title.replace(/,/g,' '),p.group,p.contentScore ?? '',p.status,p.price,p.originalPrice,p.sales,p.stock,p.store,p.publisher,p.time].join(',')));
  const blob=new Blob(['\uFEFF'+csv.join('\n')],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='在线商品_'+new Date().toISOString().slice(0,10)+'.csv';a.click();URL.revokeObjectURL(a.href);
  Toast.show('导出成功：'+filteredOnline.length+'条数据','success');
}

/** 全局同步 */
function syncAllProducts(){Toast.show('正在同步所有商品数据...','info');setTimeout(()=>{syncContentRating();},500);}

/** 更多菜单 */
function toggleOnlineMore(btn){const row=btn.closest('tr');const menu=row.querySelector('.more-actions-menu');document.querySelectorAll('.more-actions-menu').forEach(m=>{if(m!==menu)m.style.display='none';});menu.style.display=menu.style.display==='none'?'block':'none';}

/** 查看详情 */
function viewProductDetail(id){
  const p=allOnlineProducts.find(x=>x.id===id);if(!p)return;
  Modal.show({title:'商品详情 - '+p.sku,size:'lg',body:`<div class="store-detail-grid"><div class="detail-item"><span class="detail-label">ID</span><span class="detail-value mono">${p.id}</span></div><div class="detail-item"><span class="detail-label">SKU</span><span class="detail-value mono">${p.sku}</span></div><div class="detail-item full-width"><span class="detail-label">标题</span><span class="detail-value">${p.title}</span></div><div class="detail-item"><span class="detail-label">内容评分</span><span class="detail-value">${renderContentScore(p.contentScore)}</span></div><div class="detail-item"><span class="detail-label">状态</span><span class="detail-value">${getOnlineStatusBadge(p.status)}</span></div><div class="detail-item"><span class="detail-label">售价</span><span class="detail-value highlight">₽${Number(p.price||0).toLocaleString()}</span></div><div class="detail-item"><span class="detail-label">库存</span><span class="detail-value">${p.stock}</span></div><div class="detail-item"><span class="detail-label">店铺</span><span class="detail-value">${p.store}</span></div><div class="detail-item full-width"><span class="detail-label">Ozon 原始状态</span><span class="detail-value mono">${p.ozonStatus||'-'}</span></div><div class="detail-item full-width"><span class="detail-label">备注</span><span class="detail-value">${p.note||'-'}</span></div></div>`,footer:[{text:'关闭',class:'btn-ghost',onClick:()=>Modal.close()}]});
}

/** 改变状态 */
async function changeStatus(id, status) {
  const p = allOnlineProducts.find(x => x.id === id);
  if (!p) return;
  if (isBackendUnavailable) {
    Toast.show('后端未连接，无法更新状态', 'warning');
    return;
  }
  p.status = status;
  filterOnlineProducts();
  Toast.show('状态已更新', 'success');
  try {
    const res = await Api.updateOnlineProduct(id, { status });
    if (res && res.code === 200 && res.data) {
      const idx = allOnlineProducts.findIndex(x => x.id === id);
      if (idx >= 0) allOnlineProducts[idx] = res.data;
      filterOnlineProducts();
    } else {
      console.warn('[在线商品] 状态更新后端返回异常:', res);
    }
  } catch (e) {
    console.warn('[在线商品] 状态更新后端失败:', e);
  }
}

/** 删除 */
function deleteOnlineProduct(id) {
  const p = allOnlineProducts.find(x => x.id === id);
  if (!p) return;
  Modal.confirm('确定删除商品「' + p.sku + '」？不可恢复。').then(async (ok) => {
    if (!ok) return;
    if (isBackendUnavailable) {
      Toast.show('后端未连接，无法删除商品', 'warning');
      return;
    }
    try {
      const res = await Api.deleteOnlineProduct(id);
      if (!res || res.code !== 200) {
        Toast.show('删除失败：' + (res && res.msg ? res.msg : '未知错误'), 'error');
        return;
      }
    } catch (e) {
      Toast.show('删除异常：' + (e && e.message ? e.message : e), 'error');
      return;
    }
    allOnlineProducts = allOnlineProducts.filter(x => x.id !== id);
    filterOnlineProducts();
    Toast.show('商品已删除', 'success');
  });
}
function batchDeleteOnline() {
  const ids = getSelectedOnlineIds();
  if (!ids.length) { Toast.show('请先选择商品', 'warning'); return; }
  Modal.confirm('确定删除选中的' + ids.length + '个商品？').then(async (ok) => {
    if (!ok) return;
    if (isBackendUnavailable) {
      Toast.show('后端未连接，无法删除商品', 'warning');
      return;
    }
    try {
      const res = await Api.batchDeleteOnlineProducts(ids);
      if (!res || res.code !== 200) {
        Toast.show('批量删除失败：' + (res && res.msg ? res.msg : '未知错误'), 'error');
        return;
      }
    } catch (e) {
      Toast.show('批量删除异常：' + (e && e.message ? e.message : e), 'error');
      return;
    }
    allOnlineProducts = allOnlineProducts.filter(p => !ids.includes(p.id));
    filterOnlineProducts();
    Toast.show('已删除' + ids.length + '个商品', 'success');
  });
}

// ===== 市场情报（从 JLC 工作台迁移到 ERP 在线商品） =====

let _marketIntelLoaded = false;

function showMarketIntelligence() {
  const panel = document.getElementById('marketIntelPanel');
  const batch = document.getElementById('batchActionBar');
  const table = document.getElementById('onlineTable');
  const tableWrap = table ? table.closest('.collect-table-wrap') : null;
  const footer = document.getElementById('onlineFooterBar');
  if (batch) batch.style.display = 'none';
  if (tableWrap) tableWrap.style.display = 'none';
  if (footer) footer.style.display = 'none';
  if (panel) panel.style.display = '';
  if (!_marketIntelLoaded) loadMarketIntel();
}

function hideMarketIntelligence() {
  const panel = document.getElementById('marketIntelPanel');
  const batch = document.getElementById('batchActionBar');
  const table = document.getElementById('onlineTable');
  const tableWrap = table ? table.closest('.collect-table-wrap') : null;
  const footer = document.getElementById('onlineFooterBar');
  if (panel) panel.style.display = 'none';
  if (batch) batch.style.display = '';
  if (tableWrap) tableWrap.style.display = '';
  if (footer) footer.style.display = 'flex';
}

async function loadMarketIntel() {
  _marketIntelLoaded = true;
  try {
    const res = await API.get('/api/market-intelligence/status');
    const d = (res && res.code === 200 && res.data) ? res.data : null;
    const categories = (d && Array.isArray(d.categories)) ? d.categories : [];
    const noticeEl = document.getElementById('marketIntelNotice');
    if (noticeEl) noticeEl.textContent = (d && d.notice) ? d.notice : '';
    const catSel = document.getElementById('marketCategorySelect');
    if (catSel) {
      catSel.innerHTML = categories.map(function (c) {
        return '<option value="' + escapeAttr(c.key) + '">' + escapeHtml(c.name_zh || c.name_ru || c.key) + '</option>';
      }).join('');
    }
  } catch (e) {
    console.warn('[市场情报] status 加载失败:', e);
  }
  loadMarketIntelProducts();
  loadMarketIntelKeywords();
  loadMarketIntelSearchVisibility();
}

async function loadMarketIntelProducts() {
  const grid = document.getElementById('marketIntelGrid');
  if (!grid) return;
  const category = document.getElementById('marketCategorySelect')?.value || 'home';
  const ranking = document.getElementById('marketRankingSelect')?.value || 'hot';
  grid.innerHTML = '<div style="padding:20px;color:#9ca3af;font-size:13px;grid-column:1/-1;">加载中...</div>';
  try {
    const res = await API.get('/api/market-intelligence/products', { ranking, category, period: 30, page: 1, page_size: 24 });
    const d = (res && res.code === 200 && res.data) ? res.data : null;
    const items = (d && d.items) || [];
    if (!items.length) {
      grid.innerHTML = '<div style="padding:30px;color:#9ca3af;font-size:13px;grid-column:1/-1;text-align:center;">' + ((d && d.notice) || '暂无市场数据') + '</div>';
      return;
    }
    grid.innerHTML = items.map(function (p) {
      const img = (p.image_url && p.image_url !== 'unknown') ? p.image_url : '';
      const facts = p.facts || {};
      const price = facts.ordered_amount_rub || '';
      const sold = facts.ordered_units || '';
      const fbs = (p.fbs_assessment && p.fbs_assessment.label) ? p.fbs_assessment.label : '';
      const imgHtml = img
        ? '<img src="' + escapeAttr(img) + '" alt="" loading="lazy" referrerpolicy="no-referrer" style="width:100%;height:100%;object-fit:cover;" onerror="this.remove();">'
        : '<span style="color:#d1d5db;font-size:12px;">无图</span>';
      let priceHtml = '';
      if (price !== '') {
        priceHtml = '<span style="font-size:13px;font-weight:700;color:#dc2626;">₽' + Number(price).toLocaleString('ru-RU') + '</span>';
      }
      let heatHtml = '';
      if (typeof p.heat_index === 'number') {
        heatHtml = '<div style="font-size:11px;color:#9ca3af;">热度 ' + p.heat_index + '</div>';
      }
      let fbsHtml = '';
      if (fbs) {
        fbsHtml = '<div style="font-size:11px;color:#6b7280;">FBS适配: ' + escapeHtml(fbs) + '</div>';
      }
      return ''
        + '<div style="border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;background:#fff;display:flex;flex-direction:column;">'
        + '<div style="height:140px;background:#f3f4f6;display:flex;align-items:center;justify-content:center;overflow:hidden;">' + imgHtml + '</div>'
        + '<div style="padding:10px;display:flex;flex-direction:column;gap:6px;flex:1;">'
        + '<div style="font-size:13px;font-weight:600;color:#111827;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:36px;" title="' + escapeAttr(p.title_ru || p.title_zh || '') + '">' + escapeHtml(p.title_ru || p.title_zh || '-') + '</div>'
        + '<div style="display:flex;justify-content:space-between;align-items:center;">'
        + '<span style="font-size:12px;color:#6b7280;">销量 ' + (sold === '' ? '-' : sold) + '</span>'
        + priceHtml
        + '</div>'
        + heatHtml
        + fbsHtml
        + '</div>'
        + '</div>';
    }).join('');
  } catch (e) {
    console.warn('[市场情报] products 加载失败:', e);
    grid.innerHTML = '<div style="padding:20px;color:#dc2626;font-size:13px;grid-column:1/-1;">市场数据加载失败</div>';
  }
}

async function loadMarketIntelKeywords() {
  const box = document.getElementById('marketIntelKeywords');
  if (!box) return;
  const category = document.getElementById('marketCategorySelect')?.value || '';
  try {
    const res = await API.get('/api/market-intelligence/keywords', { category, limit: 12 });
    const d = (res && res.code === 200 && res.data) ? res.data : null;
    const items = (d && d.items) || [];
    if (!items.length) {
      box.innerHTML = '<div style="font-size:13px;color:#9ca3af;">暂无关键词数据</div>';
      return;
    }
    box.innerHTML = '<div style="font-weight:700;font-size:13px;margin-bottom:8px;color:#111827;">近7天热搜关键词</div><div>'
      + items.map(function (k) {
          const pop = (k.metrics && k.metrics.popularity != null) ? k.metrics.popularity : '';
          const sub = pop !== '' ? ' <span style="color:#9ca3af;">' + pop + '</span>' : '';
          return '<span style="display:inline-block;padding:4px 10px;margin:4px;border-radius:14px;background:#f3f4f6;font-size:12px;color:#111827;">'
            + escapeHtml(k.keyword_ru || k.keyword || '')
            + (k.keyword_zh ? ' <span style="color:#6b7280;">' + escapeHtml(k.keyword_zh) + '</span>' : '')
            + sub
            + '</span>';
        }).join('')
      + '</div>';
  } catch (e) {
    console.warn('[市场情报] keywords 加载失败:', e);
    box.innerHTML = '<div style="font-size:13px;color:#dc2626;">关键词加载失败</div>';
  }
}

async function loadMarketIntelSearchVisibility() {
  const box = document.getElementById('marketIntelSearchVisibility');
  if (!box) return;
  try {
    const res = await API.get('/api/market-intelligence/search-visibility/latest');
    const d = (res && res.code === 200 && res.data) ? res.data : null;
    if (!d || !d.available) {
      box.innerHTML = '<div style="font-size:13px;color:#9ca3af;">' + ((d && d.notice) || '暂无搜索可见度方案（点击同步后生成）') + '</div>';
      return;
    }
    const summary = d.summary || {};
    const actions = d.actions || [];
    // 汇总商品生成的关键词证据(top_queries)：取前 60 条去重
    const seen = {};
    const kwItems = [];
    for (let i = 0; i < actions.length && kwItems.length < 60; i++) {
      const ev = (actions[i] && actions[i].evidence) || {};
      const rows = ev.top_queries || [];
      for (let j = 0; j < rows.length && kwItems.length < 60; j++) {
        const q = rows[j];
        const key = String(q.query || '').toLowerCase();
        if (!key || seen[key]) continue;
        seen[key] = 1;
        kwItems.push(q);
      }
    }
    const head = '<div style="font-weight:700;font-size:13px;margin-bottom:8px;color:#111827;">搜索可见度关键词方案'
      + ' <span style="color:#6b7280;font-weight:400;">(' + (summary.products || 0) + ' 个商品)</span></div>';
    if (!kwItems.length) {
      box.innerHTML = head + '<div style="font-size:13px;color:#9ca3af;">该方案暂无可展示的搜索词证据</div>';
      return;
    }
    box.innerHTML = head + '<div>'
      + kwItems.map(function (k) {
          const imp = (k.impressions != null) ? k.impressions : ((k.metrics && k.metrics.impressions != null) ? k.metrics.impressions : '');
          const sub = imp !== '' ? ' <span style="color:#9ca3af;">' + imp + '</span>' : '';
          return '<span style="display:inline-block;padding:4px 10px;margin:4px;border-radius:14px;background:#eef2ff;font-size:12px;color:#111827;">'
            + escapeHtml(k.query || '') + sub
            + '</span>';
        }).join('')
      + '</div>';
  } catch (e) {
    console.warn('[市场情报] search-visibility 加载失败:', e);
    box.innerHTML = '<div style="font-size:13px;color:#dc2626;">搜索可见度方案加载失败</div>';
  }
}

// ===== 初始化 =====

async function initOnlinePage(){
  allOnlineProducts = [];
  isBackendUnavailable = true;
  filteredOnline = [...allOnlineProducts];
  renderOnlineTable();
  loadOnlineStoreFilter(); // 多店铺：填充选店下拉
  await reloadOnlineProducts();
  // 点击空白处关闭更多菜单
  document.addEventListener('click',(e)=>{if(!e.target.closest('.more-action')&&!e.target.closest('.more-actions-menu'))document.querySelectorAll('.more-actions-menu').forEach(m=>m.style.display='none');});
}

Router.register('/online-products',(route)=>{const html=renderOnlineProductsPage(route);requestAnimationFrame(()=>{initOnlinePage();});return html;});

document.addEventListener('DOMContentLoaded',()=>{
  setTimeout(()=>{
    if(window.location.hash==='#/online-products'||Router.currentRoute?.path==='/online-products')initOnlinePage();
  },150);
});

