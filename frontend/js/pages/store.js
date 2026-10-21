/**
 * 店铺绑定管理页 - Store Management Page
 * 支持店铺授权、币种管理、分组管理、批量操作等功能
 * 数据来源：后端 SQLite 数据库 API
 */
/** 当前用户是否为管理员（店铺写操作/用户管理仅管理员可用） */
function _storeIsAdmin() {
  return Store?.getUser()?.role === 'admin';
}

function renderStorePage(route) {
  const isAdmin = _storeIsAdmin();
  return `
    <div style="animation: pageEnter 0.35s ease;">
      <!-- 页面标题 -->
      <div class="store-page-header">
        <div>
          <h2 class="store-page-title">店铺管理</h2>
          <p class="store-page-desc">${isAdmin ? '管理所有已绑定的店铺账号，支持授权、分组、币种配置' : '查看分配给您的店铺（只读）'}</p>
        </div>
        <div class="store-header-actions">
          ${isAdmin ? `
          <button class="btn btn-sm btn-ghost" onclick="showUserManager()" title="创建业务员账号并绑定店铺">
            <i data-lucide="users" style="width:14px;height:14px;"></i> 用户管理
          </button>
          <button class="btn btn-sm btn-ghost" onclick="showAddGroupDialog()">
            <i data-lucide="folder-plus" style="width:14px;height:14px;"></i> 新建分组
          </button>
          <button class="btn btn-primary btn-sm" onclick="showAddStoreDialog()">
            <i data-lucide="plus" style="width:14px;height:14px;"></i> 添加店铺
          </button>` : ''}
        </div>
      </div>

      <!-- 筛选工具栏 -->
      <div class="store-toolbar">
        <div class="store-toolbar-left">
          <!-- 分组筛选 -->
          <select class="form-select filter-select" id="filterGroup" onchange="filterStores()">
            <option value="">全部分组</option>
          </select>
          <!-- 授权状态 -->
          <select class="form-select filter-select" id="filterAuthStatus" onchange="filterStores()">
            <option value="">全部状态</option>
            <option value="active">已授权</option>
            <option value="expired">已过期</option>
            <option value="pending">待授权</option>
            <option value="disabled">已禁用</option>
          </select>
          <!-- 通知推送 -->
          <select class="form-select filter-select" id="filterNotifyStatus" onchange="filterStores()">
            <option value="">全部通知</option>
            <option value="on">已开启</option>
            <option value="off">未开启</option>
          </select>
          <!-- 关键词搜索 -->
          <div class="search-input-group">
            <input type="text" class="form-input search-input" placeholder="搜索店铺ID/别名/关键词..." id="storeSearchInput" onkeydown="if(event.key==='Enter')filterStores()">
          </div>
        </div>
        <div class="store-toolbar-right">
          <button class="btn btn-sm btn-primary" onclick="filterStores()">
            <i data-lucide="search" style="width:14px;height:14px;"></i> 搜索
          </button>
          <button class="btn btn-sm btn-ghost" onclick="resetStoreFilters()">
            <i data-lucide="rotate-ccw" style="width:14px;height:14px;"></i> 重置
          </button>
        </div>
      </div>

      <!-- 表格区域 -->
      <div class="store-table-wrap">
        <table class="collect-table" id="storeTable">
          <thead>
            <tr>
              ${isAdmin ? '<th><input type="checkbox" class="table-check-all" onclick="toggleSelectAllStores(this)"></th>' : ''}
              <th>序号</th>
              <th>店铺ID</th>
              <th>今日可刊登数</th>
              <th>币种</th>
              <th>分组</th>
              <th>店铺别名</th>
              <th>归属</th>
              <th>通知推送</th>
              <th>授权类型</th>
              <th>授权时间</th>
              <th>授权状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="storeTableBody"></tbody>
        </table>

        <!-- 空状态 -->
        <div class="empty-state" id="storeEmptyState" style="display:none;">
          <div class="empty-icon">&#128203;</div>
          <p>暂无店铺数据</p>
          <p style="font-size:12px;margin-top:4px;">${isAdmin ? '点击「添加店铺」开始绑定您的第一个店铺' : '请联系管理员为您分配店铺'}</p>
        </div>
      </div>

      <!-- 底部操作栏 -->
      <div class="store-footer-bar" id="storeFooterBar">
        <div class="store-footer-left">
          <span class="select-info">已选 <strong id="storeSelectedCount">0</strong> 条</span>
          ${isAdmin ? `
          <button class="btn btn-sm btn-ghost" onclick="batchSetGroup()">批量设分组</button>
          <button class="btn btn-sm btn-ghost" onclick="batchSetCurrency()">批量设币种</button>
          <button class="btn btn-sm btn-ghost danger" onclick="batchDeleteStores()">批量删除</button>` : ''}
        </div>
        <div class="store-footer-right">
          <span class="total-info">共 <strong id="storeTotalCount">0</strong> 个店铺</span>
        </div>
      </div>
    </div>
  `;
}

// ===== 数据层 =====

let allStores = [];
let filteredStores = [];
let storeGroups = [];

/** 从后端 API 加载店铺数据 */
async function loadStoresFromAPI() {
  const res = await Api.getStores();
  if (res.code === 200 && res.data) {
    allStores = res.data.list || [];
    filteredStores = [...allStores];
  } else {
    allStores = [];
    filteredStores = [];
  }
  return allStores;
}

/** 从后端 API 加载分组列表 */
async function loadGroupsFromAPI() {
  const res = await Api.getStoreGroups();
  if (res.code === 200 && res.data) {
    storeGroups = res.data;
    // 更新分组筛选下拉框
    const select = document.getElementById('filterGroup');
    if (select) {
      const current = select.value;
      select.innerHTML = '<option value="">全部分组</option>' +
        storeGroups.map(g => `<option value="${g}">${g}</option>`).join('');
      select.value = current;
    }
  }
}

// ===== 渲染层 =====

/** 渲染表格 */
function renderStoreTable() {
  const tbody = document.getElementById('storeTableBody');
  const emptyEl = document.getElementById('storeEmptyState');
  if (!tbody) return;
  const isAdmin = _storeIsAdmin();

  if (filteredStores.length === 0) {
    tbody.innerHTML = '';
    emptyEl.style.display = '';
    document.getElementById('storeFooterBar').style.display = 'none';
    return;
  }

  emptyEl.style.display = 'none';
  document.getElementById('storeFooterBar').style.display = 'flex';

  tbody.innerHTML = filteredStores.map((s, i) => `
    <tr data-id="${s.id}" data-pk="${s.id}">
      ${isAdmin ? `<td><input type="checkbox" class="table-check-item store-check" value="${s.id}"></td>` : ''}
      <td class="seq-cell">${i + 1}</td>
      <td><span class="store-id-text">${s.store_id || s.storeId || s.id}</span></td>
      <td class="limit-cell">${s.todayLimit > 0 ? s.todayLimit.toLocaleString() : '-'}</td>
      <td><span class="currency-badge">${s.currency}</span></td>
      <td><span class="group-badge">${s.storeGroup || s.group || ''}</span></td>
      <td><span class="alias-text">${s.alias}</span></td>
      <td>${s.accountId ? `<span class="owner-badge" title="归属业务员">${s.ownerName || '已绑定'}</span>` : '<span style="color:var(--text-tertiary);font-size:12px;">公共</span>'}</td>
      <td>${s.notifyOn ? '<span class="notify-badge on"><i data-lucide="bell-ring" style="width:12px;height:12px;"></i> 已开启</span>' : '<span class="notify-badge off">未开启</span>'}</td>
      <td><span class="auth-type-tag ${s.authType === 'API授权' ? 'api' : 'cookie'}">${s.authType}</span></td>
      <td class="time-cell">${s.authTime || ''}</td>
      <td>${getStoreStatusBadge(s.authStatus)}</td>
      <td>
        ${isAdmin ? `
        <div class="action-btns">
          <button class="action-link" onclick="showEditStoreDialog(${s.id})">编辑</button>
          <button class="action-link" onclick="refreshAuth(${s.id})">更新授权</button>
          <button class="action-link" onclick="openStoreOwnerDialog(${s.id})">${s.accountId ? '改绑定' : '绑定'}</button>
          <button class="action-link danger" onclick="deleteStore(${s.id})">删除</button>
        </div>` : '<span style="color:var(--text-tertiary);">-</span>'}
      </td>
    </tr>
  `).join('');

  // 更新计数
  document.getElementById('storeTotalCount').textContent = filteredStores.length;
  updateStoreSelection();

  if (window.lucide) lucide.createIcons();
}

/** 店铺状态徽章 */
function getStoreStatusBadge(status) {
  const map = {
    active: { text: '已授权', cls: 'status-active' },
    expired: { text: '已过期', cls: 'status-expired' },
    pending: { text: '待授权', cls: 'status-pending' },
    disabled: { text: '已禁用', cls: 'status-disabled' },
  };
  const item = map[status] || { text: status, cls: '' };
  return `<span class="store-status-badge ${item.cls}">${item.text}</span>`;
}

// ===== 筛选 & 搜索 =====

/** 筛选（从后端重新查询） */
async function filterStores() {
  const groupVal = document.getElementById('filterGroup')?.value || '';
  const statusVal = document.getElementById('filterAuthStatus')?.value || '';
  const notifyVal = document.getElementById('filterNotifyStatus')?.value || '';
  const keyword = (document.getElementById('storeSearchInput')?.value || '').trim();

  const res = await Api.getStores({
    group: groupVal,
    authStatus: statusVal,
    notify: notifyVal,
    keyword: keyword,
  });

  if (res.code === 200 && res.data) {
    filteredStores = res.data.list || [];
  } else {
    filteredStores = [];
  }

  renderStoreTable();
}

/** 重置筛选 */
async function resetStoreFilters() {
  const el = (id) => document.getElementById(id);
  if (el('filterGroup')) el('filterGroup').value = '';
  if (el('filterAuthStatus')) el('filterAuthStatus').value = '';
  if (el('filterNotifyStatus')) el('filterNotifyStatus').value = '';
  if (el('storeSearchInput')) el('storeSearchInput').value = '';
  await loadStoresFromAPI();
  renderStoreTable();
}

// ===== 选择 & 批量操作 =====

/** 全选/取消全选 */
function toggleSelectAllStores(checkbox) {
  const items = document.querySelectorAll('.store-check');
  items.forEach(item => item.checked = checkbox.checked);
  updateStoreSelection();
}

/** 更新选择计数 */
function updateStoreSelection() {
  const checked = document.querySelectorAll('.store-check:checked');
  document.getElementById('storeSelectedCount').textContent = checked.length;
  const allCheck = document.querySelector('.table-check-all');
  const totalItems = document.querySelectorAll('.store-check');
  if (allCheck) allCheck.checked = totalItems.length > 0 && checked.length === totalItems.length;
}

/** 批量设置分组 */
function batchSetGroup() {
  const ids = getSelectedStoreIds();
  if (ids.length === 0) { Toast.show('请先选择店铺', 'warning'); return; }
  Modal.show({
    title: '批量设置分组',
    body: `
      <p style="font-size:13px;color:#6B7280;margin-bottom:12px;">已选择 <b>${ids.length}</b> 个店铺</p>
      <div class="form-group">
        <label class="form-label">目标分组</label>
        <select class="form-select" id="batchGroupSelect">
          ${storeGroups.map(g => `<option value="${g}">${g}</option>`).join('')}
        </select>
      </div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost' },
      { text: '确认', class: 'btn-primary', onClick: async () => {
        const group = document.getElementById('batchGroupSelect').value;
        const res = await Api.batchSetGroup(ids, group);
        if (res.code === 200) {
          Modal.close();
          await loadStoresFromAPI();
          await loadGroupsFromAPI();
          renderStoreTable();
          Toast.show(res.msg || `已将 ${ids.length} 个店铺移至「${group}」`, 'success');
        } else {
          Toast.show(res.msg || '操作失败', 'error');
        }
      }},
    ],
  });
}

/** 批量设置币种 */
function batchSetCurrency() {
  const ids = getSelectedStoreIds();
  if (ids.length === 0) { Toast.show('请先选择店铺', 'warning'); return; }
  Modal.show({
    title: '批量设置币种',
    body: `
      <p style="font-size:13px;color:#6B7280;margin-bottom:12px;">已选择 <b>${ids.length}</b> 个店铺</p>
      <div class="form-group">
        <label class="form-label">币种</label>
        <select class="form-select" id="batchCurrencySelect">
          <option value="RUB">RUB - 俄罗斯卢布</option>
          <option value="USD">USD - 美元</option>
          <option value="EUR">EUR - 欧元</option>
          <option value="CNY">CNY - 人民币</option>
        </select>
      </div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost' },
      { text: '确认', class: 'btn-primary', onClick: async () => {
        const cur = document.getElementById('batchCurrencySelect').value;
        const res = await Api.batchSetCurrency(ids, cur);
        if (res.code === 200) {
          Modal.close();
          await loadStoresFromAPI();
          renderStoreTable();
          Toast.show(res.msg || `已将 ${ids.length} 个店铺币种设置为「${cur}」`, 'success');
        } else {
          Toast.show(res.msg || '操作失败', 'error');
        }
      }},
    ],
  });
}

/** 批量删除 */
function batchDeleteStores() {
  const ids = getSelectedStoreIds();
  if (ids.length === 0) { Toast.show('请先选择店铺', 'warning'); return; }
  Modal.confirm(`确定要删除选中的 ${ids.length} 个店铺吗？此操作不可恢复。`).then(async (ok) => {
    if (ok) {
      const res = await Api.batchDeleteStores(ids);
      if (res.code === 200) {
        await loadStoresFromAPI();
        await loadGroupsFromAPI();
        renderStoreTable();
        Toast.show(res.msg || `已删除 ${ids.length} 个店铺`, 'success');
      } else {
        Toast.show(res.msg || '删除失败', 'error');
      }
    }
  });
}

/** 获取选中的店铺ID列表 */
function getSelectedStoreIds() {
  return [...document.querySelectorAll('.store-check:checked')].map(el => parseInt(el.value));
}

// ===== 单项操作 =====

/** 删除店铺 */
function deleteStore(pk) {
  const store = filteredStores.find(s => s.id === pk);
  if (!store) return;
  Modal.confirm(`确定要删除店铺「${store.alias}」吗？此操作不可恢复。`).then(async (ok) => {
    if (ok) {
      const res = await Api.deleteStore(pk);
      if (res.code === 200) {
        await loadStoresFromAPI();
        await loadGroupsFromAPI();
        renderStoreTable();
        Toast.show(`店铺「${store.alias}」已删除`, 'success');
      } else {
        Toast.show(res.msg || '删除失败', 'error');
      }
    }
  });
}

/** 更新授权 */
async function refreshAuth(pk) {
  const store = filteredStores.find(s => s.id === pk);
  if (!store) return;
  Toast.show(`正在更新「${store.alias}」的授权信息...`, 'info');
  const res = await Api.refreshStoreAuth(pk);
  if (res.code === 200) {
    await loadStoresFromAPI();
    renderStoreTable();
    Toast.show('授权更新成功', 'success');
  } else {
    Toast.show(res.msg || '授权更新失败', 'error');
  }
}

/** 显示编辑店铺弹窗 */
function showEditStoreDialog(pk) {
  const store = filteredStores.find(s => s.id === pk);
  if (!store) return;

  Modal.show({
    title: `编辑店铺 - ${store.alias}`,
    size: 'md',
    body: `
      <div class="add-auth-form">
        <div class="form-group auth-form-group">
          <label class="form-label auth-label">店铺别名</label>
          <input type="text" class="form-input auth-input" id="editAlias"
                 value="${store.alias || ''}" maxlength="50">
        </div>

        <div class="form-group auth-form-group">
          <label class="form-label auth-label">Client-Id</label>
          <input type="text" class="form-input auth-input" id="editClientId"
                 value="${store.clientId || ''}" maxlength="64">
        </div>

        <div class="form-group auth-form-group">
          <label class="form-label auth-label">Api-Key</label>
          <input type="password" class="form-input auth-input" id="editApiKey"
                 placeholder="输入新的 Api-Key 以更新" maxlength="128">
          <button type="button" class="toggle-pwd-btn" onclick="togglePwdVisibility('editApiKey',this)" title="显示/隐藏密码">
            <i data-lucide="eye" style="width:14px;height:14px;"></i>
          </button>
          <div class="field-notice">
            <span class="notice-text" style="color:#6B7280;font-size:12px;">
              留空表示不修改；从 Ozon 卖家后台获取：设置 → API Keys
            </span>
          </div>
        </div>

        <div class="form-group auth-form-group">
          <label class="form-label auth-label">店铺币种</label>
          <select class="form-select auth-input" id="editCurrency">
            <option value="RUB" ${store.currency === 'RUB' ? 'selected' : ''}>RUB - 俄罗斯卢布</option>
            <option value="USD" ${store.currency === 'USD' ? 'selected' : ''}>USD - 美元</option>
            <option value="EUR" ${store.currency === 'EUR' ? 'selected' : ''}>EUR - 欧元</option>
            <option value="CNY" ${store.currency === 'CNY' ? 'selected' : ''}>CNY - 人民币</option>
            <option value="KZT" ${store.currency === 'KZT' ? 'selected' : ''}>KZT - 哈萨克斯坦坚戈</option>
            <option value="BYN" ${store.currency === 'BYN' ? 'selected' : ''}>BYN - 白俄罗斯卢布</option>
          </select>
        </div>

        <div class="form-group auth-form-group">
          <label class="form-label auth-label">授权状态</label>
          <select class="form-select auth-input" id="editAuthStatus">
            <option value="active" ${store.authStatus === 'active' ? 'selected' : ''}>已授权</option>
            <option value="pending" ${store.authStatus === 'pending' ? 'selected' : ''}>待授权</option>
            <option value="expired" ${store.authStatus === 'expired' ? 'selected' : ''}>已过期</option>
            <option value="disabled" ${store.authStatus === 'disabled' ? 'selected' : ''}>已禁用</option>
          </select>
        </div>
      </div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
      { text: '保存', class: 'btn-primary', id: 'btnEditStoreConfirm', onClick: () => submitEditStoreForm(pk) },
    ],
    onOpen: () => {
      if (window.lucide) lucide.createIcons();
    },
  });
}

/** 提交编辑店铺表单 */
async function submitEditStoreForm(pk) {
  const apiKey = document.getElementById('editApiKey').value.trim();
  const updateData = {
    alias: document.getElementById('editAlias').value.trim(),
    clientId: document.getElementById('editClientId').value.trim(),
    currency: document.getElementById('editCurrency').value,
    authStatus: document.getElementById('editAuthStatus').value,
  };

  // Api-Key 留空表示不修改
  if (apiKey) {
    updateData.apiKey = apiKey;
  }

  const confirmBtn = document.getElementById('btnEditStoreConfirm');
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.innerHTML = '<span class="loading-spinner-sm"></span> 保存中...';
  }

  const res = await Api.updateStore(pk, updateData);

  if (confirmBtn) {
    confirmBtn.disabled = false;
    confirmBtn.innerHTML = '保存';
  }

  if (res.code === 200) {
    Modal.close();
    await loadStoresFromAPI();
    renderStoreTable();
    Toast.show('店铺信息已更新', 'success');
  } else {
    Toast.show(res.msg || '保存失败', 'error');
  }
}

// ===== 弹窗操作 =====

/** 显示添加授权弹窗 */
function showAddStoreDialog() {
  Modal.show({
    title: '添加授权',
    size: 'md',
    body: `
      <!-- 顶部提示横幅 -->
      <div class="auth-tip-banner">
        <span class="auth-tip-text">提示：OZON除授权店铺外，还需要开启通知推送，可避免"订单自动同步延迟、打印面单慢、漏单"等问题。</span>
        <a class="auth-tip-link" href="#" onclick="Toast.show('打开帮助中心','info');return false;">帮助中心</a>
      </div>

      <!-- 表单区域 -->
      <div class="add-auth-form">
        <!-- 店铺ID -->
        <div class="form-group auth-form-group">
          <label class="form-label auth-label">店铺ID <span class="required">*</span></label>
          <input type="text" class="form-input auth-input" id="addStoreId"
                 placeholder="Ozon 平台店铺 ID" maxlength="20"
                 oninput="validateAuthField('storeId',this.value)">
          <div class="field-error" id="error_storeId"></div>
        </div>

        <!-- 店铺别名 -->
        <div class="form-group auth-form-group">
          <label class="form-label auth-label">店铺别名 <span class="required">*</span></label>
          <input type="text" class="form-input auth-input" id="addAlias"
                 placeholder="" maxlength="50"
                 oninput="validateAuthField('alias',this.value)">
          <div class="field-notice" id="notice_alias">
            <span class="notice-icon">&#9888;</span>
            <span class="notice-text">注意：生成API密钥时请选择"管理员"权限</span>
            <a class="notice-detail-link" href="#" onclick="Toast.show('查看权限详情','info');return false;">详情</a>
          </div>
          <div class="field-error" id="error_alias"></div>
        </div>

        <!-- 店铺币种 -->
        <div class="form-group auth-form-group">
          <label class="form-label auth-label">店铺币种 <span class="required">*</span></label>
          <select class="form-select auth-input" id="addCurrency" onchange="validateAuthField('currency',this.value)">
            <option value="">请选择店铺后台币种</option>
            <option value="RUB">RUB - 俄罗斯卢布</option>
            <option value="USD">USD - 美元</option>
            <option value="EUR">EUR - 欧元</option>
            <option value="CNY">CNY - 人民币</option>
            <option value="KZT">KZT - 哈萨克斯坦坚戈</option>
            <option value="BYN">BYN - 白俄罗斯卢布</option>
          </select>
          <div class="field-notice" id="notice_currency">
            <span class="notice-icon">&#9888;</span>
            <span class="notice-text">注意：请选择与店铺后台相同的币种，否则会影响发布</span>
            <a class="notice-detail-link" href="#" onclick="Toast.show('查看币种影响说明','info');return false;">详情</a>
          </div>
          <div class="field-error" id="error_currency"></div>
        </div>

        <!-- Client-Id -->
        <div class="form-group auth-form-group">
          <label class="form-label auth-label">Client-Id <span class="required">*</span></label>
          <input type="text" class="form-input auth-input" id="addClientId"
                 placeholder="" maxlength="64"
                 oninput="validateAuthField('clientId',this.value)">
          <div class="field-error" id="error_clientId"></div>
        </div>

        <!-- Api-Key -->
        <div class="form-group auth-form-group">
          <label class="form-label auth-label">Api-Key <span class="required">*</span></label>
          <input type="password" class="form-input auth-input" id="addApiKey"
                 placeholder="" maxlength="128"
                 oninput="validateAuthField('apiKey',this.value)">
          <button type="button" class="toggle-pwd-btn" onclick="togglePwdVisibility('addApiKey',this)" title="显示/隐藏密码">
            <i data-lucide="eye" style="width:14px;height:14px;"></i>
          </button>
          <div class="field-error" id="error_apiKey"></div>
        </div>
      </div>
    `,
    footer: [
      { text: '查看授权帮助', class: 'btn-link', onClick: () => Toast.show('正在打开授权帮助文档...', 'info') },
      { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
      { text: '确定', class: 'btn-primary', id: 'btnAddStoreConfirm', onClick: () => submitAddStoreForm() },
    ],
    onOpen: () => {
      if (window.lucide) lucide.createIcons();
      clearAllAuthErrors();
    },
  });
}

/** 验证单个字段 */
function validateAuthField(field, value) {
  const errorEl = document.getElementById(`error_${field}`);
  if (!errorEl) return true;
  errorEl.textContent = '';
  errorEl.classList.remove('show');

  const v = (value || '').trim();

  switch (field) {
    case 'storeId':
      if (!v) { showFieldError(field, '店铺ID不能为空'); return false; }
      break;
    case 'alias':
      if (!v) { showFieldError(field, '店铺别名不能为空'); return false; }
      if (v.length < 2) { showFieldError(field, '店铺别名至少需要2个字符'); return false; }
      break;
    case 'currency':
      if (!v) { showFieldError(field, '请选择店铺币种'); return false; }
      break;
    case 'clientId':
      if (!v) { showFieldError(field, 'Client-Id不能为空'); return false; }
      break;
    case 'apiKey':
      if (!v) { showFieldError(field, 'Api-Key不能为空'); return false; }
      break;
  }
  return true;
}

/** 显示字段错误 */
function showFieldError(field, msg) {
  const el = document.getElementById(`error_${field}`);
  if (el) { el.textContent = msg; el.classList.add('show'); }
}

/** 清除所有错误 */
function clearAllAuthErrors() {
  ['storeId','alias','currency','clientId','apiKey'].forEach(f => {
    const el = document.getElementById(`error_${f}`);
    if (el) { el.textContent = ''; el.classList.remove('show'); }
  });
}

/** 切换密码可见性 */
function togglePwdVisibility(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const isPassword = input.type === 'password';
  input.type = isPassword ? 'text' : 'password';
  const icon = btn.querySelector('i');
  if (icon && window.lucide) {
    icon.setAttribute('data-lucide', isPassword ? 'eye-off' : 'eye');
    lucide.createIcons();
  }
}

/** 提交添加店铺表单 */
async function submitAddStoreForm() {
  clearAllAuthErrors();

  let allValid = true;
  allValid &= validateAuthField('storeId', document.getElementById('addStoreId')?.value);
  allValid &= validateAuthField('alias', document.getElementById('addAlias')?.value);
  allValid &= validateAuthField('currency', document.getElementById('addCurrency')?.value);
  allValid &= validateAuthField('clientId', document.getElementById('addClientId')?.value);
  allValid &= validateAuthField('apiKey', document.getElementById('addApiKey')?.value);

  if (!allValid) {
    Toast.show('请检查表单中的错误项', 'warning');
    return;
  }

  const confirmBtn = document.getElementById('btnAddStoreConfirm');
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.innerHTML = '<span class="loading-spinner-sm"></span> 提交中...';
  }

  const storeData = {
    storeId: document.getElementById('addStoreId').value.trim(),
    alias: document.getElementById('addAlias').value.trim(),
    currency: document.getElementById('addCurrency').value,
    group: '新店',
    notifyOn: true,
    authType: 'api',
    clientId: document.getElementById('addClientId').value.trim(),
    apiKey: document.getElementById('addApiKey').value.trim(),
    authStatus: 'active',
  };

  const res = await Api.createStore(storeData);

  if (confirmBtn) {
    confirmBtn.disabled = false;
    confirmBtn.innerHTML = '确定';
  }

  if (res.code === 200) {
    Modal.close();
    await loadStoresFromAPI();
    await loadGroupsFromAPI();
    renderStoreTable();
    Toast.show(`店铺「${storeData.alias}」添加并授权成功！`, 'success');
  } else {
    Toast.show(res.msg || '添加失败', 'error');
  }
}

/** 显示新建分组弹窗 */
function showAddGroupDialog() {
  Modal.show({
    title: '新建分组',
    body: `
      <div class="form-group">
        <label class="form-label">分组名称 <span class="required">*</span></label>
        <input type="text" class="form-input" id="newGroupName" placeholder="输入分组名称...">
      </div>
      <div class="existing-groups" id="existingGroups">
        <p style="font-size:12px;color:#9CA3AF;margin-bottom:8px;">已有分组：</p>
        <div class="group-tags" id="existingGroupTags">
          ${storeGroups.map(g => `<span class="group-tag">${g}</span>`).join('')}
        </div>
      </div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost' },
      { text: '创建', class: 'btn-primary', onClick: () => {
        const name = document.getElementById('newGroupName')?.value?.trim();
        if (!name) { Toast.show('请输入分组名称', 'warning'); return; }
        Modal.close();
        Toast.show(`分组「${name}」创建成功`, 'success');
        // 刷新分组列表
        loadGroupsFromAPI();
      }},
    ],
  });
}

// ===== 用户管理（管理员专属） =====

const USER_ROLE_TEXT = { admin: '管理员', salesperson: '业务员', operator: '历史运营账号' };
let _usersCache = [];

function _userRoleText(r) { return USER_ROLE_TEXT[r] || r || '-'; }
function _findUser(uid) { return _usersCache.find(u => u.id === uid); }

/** 拉取用户列表 */
async function loadUsers() {
  const res = await API.get('/api/users');
  return (res && res.code === 200 && res.data) ? (res.data.list || []) : [];
}

/** 关闭当前所有弹窗（供嵌套操作成功后重建列表） */
async function _closeAllModals() {
  while (document.querySelector('.modal-overlay')) {
    const closed = await Modal.close();
    if (!closed) break;
  }
}

/** 打开用户管理弹窗（账号列表 + 新建/编辑/删除/绑定店铺） */
async function showUserManager() {
  if (!_storeIsAdmin()) { Toast.show('仅管理员可访问用户管理', 'warning'); return; }
  const users = await loadUsers();
  _usersCache = users;
  const me = Store.getUser();
  const rows = users.length ? users.map(u => `
    <tr>
      <td><strong>${u.username}</strong>${me && u.id === me.id ? ' <span style="color:var(--primary);font-size:12px;">（当前账号）</span>' : ''}</td>
      <td>${u.nickname || '-'}</td>
      <td>${_userRoleText(u.role)}</td>
      <td>${u.status === 'disabled' ? '<span style="color:#dc2626;">禁用</span>' : '<span style="color:#16a34a;">启用</span>'}</td>
      <td>${u.storeCount ?? 0}</td>
      <td>
        ${me && u.id === me.id
          ? '<span style="color:var(--text-tertiary);font-size:12px;">当前账号不可操作</span>'
          : `
        <button class="btn btn-xs" onclick="openUserStoreBindDialog(${u.id})">绑定店铺</button>
        <button class="btn btn-xs" onclick="openEditUserDialog(${u.id})">编辑</button>
        <button class="btn btn-xs" style="color:var(--danger);" onclick="deleteUser(${u.id})">删除</button>`}
      </td>
    </tr>
  `).join('') : '<tr><td colspan="6" style="text-align:center;padding:30px;color:var(--text-tertiary);">暂无账号</td></tr>';

  Modal.show({
    title: '用户管理',
    size: 'lg',
    body: `
      <p style="font-size:13px;color:var(--text-tertiary);margin-bottom:12px;">创建业务员账号并为其绑定店铺，业务员登录后仅能查看归属店铺的产品与店铺数据、财务，选品池为只读。</p>
      <div style="overflow:auto;max-height:420px;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead>
            <tr>
              <th>用户名</th>
              <th>昵称</th>
              <th>角色</th>
              <th>状态</th>
              <th>店铺数</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `,
    footer: [
      { text: '新建账号', class: 'btn-primary', onClick: () => openCreateUserDialog() },
      { text: '关闭', class: 'btn-ghost', onClick: () => Modal.close() },
    ],
    onOpen: () => { if (window.lucide) lucide.createIcons(); },
  });
}

/** 新建账号弹窗 */
function openCreateUserDialog() {
  if (!_storeIsAdmin()) { Toast.show('仅管理员可操作', 'warning'); return; }
  Modal.show({
    title: '新建账号',
    size: 'md',
    body: `
      <div class="form-group">
        <label class="form-label">用户名 <span class="required">*</span></label>
        <input type="text" class="form-input" id="cuUsername" placeholder="登录用户名" maxlength="50" autocomplete="off">
      </div>
      <div class="form-group">
        <label class="form-label">密码 <span class="required">*</span></label>
        <input type="password" class="form-input" id="cuPassword" placeholder="至少 6 位" maxlength="64" autocomplete="new-password">
      </div>
      <div class="form-group">
        <label class="form-label">昵称</label>
        <input type="text" class="form-input" id="cuNickname" placeholder="显示名称，默认同用户名" maxlength="50">
      </div>
      <div class="form-group">
        <label class="form-label">角色</label>
        <input type="text" class="form-input" value="业务员（仅查看已分配店铺）" readonly>
      </div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
      { text: '创建', class: 'btn-primary', onClick: () => submitCreateUser() },
    ],
  });
}

/** 提交创建账号 */
async function submitCreateUser() {
  const username = document.getElementById('cuUsername').value.trim();
  const password = document.getElementById('cuPassword').value;
  if (!username || !password) { Toast.show('用户名和密码不能为空', 'warning'); return; }
  const res = await API.post('/api/users', {
    username,
    password,
    role: 'salesperson',
    nickname: document.getElementById('cuNickname').value.trim(),
  });
  if (res.code === 200) {
    await _closeAllModals();
    Toast.show(res.msg || '账号已创建', 'success');
    showUserManager();
  } else {
    Toast.show(res.msg || '创建失败', 'error');
  }
}

/** 编辑账号弹窗 */
function openEditUserDialog(uid) {
  if (!_storeIsAdmin()) { Toast.show('仅管理员可操作', 'warning'); return; }
  const u = _findUser(uid);
  if (!u) return;
  Modal.show({
    title: `编辑账号 - ${u.username}`,
    size: 'md',
    body: `
      <div class="form-group">
        <label class="form-label">昵称</label>
        <input type="text" class="form-input" id="euNickname" value="${u.nickname || ''}" maxlength="50">
      </div>
      <div class="form-group">
        <label class="form-label">状态</label>
        <select class="form-select" id="euStatus">
          <option value="active" ${u.status !== 'disabled' ? 'selected' : ''}>启用</option>
          <option value="disabled" ${u.status === 'disabled' ? 'selected' : ''}>禁用</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">重置密码（留空不修改）</label>
        <input type="password" class="form-input" id="euPassword" placeholder="至少 6 位" maxlength="64" autocomplete="new-password">
      </div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
      { text: '保存', class: 'btn-primary', onClick: () => submitEditUser(u.id) },
    ],
  });
}

/** 提交编辑账号 */
async function submitEditUser(uid) {
  const data = {
    nickname: document.getElementById('euNickname').value.trim(),
    status: document.getElementById('euStatus').value,
  };
  const pwd = document.getElementById('euPassword').value;
  if (pwd) data.password = pwd;
  const res = await API.put('/api/users/' + uid, data);
  if (res.code === 200) {
    await _closeAllModals();
    Toast.show(res.msg || '账号已更新', 'success');
    showUserManager();
  } else {
    Toast.show(res.msg || '保存失败', 'error');
  }
}

/** 删除账号（其名下店铺自动解绑为公共店铺） */
async function deleteUser(uid) {
  if (!_storeIsAdmin()) { Toast.show('仅管理员可操作', 'warning'); return; }
  const u = _findUser(uid);
  if (!u) return;
  const me = Store.getUser();
  if (me && u.id === me.id) { Toast.show('不能删除当前登录账号', 'warning'); return; }
  const ok = await Modal.confirm(`确定删除账号「${u.username}」？其名下店铺将自动解绑为公共店铺。`);
  if (!ok) return;
  const res = await API.del('/api/users/' + uid);
  if (res.code === 200) {
    await _closeAllModals();
    Toast.show(res.msg || '账号已删除', 'success');
    await loadStoresFromAPI();
    renderStoreTable();
    showUserManager();
  } else {
    Toast.show(res.msg || '删除失败', 'error');
  }
}

/** 用户管理弹窗内：绑定店铺（给某用户绑定一家店铺） */
function openUserStoreBindDialog(uid) {
  if (!_storeIsAdmin()) { Toast.show('仅管理员可操作', 'warning'); return; }
  const u = _findUser(uid);
  if (!u) return;
  if (u.role !== 'salesperson') { Toast.show('店铺只能绑定给业务员账号', 'warning'); return; }
  if (!allStores.length) { Toast.show('店铺列表为空，请先添加店铺', 'warning'); return; }
  Modal.show({
    title: `绑定店铺 - ${u.username}`,
    size: 'md',
    body: `
      <p style="font-size:13px;color:var(--text-tertiary);margin-bottom:12px;">将店铺绑定给「${u.username}」后，该业务员登录仅能查看归属店铺的数据。重复绑定会转移归属。</p>
      <div class="form-group">
        <label class="form-label">选择店铺</label>
        <select class="form-select" id="bindStoreSelect">
          <option value="">请选择店铺</option>
          ${allStores.map(s => `<option value="${s.id}">${s.alias || s.store_id}（${s.store_id || s.storeId}）｜${s.accountId ? '当前归属：' + (s.ownerName || '已绑定') : '公共'}</option>`).join('')}
        </select>
      </div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
      { text: '绑定', class: 'btn-primary', onClick: () => submitBindStore(uid) },
    ],
  });
}

/** 提交店铺→用户绑定 */
async function submitBindStore(uid) {
  const storePk = parseInt(document.getElementById('bindStoreSelect').value, 10);
  if (!storePk) { Toast.show('请选择店铺', 'warning'); return; }
  const res = await API.put('/api/stores/owner', { storePk, accountId: uid });
  if (res.code === 200) {
    await _closeAllModals();
    Toast.show(res.msg || '绑定成功', 'success');
    await loadStoresFromAPI();
    renderStoreTable();
    showUserManager();
  } else {
    Toast.show(res.msg || '绑定失败', 'error');
  }
}

/** 店铺表格内：设置店铺归属（绑定/改绑/解绑） */
async function openStoreOwnerDialog(storePk) {
  if (!_storeIsAdmin()) { Toast.show('仅管理员可操作', 'warning'); return; }
  const store = allStores.find(s => s.id === storePk);
  if (!store) return;
  const users = await loadUsers();
  const candidates = users.filter(u => u.role === 'salesperson');
  Modal.show({
    title: `店铺归属 - ${store.alias || store.store_id}`,
    size: 'md',
    body: `
      <p style="font-size:13px;color:var(--text-tertiary);margin-bottom:12px;">绑定业务员后，该员工登录仅能查看此店铺数据。选择「公共店铺」即解绑。</p>
      <div class="form-group">
        <label class="form-label">归属业务员</label>
        <select class="form-select" id="ownerUserSelect">
          <option value="">公共店铺（不绑定）</option>
          ${candidates.map(u => `<option value="${u.id}" ${store.accountId == u.id ? 'selected' : ''}>${u.username}${u.nickname && u.nickname !== u.username ? '（' + u.nickname + '）' : ''}</option>`).join('')}
        </select>
      </div>
    `,
    footer: [
      { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
      { text: '保存', class: 'btn-primary', onClick: () => submitStoreOwner(storePk) },
    ],
  });
}

/** 提交店铺归属设置 */
async function submitStoreOwner(storePk) {
  const val = document.getElementById('ownerUserSelect').value;
  const accountId = val ? parseInt(val, 10) : null;
  const res = await API.put('/api/stores/owner', { storePk, accountId });
  if (res.code === 200) {
    Modal.close();
    Toast.show(res.msg || '已保存', 'success');
    await loadStoresFromAPI();
    renderStoreTable();
  } else {
    Toast.show(res.msg || '保存失败', 'error');
  }
}

// ===== 初始化 =====

async function initStorePage() {
  await loadStoresFromAPI();
  await loadGroupsFromAPI();
  renderStoreTable();
}

// 注册路由
Router.register('/store-manage', (route) => {
  const html = renderStorePage(route);
  requestAnimationFrame(() => initStorePage());
  return html;
});

// 页面加载后初始化（首次直接访问时）
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    if (window.location.hash === '#/store-manage' || Router.currentRoute?.path === '/store-manage') {
      initStorePage();
    }
  }, 150);
});
