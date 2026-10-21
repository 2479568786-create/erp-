/**
 * 用户管理 · 店群绑定 - User & Shop-Group Management Page
 *
 * 【模块定位】
 * 面向 5~7 人小团队的「店群管理模式」主入口。管理员在这里：
 *   1. 维护成员账号（新建 / 编辑 / 禁用 / 删除 / 重置密码）
 *   2. 把 Ozon 店铺分配给业务员（单个 / 批量 / 按分组 / 覆盖式）
 *   3. 查看「谁管哪些店」「哪些店还没分配」的整体分布
 *   4. 追溯绑定变更历史（谁在什么时候把哪家店转给了谁）
 *
 * 【为什么要有这个页面】
 * 绑定能力此前只以弹窗形式藏在「店铺管理」页内部，没有菜单入口，
 * 且一次只能绑一家店。本页把它升级为独立的店群管理入口，
 * 支持批量与按分组分配，适配「一人管多店」的实际作业方式。
 *
 * 【权限】
 * 仅管理员可访问；非管理员进入时显示提示并引导回首页。
 * 对应后端接口均为 require_admin：
 *   GET  /api/store-assignment/overview
 *   GET  /api/store-assignment/members
 *   GET  /api/users/<uid>/stores
 *   POST /api/store-assignment/bind
 *   POST /api/store-assignment/unbind
 *   GET  /api/store-assignment/logs
 */
const UserManagePage = (() => {
  const ROLE_TEXT = { admin: '管理员', salesperson: '业务员', operator: '运营员' };
  const ROLE_COLOR = { admin: '#7c3aed', salesperson: '#2563eb', operator: '#0891b2' };

  const state = {
    overview: null,
    tab: 'members',
    loading: false,
    keyword: '',
    logs: [],
  };

  // 店铺选择器（批量绑定弹窗）的临时状态
  const picker = {
    selected: new Set(),
    keyword: '',
    group: '',
    stores: [],
    assignee: null,
    mode: 'add',
    remark: '',
  };

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function isAdmin() {
    return Store?.getUser()?.role === 'admin';
  }

  function roleBadge(role) {
    const c = ROLE_COLOR[role] || '#6b7280';
    return `<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;
      background:${c}14;color:${c};border:1px solid ${c}33;">${esc(ROLE_TEXT[role] || role)}</span>`;
  }

  function statusBadge(status) {
    return status === 'disabled'
      ? '<span style="color:#dc2626;font-size:12px;">● 已禁用</span>'
      : '<span style="color:#16a34a;font-size:12px;">● 启用</span>';
  }

  // ======================= 数据加载 =======================

  async function loadOverview() {
    const res = await API.get('/api/store-assignment/overview');
    if (res && res.code === 200) {
      state.overview = res.data;
      return res.data;
    }
    Toast.show((res && res.msg) || '加载店群总览失败', 'error');
    return null;
  }

  async function loadLogs() {
    const res = await API.get('/api/store-assignment/logs', { limit: 100 });
    state.logs = (res && res.code === 200) ? (res.data.list || []) : [];
    return state.logs;
  }

  /** 拉取全部店铺（管理员视角，含归属人）用于分配选择器 */
  async function loadAllStores() {
    const res = await API.get('/api/stores');
    if (res && res.code === 200) return res.data.list || [];
    return [];
  }

  // ======================= 页面骨架 =======================

  function render() {
    if (!isAdmin()) {
      return `
        <div style="animation: pageEnter .35s ease; max-width:640px;">
          <h2 style="font-size:20px;font-weight:600;color:#101828;margin-bottom:8px;">用户管理</h2>
          <div style="padding:24px;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
            <p style="color:#6b7280;font-size:14px;margin:0;">
              只有管理员可以使用用户管理与店铺绑定功能。您当前的角色无权访问此页面。
            </p>
            <button class="btn btn-sm btn-primary" style="margin-top:16px;" onclick="Router.navigate('/store-manage')">
              返回店铺管理
            </button>
          </div>
        </div>`;
    }
    return `
      <div style="animation: pageEnter .35s ease;">
        <div class="store-page-header">
          <div>
            <h2 class="store-page-title">用户管理 · 店群</h2>
            <p class="store-page-desc">维护团队成员账号，并把 Ozon 店铺分配给业务员；业务员登录后只能看到自己名下的店铺、商品与财务。</p>
          </div>
          <div class="store-header-actions">
            <button class="btn btn-sm btn-ghost" onclick="UserManagePage.refresh()">
              <i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 刷新
            </button>
            <button class="btn btn-sm btn-primary" onclick="UserManagePage.openBatchAssign(null)">
              <i data-lucide="link" style="width:14px;height:14px;"></i> 批量分配店铺
            </button>
          </div>
        </div>

        <div id="umStats">${renderStats(null)}</div>
        <div id="umTabs">${renderTabs()}</div>
        <div id="umBody"><div style="padding:40px;text-align:center;color:#98a2b3;">加载中…</div></div>
      </div>`;
  }

  function renderStats(ov) {
    const t = (ov && ov.totals) || {};
    const card = (label, value, hint, color) => `
      <div style="flex:1 1 150px;min-width:140px;padding:14px 16px;border:1px solid #e4e7ec;
                  border-radius:12px;background:#fff;">
        <div style="font-size:12px;color:#667085;margin-bottom:6px;">${esc(label)}</div>
        <div style="font-size:24px;font-weight:600;color:${color || '#101828'};line-height:1.2;">
          ${ov ? value : '–'}</div>
        ${hint ? `<div style="font-size:11px;color:#98a2b3;margin-top:4px;">${esc(hint)}</div>` : ''}
      </div>`;

    const unassigned = t.unassigned || 0;
    return `
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;">
        ${card('Ozon 店铺总数', t.stores || 0, '已接入的店铺')}
        ${card('已绑定业务员', t.bound || 0, '归属明确', '#2563eb')}
        ${card('未分配（公共）', unassigned, unassigned ? '仅管理员可见' : '全部已分配', unassigned ? '#d97706' : '#16a34a')}
        ${card('团队成员', t.members || 0, `在用 ${t.activeMembers || 0} 人`, '#7c3aed')}
        ${card('店铺分组', t.groups || 0, '可按分组批量分配')}
      </div>`;
  }

  function renderTabs() {
    const tabs = [
      ['members', '成员与绑定'],
      ['unassigned', '未分配店铺'],
      ['groups', '分组分布'],
      ['logs', '变更日志'],
    ];
    const ov = state.overview || {};
    const badge = {
      members: (ov.totals && ov.totals.members) || 0,
      unassigned: (ov.totals && ov.totals.unassigned) || 0,
      groups: (ov.totals && ov.totals.groups) || 0,
      logs: '',
    };
    return `
      <div style="display:flex;gap:4px;border-bottom:1px solid #e4e7ec;margin-bottom:16px;">
        ${tabs.map(([k, label]) => {
          const active = state.tab === k;
          return `<button onclick="UserManagePage.switchTab('${k}')"
            style="padding:9px 14px;font-size:14px;background:none;border:none;cursor:pointer;
                   color:${active ? '#2563eb' : '#667085'};font-weight:${active ? 600 : 400};
                   border-bottom:2px solid ${active ? '#2563eb' : 'transparent'};margin-bottom:-1px;">
            ${esc(label)}${badge[k] !== '' ? ` <span style="color:#98a2b3;font-size:12px;">${badge[k]}</span>` : ''}
          </button>`;
        }).join('')}
      </div>`;
  }

  function renderBody() {
    switch (state.tab) {
      case 'unassigned': return renderUnassigned();
      case 'groups': return renderGroups();
      case 'logs': return renderLogs();
      case 'members':
      default: return renderMembers();
    }
  }

  // ======================= Tab 1：成员与绑定 =======================

  function renderMembers() {
    const ov = state.overview || {};
    const members = (ov.members || []).filter(m => {
      if (!state.keyword) return true;
      const kw = state.keyword.toLowerCase();
      return (m.username || '').toLowerCase().includes(kw) || (m.nickname || '').toLowerCase().includes(kw);
    });
    const me = Store.getUser();

    const rows = members.length ? members.map(m => `
      <tr>
        <td>
          <strong>${esc(m.username)}</strong>
          ${m.nickname && m.nickname !== m.username
            ? `<span style="color:#98a2b3;font-size:12px;"> · ${esc(m.nickname)}</span>` : ''}
          ${me && m.id === me.id ? '<span style="color:#2563eb;font-size:12px;">（当前账号）</span>' : ''}
        </td>
        <td>${roleBadge(m.role)}</td>
        <td>${statusBadge(m.status)}</td>
        <td>
          ${m.storeCount
            ? `<span style="font-weight:600;color:#2563eb;cursor:pointer;"
                     onclick="UserManagePage.openMemberStores(${m.id})">${m.storeCount} 家</span>`
            : '<span style="color:#d97706;">未分配</span>'}
        </td>
        <td style="max-width:220px;">
          ${(m.groups || []).length
            ? m.groups.map(g => `<span style="display:inline-block;padding:1px 7px;margin:1px 3px 1px 0;
                 border-radius:6px;background:#f2f4f7;color:#475467;font-size:12px;">${esc(g)}</span>`).join('')
            : '<span style="color:#98a2b3;">–</span>'}
        </td>
        <td style="color:#667085;font-size:12px;">${esc(m.lastLoginAt || '从未登录')}</td>
        <td style="white-space:nowrap;">
          <button class="btn btn-xs btn-primary" onclick="UserManagePage.openBatchAssign(${m.id})">分配店铺</button>
          <button class="btn btn-xs" onclick="UserManagePage.openMemberStores(${m.id})">已绑定</button>
          <button class="btn btn-xs" onclick="UserManagePage.openEditAccount(${m.id})">编辑</button>
          <button class="btn btn-xs" style="color:#dc2626;" onclick="UserManagePage.deleteAccount(${m.id})">删除</button>
        </td>
      </tr>`).join('')
      : `<tr><td colspan="7" style="text-align:center;padding:32px;color:#98a2b3;">
           ${state.keyword ? '没有匹配的成员' : '还没有业务员账号，点击右上角「新建账号」开始'}
         </td></tr>`;

    return `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap;">
        <input class="form-input" style="max-width:260px;" placeholder="搜索用户名 / 昵称"
               value="${esc(state.keyword)}" oninput="UserManagePage.setKeyword(this.value)">
        <button class="btn btn-sm btn-primary" onclick="UserManagePage.openCreateAccount()">
          <i data-lucide="user-plus" style="width:14px;height:14px;"></i> 新建账号
        </button>
        <span style="font-size:12px;color:#98a2b3;">共 ${members.length} 名成员</span>
      </div>
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead>
            <tr>
              <th style="text-align:left;">用户名</th>
              <th style="text-align:left;">角色</th>
              <th style="text-align:left;">状态</th>
              <th style="text-align:left;">名下店铺</th>
              <th style="text-align:left;">涉及分组</th>
              <th style="text-align:left;">最近登录</th>
              <th style="text-align:left;">操作</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ======================= Tab 2：未分配店铺 =======================

  function renderUnassigned() {
    const list = (state.overview && state.overview.unassigned) || [];
    const rows = list.length ? list.map(s => `
      <tr>
        <td><input type="checkbox" class="um-una-cb" value="${s.id}"></td>
        <td><strong>${esc(s.alias || s.storeId)}</strong></td>
        <td style="font-family:ui-monospace,monospace;font-size:12px;">${esc(s.storeId)}</td>
        <td>${esc(s.group || '默认')}</td>
        <td>${esc(s.currency)}</td>
        <td>${esc(s.authStatus)}</td>
      </tr>`).join('')
      : `<tr><td colspan="6" style="text-align:center;padding:32px;color:#16a34a;">
           所有店铺都已分配，无公共店铺。</td></tr>`;

    return `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap;">
        <p style="font-size:13px;color:#667085;margin:0;flex:1;">
          未分配的「公共店铺」对所有管理员可见、对业务员不可见。勾选后可直接分配给某位业务员。
        </p>
        <button class="btn btn-sm btn-ghost" onclick="UserManagePage.toggleAllUnassigned(true)">全选</button>
        <button class="btn btn-sm btn-ghost" onclick="UserManagePage.toggleAllUnassigned(false)">清空</button>
        <button class="btn btn-sm btn-primary" onclick="UserManagePage.assignSelectedUnassigned()">分配给…</button>
      </div>
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead><tr>
            <th style="width:36px;"></th>
            <th style="text-align:left;">店铺别名</th>
            <th style="text-align:left;">Ozon 店铺 ID</th>
            <th style="text-align:left;">分组</th>
            <th style="text-align:left;">币种</th>
            <th style="text-align:left;">授权状态</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ======================= Tab 3：分组分布 =======================

  function renderGroups() {
    const groups = (state.overview && state.overview.groups) || [];
    if (!groups.length) {
      return '<div style="padding:40px;text-align:center;color:#98a2b3;">暂无店铺分组。</div>';
    }
    const cards = groups.map(g => `
      <div style="border:1px solid #e4e7ec;border-radius:12px;background:#fff;padding:16px;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
          <div>
            <div style="font-size:15px;font-weight:600;color:#101828;">${esc(g.name)}</div>
            <div style="font-size:12px;color:#667085;margin-top:4px;">
              共 ${g.storeCount} 家 · 已绑定 ${g.boundCount} · 未分配 ${g.unboundCount}
            </div>
          </div>
          <div style="text-align:right;white-space:nowrap;">
            <button class="btn btn-xs btn-primary" onclick="UserManagePage.openBatchAssign(null, '${esc(g.name)}')">整组分配</button>
            <button class="btn btn-xs" onclick="UserManagePage.unbindGroup('${esc(g.name)}')">整组解绑</button>
          </div>
        </div>
        <div style="margin-top:10px;font-size:12px;color:#475467;">
          归属：${g.ownerNames.length ? g.ownerNames.map(esc).join('、') : '<span style="color:#98a2b3;">无（全部公共）</span>'}
        </div>
        <div style="margin-top:10px;height:6px;border-radius:3px;background:#f2f4f7;overflow:hidden;display:flex;">
          <div style="width:${g.storeCount ? (g.boundCount / g.storeCount * 100) : 0}%;background:#2563eb;"></div>
          <div style="width:${g.storeCount ? (g.unboundCount / g.storeCount * 100) : 0}%;background:#f59e0b;"></div>
        </div>
      </div>`).join('');

    return `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;">${cards}</div>`;
  }

  // ======================= Tab 4：变更日志 =======================

  function renderLogs() {
    const rows = state.logs.length ? state.logs.map(l => `
      <tr>
        <td style="font-size:12px;color:#667085;white-space:nowrap;">${esc(l.createdAt)}</td>
        <td>${esc(l.actionText)}</td>
        <td>${esc(l.storeAlias || l.storeId)}</td>
        <td style="font-size:12px;">${esc(l.fromName || '公共')} → <strong>${esc(l.toName || '公共')}</strong></td>
        <td style="font-size:12px;">${esc(l.operatorName)}</td>
        <td style="font-size:12px;color:#667085;">${esc(l.remark || '–')}</td>
      </tr>`).join('')
      : '<tr><td colspan="6" style="text-align:center;padding:32px;color:#98a2b3;">暂无绑定变更记录</td></tr>';

    return `
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;max-height:560px;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead><tr>
            <th style="text-align:left;">时间</th>
            <th style="text-align:left;">动作</th>
            <th style="text-align:left;">店铺</th>
            <th style="text-align:left;">归属变更</th>
            <th style="text-align:left;">操作人</th>
            <th style="text-align:left;">备注</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ======================= 批量分配店铺弹窗（核心） =======================

  async function openBatchAssign(uid, presetGroup) {
    if (!isAdmin()) { Toast.show('仅管理员可操作', 'warning'); return; }

    const ov = await loadOverview();
    if (!ov) return;
    const members = (ov.members || []).filter(m => m.status === 'active');
    if (!members.length) {
      Toast.show('请先创建至少一个启用状态的业务员账号', 'warning');
      return;
    }
    const stores = await loadAllStores();
    if (!stores.length) {
      Toast.show('还没有店铺，请先到「店铺管理」添加店铺', 'warning');
      return;
    }

    picker.stores = stores;
    picker.selected = new Set();
    picker.keyword = '';
    picker.group = presetGroup || '';
    picker.mode = 'add';
    picker.remark = '';
    picker.assignee = uid || members[0].id;

    Modal.show({
      title: '分配店铺给业务员',
      size: 'lg',
      body: renderAssignDialog(members),
      footer: [
        { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
        { text: '确认分配', class: 'btn-primary', onClick: () => submitBatchAssign() },
      ],
      onOpen: () => {
        if (window.lucide) lucide.createIcons();
        refreshPickerView();
      },
    });
  }

  function renderAssignDialog(members) {
    const groupNames = [...new Set(picker.stores.map(s => s.group || '默认'))].sort();
    return `
      <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-bottom:14px;">
        <div class="form-group" style="flex:1 1 200px;margin:0;">
          <label class="form-label">分配给业务员 <span class="required">*</span></label>
          <select class="form-select" id="umAssignee">
            ${members.map(m => `<option value="${m.id}" ${m.id === picker.assignee ? 'selected' : ''}>
              ${esc(m.username)}${m.nickname && m.nickname !== m.username ? '（' + esc(m.nickname) + '）' : ''}
              · 现有 ${m.storeCount} 家</option>`).join('')}
          </select>
        </div>
        <div class="form-group" style="flex:0 0 150px;margin:0;">
          <label class="form-label">分配模式</label>
          <select class="form-select" id="umMode" onchange="UserManagePage.onModeChange(this.value)">
            <option value="add">追加绑定</option>
            <option value="replace">覆盖绑定</option>
          </select>
        </div>
      </div>

      <div id="umModeHint" style="font-size:12px;color:#667085;background:#f9fafb;border:1px solid #eaecf0;
           border-radius:8px;padding:8px 10px;margin-bottom:12px;">
        追加绑定：把勾选的店铺加到此业务员名下；已被他人持有的店铺会<strong>转移</strong>过来。
      </div>

      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
        <input class="form-input" style="max-width:220px;" placeholder="搜索店铺别名 / ID"
               oninput="UserManagePage.setPickerKeyword(this.value)">
        <select class="form-select" style="max-width:170px;" onchange="UserManagePage.setPickerGroup(this.value)">
          <option value="">全部分组</option>
          ${groupNames.map(g => `<option value="${esc(g)}" ${g === picker.group ? 'selected' : ''}>${esc(g)}</option>`).join('')}
        </select>
        <button class="btn btn-sm btn-ghost" onclick="UserManagePage.pickerSelectAll(true)">全选当前筛选</button>
        <button class="btn btn-sm btn-ghost" onclick="UserManagePage.pickerSelectAll(false)">清空选择</button>
        <span id="umPickCount" style="font-size:12px;color:#2563eb;font-weight:600;">已选 0 家</span>
      </div>

      <div id="umPickerList" style="max-height:300px;overflow:auto;border:1px solid #e4e7ec;border-radius:10px;"></div>

      <div class="form-group" style="margin-top:12px;">
        <label class="form-label">备注（写入变更日志）</label>
        <input class="form-input" id="umRemark" placeholder="如：新同事接手 / 老员工离职交接"
               oninput="UserManagePage.setRemark(this.value)">
      </div>`;
  }

  /** 按当前筛选条件重绘店铺勾选列表 */
  function refreshPickerView() {
    const host = document.getElementById('umPickerList');
    if (!host) return;
    const kw = (picker.keyword || '').toLowerCase();
    const list = picker.stores.filter(s => {
      if (picker.group && (s.group || '默认') !== picker.group) return false;
      if (!kw) return true;
      return String(s.alias || '').toLowerCase().includes(kw)
          || String(s.storeId || s.store_id || '').toLowerCase().includes(kw);
    });

    if (!list.length) {
      host.innerHTML = '<div style="padding:24px;text-align:center;color:#98a2b3;font-size:13px;">没有匹配的店铺</div>';
    } else {
      host.innerHTML = list.map(s => {
        const checked = picker.selected.has(s.id);
        const owner = s.accountId ? (s.ownerName || '已绑定') : '公共';
        const ownerColor = s.accountId ? '#2563eb' : '#98a2b3';
        return `
          <label style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid #f2f4f7;
                        cursor:pointer;font-size:13px;${checked ? 'background:#eff6ff;' : ''}">
            <input type="checkbox" ${checked ? 'checked' : ''}
                   onchange="UserManagePage.togglePick(${s.id}, this.checked)">
            <span style="flex:1;">
              <strong>${esc(s.alias || s.storeId)}</strong>
              <span style="font-family:ui-monospace,monospace;font-size:11px;color:#98a2b3;">
                ${esc(s.storeId || s.store_id || '')}</span>
            </span>
            <span style="font-size:12px;color:#667085;">${esc(s.group || '默认')}</span>
            <span style="font-size:12px;color:${ownerColor};min-width:70px;text-align:right;">${esc(owner)}</span>
          </label>`;
      }).join('');
    }
    const el = document.getElementById('umPickCount');
    if (el) el.textContent = '已选 ' + picker.selected.size + ' 家';
  }

  async function submitBatchAssign() {
    const sel = document.getElementById('umAssignee');
    const accountId = sel ? parseInt(sel.value, 10) : null;
    if (!accountId) { Toast.show('请选择业务员', 'warning'); return; }
    if (!picker.selected.size) { Toast.show('请至少勾选一家店铺', 'warning'); return; }

    const body = {
      accountId,
      storePks: [...picker.selected],
      mode: picker.mode,
      remark: picker.remark || '',
    };
    const res = await API.post('/api/store-assignment/bind', body);
    if (res && res.code === 200) {
      await _closeAllModalsSafely();
      Toast.show(res.msg || '分配成功', 'success');
      await refresh();
    } else {
      Toast.show((res && res.msg) || '分配失败', 'error');
    }
  }

  // ======================= 成员已绑定店铺弹窗 =======================

  async function openMemberStores(uid) {
    if (!isAdmin()) { Toast.show('仅管理员可操作', 'warning'); return; }
    const res = await API.get('/api/users/' + uid + '/stores');
    if (!res || res.code !== 200) { Toast.show((res && res.msg) || '加载失败', 'error'); return; }
    const { user, list } = res.data;

    const rows = list.length ? list.map(s => `
      <tr>
        <td><strong>${esc(s.alias || s.storeId)}</strong></td>
        <td style="font-family:ui-monospace,monospace;font-size:12px;">${esc(s.storeId)}</td>
        <td>${esc(s.group || '默认')}</td>
        <td>${esc(s.authStatus)}</td>
        <td><button class="btn btn-xs" style="color:#dc2626;"
              onclick="UserManagePage.unbindStore(${s.id}, ${uid})">解绑</button></td>
      </tr>`).join('')
      : '<tr><td colspan="5" style="text-align:center;padding:28px;color:#d97706;">该业务员名下暂无店铺</td></tr>';

    Modal.show({
      title: `${user.username} 名下店铺（${list.length} 家）`,
      size: 'lg',
      body: `
        <p style="font-size:13px;color:#667085;margin-bottom:12px;">
          解绑后店铺变为「公共店铺」，仅管理员可见，业务员将立即失去访问权限。
        </p>
        <div style="overflow:auto;max-height:380px;border:1px solid #e4e7ec;border-radius:10px;">
          <table class="collect-table" style="width:100%;font-size:13px;">
            <thead><tr>
              <th style="text-align:left;">店铺别名</th>
              <th style="text-align:left;">Ozon 店铺 ID</th>
              <th style="text-align:left;">分组</th>
              <th style="text-align:left;">授权</th>
              <th style="text-align:left;">操作</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`,
      footer: [
        { text: '全部解绑', class: 'btn-ghost', onClick: () => unbindAllOf(uid, user.username, list.length) },
        { text: '关闭', class: 'btn-primary', onClick: () => Modal.close() },
      ],
    });
  }

  async function unbindStore(storePk, uid) {
    const ok = await Modal.confirm('确定解绑该店铺？解绑后成为公共店铺，该业务员将失去访问权限。');
    if (!ok) return;
    const res = await API.post('/api/store-assignment/unbind', { storePks: [storePk], accountId: uid });
    if (res && res.code === 200) {
      await _closeAllModalsSafely();
      Toast.show(res.msg || '已解绑', 'success');
      await refresh();
      openMemberStores(uid);
    } else {
      Toast.show((res && res.msg) || '解绑失败', 'error');
    }
  }

  async function unbindAllOf(uid, username, count) {
    if (!count) { Toast.show('该业务员名下没有店铺', 'info'); return; }
    const ok = await Modal.confirm(`确定把 ${username} 名下全部 ${count} 家店铺解绑为公共店铺？`);
    if (!ok) return;
    const res = await API.post('/api/store-assignment/unbind', { accountId: uid, all: true });
    if (res && res.code === 200) {
      await _closeAllModalsSafely();
      Toast.show(res.msg || '已解绑', 'success');
      await refresh();
    } else {
      Toast.show((res && res.msg) || '解绑失败', 'error');
    }
  }

  async function unbindGroup(group) {
    const ok = await Modal.confirm(`确定解绑分组「${group}」下的全部已绑定店铺？`);
    if (!ok) return;
    const res = await API.post('/api/store-assignment/unbind', { group, remark: '整组解绑' });
    if (res && res.code === 200) {
      Toast.show(res.msg || '已解绑', 'success');
      await refresh();
    } else {
      Toast.show((res && res.msg) || '解绑失败', 'error');
    }
  }

  // ======================= 账号 CRUD =======================

  function openCreateAccount() {
    Modal.show({
      title: '新建账号',
      size: 'md',
      body: `
        <div class="form-group">
          <label class="form-label">用户名 <span class="required">*</span></label>
          <input class="form-input" id="umNewUser" placeholder="登录用户名" maxlength="50" autocomplete="off">
        </div>
        <div class="form-group">
          <label class="form-label">密码 <span class="required">*</span></label>
          <input type="password" class="form-input" id="umNewPwd" placeholder="至少 6 位" maxlength="64" autocomplete="new-password">
        </div>
        <div class="form-group">
          <label class="form-label">昵称</label>
          <input class="form-input" id="umNewNick" placeholder="显示名称，默认同用户名" maxlength="50">
        </div>
        <div class="form-group">
          <label class="form-label">角色</label>
          <select class="form-select" id="umNewRole">
            <option value="salesperson" selected>业务员（仅查看自己店铺）</option>
            <option value="operator">运营员（受限）</option>
            <option value="admin">管理员（全量权限）</option>
          </select>
        </div>`,
      footer: [
        { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
        { text: '创建并分配店铺', class: 'btn-primary', onClick: () => submitCreateAccount() },
      ],
    });
  }

  async function submitCreateAccount() {
    const username = (document.getElementById('umNewUser').value || '').trim();
    const password = document.getElementById('umNewPwd').value || '';
    const nickname = (document.getElementById('umNewNick').value || '').trim();
    const role = document.getElementById('umNewRole').value;
    if (!username || !password) { Toast.show('用户名和密码不能为空', 'warning'); return; }
    if (password.length < 6) { Toast.show('密码至少 6 位', 'warning'); return; }

    const res = await API.post('/api/users', { username, password, nickname, role });
    if (!res || res.code !== 200) {
      Toast.show((res && res.msg) || '创建失败', 'error');
      return;
    }
    Toast.show(res.msg || '账号已创建', 'success');
    await _closeAllModalsSafely();
    await refresh();
    // 创建后立即引导分配店铺（管理员角色无需分配）
    if (role !== 'admin') {
      const created = (state.overview?.members || []).find(m => m.username === username);
      if (created) openBatchAssign(created.id);
    }
  }

  function openEditAccount(uid) {
    const m = (state.overview?.members || []).find(x => x.id === uid);
    if (!m) { Toast.show('账号信息已过期，请刷新', 'warning'); return; }
    Modal.show({
      title: `编辑账号 - ${m.username}`,
      size: 'md',
      body: `
        <div class="form-group">
          <label class="form-label">昵称</label>
          <input class="form-input" id="umEditNick" value="${esc(m.nickname)}" maxlength="50">
        </div>
        <div class="form-group">
          <label class="form-label">角色</label>
          <select class="form-select" id="umEditRole">
            <option value="salesperson" ${m.role === 'salesperson' ? 'selected' : ''}>业务员</option>
            <option value="operator" ${m.role === 'operator' ? 'selected' : ''}>运营员</option>
            <option value="admin" ${m.role === 'admin' ? 'selected' : ''}>管理员</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">状态</label>
          <select class="form-select" id="umEditStatus">
            <option value="active" ${m.status !== 'disabled' ? 'selected' : ''}>启用</option>
            <option value="disabled" ${m.status === 'disabled' ? 'selected' : ''}>禁用</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">重置密码（留空不修改）</label>
          <input type="password" class="form-input" id="umEditPwd" placeholder="至少 6 位" maxlength="64" autocomplete="new-password">
        </div>
        <p style="font-size:12px;color:#98a2b3;">
          提示：角色改为「管理员」后，该账号会自动脱离店铺绑定关系（管理员天然可见全部店铺）。
        </p>`,
      footer: [
        { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
        { text: '保存', class: 'btn-primary', onClick: () => submitEditAccount(uid) },
      ],
    });
  }

  async function submitEditAccount(uid) {
    const body = {
      nickname: (document.getElementById('umEditNick').value || '').trim(),
      role: document.getElementById('umEditRole').value,
      status: document.getElementById('umEditStatus').value,
    };
    const pwd = document.getElementById('umEditPwd').value;
    if (pwd) {
      if (pwd.length < 6) { Toast.show('密码至少 6 位', 'warning'); return; }
      body.password = pwd;
    }
    const res = await API.put('/api/users/' + uid, body);
    if (res && res.code === 200) {
      await _closeAllModalsSafely();
      Toast.show(res.msg || '账号已更新', 'success');
      await refresh();
    } else {
      Toast.show((res && res.msg) || '保存失败', 'error');
    }
  }

  async function deleteAccount(uid) {
    const m = (state.overview?.members || []).find(x => x.id === uid);
    if (!m) return;
    const me = Store.getUser();
    if (me && m.id === me.id) { Toast.show('不能删除当前登录账号', 'warning'); return; }
    const ok = await Modal.confirm(
      `确定删除账号「${m.username}」？\n\n其名下 ${m.storeCount} 家店铺会自动解绑为公共店铺（不会被删除）。`);
    if (!ok) return;
    const res = await API.del('/api/users/' + uid);
    if (res && res.code === 200) {
      Toast.show(res.msg || '账号已删除', 'success');
      await refresh();
    } else {
      Toast.show((res && res.msg) || '删除失败', 'error');
    }
  }

  // ======================= 小工具 =======================

  /** 关闭本页打开的所有弹窗（弹窗栈式，需逐个关闭） */
  async function _closeAllModalsSafely() {
    let guard = 0;
    while (document.querySelector('.modal-overlay') && guard++ < 8) {
      const closed = await Modal.close();
      if (!closed) break;
    }
  }

  function toggleAllUnassigned(checked) {
    document.querySelectorAll('.um-una-cb').forEach(cb => { cb.checked = checked; });
  }

  async function assignSelectedUnassigned() {
    const pks = [...document.querySelectorAll('.um-una-cb:checked')].map(cb => parseInt(cb.value, 10));
    if (!pks.length) { Toast.show('请先勾选要分配的店铺', 'warning'); return; }
    // 直接把勾选的店铺装入选择器，走统一分配流程
    const ov = state.overview || {};
    const members = (ov.members || []).filter(m => m.status === 'active');
    if (!members.length) { Toast.show('请先创建至少一个启用状态的业务员账号', 'warning'); return; }
    const stores = await loadAllStores();
    picker.stores = stores;
    picker.selected = new Set(pks);
    picker.keyword = '';
    picker.group = '';
    picker.mode = 'add';
    picker.remark = '';
    picker.assignee = members[0].id;

    Modal.show({
      title: '分配店铺给业务员（已选 ' + pks.length + ' 家）',
      size: 'lg',
      body: renderAssignDialog(members),
      footer: [
        { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
        { text: '确认分配', class: 'btn-primary', onClick: () => submitBatchAssign() },
      ],
      onOpen: () => { if (window.lucide) lucide.createIcons(); refreshPickerView(); },
    });
  }

  // ======================= 对外 API =======================

  async function refresh() {
    const ov = await loadOverview();
    if (!ov) return;
    if (state.tab === 'logs') await loadLogs();
    const statsHost = document.getElementById('umStats');
    const tabsHost = document.getElementById('umTabs');
    const bodyHost = document.getElementById('umBody');
    if (statsHost) statsHost.innerHTML = renderStats(ov);
    if (tabsHost) tabsHost.innerHTML = renderTabs();
    if (bodyHost) bodyHost.innerHTML = renderBody();
    if (window.lucide) lucide.createIcons();
  }

  async function switchTab(tab) {
    state.tab = tab;
    if (tab === 'logs' && !state.logs.length) await loadLogs();
    const tabsHost = document.getElementById('umTabs');
    const bodyHost = document.getElementById('umBody');
    if (tabsHost) tabsHost.innerHTML = renderTabs();
    if (bodyHost) bodyHost.innerHTML = renderBody();
    if (window.lucide) lucide.createIcons();
  }

  function setKeyword(v) {
    state.keyword = v || '';
    const bodyHost = document.getElementById('umBody');
    if (bodyHost) bodyHost.innerHTML = renderBody();
    // 重新聚焦搜索框，避免 input 被 innerHTML 重建后失焦
    const inp = document.querySelector('#umBody input.form-input');
    if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
  }

  function setPickerKeyword(v) { picker.keyword = v || ''; refreshPickerView(); }
  function setPickerGroup(v) { picker.group = v || ''; refreshPickerView(); }
  function setRemark(v) { picker.remark = v || ''; }

  function onModeChange(v) {
    picker.mode = v;
    const hint = document.getElementById('umModeHint');
    if (hint) {
      hint.innerHTML = v === 'replace'
        ? '覆盖绑定：该业务员名下店铺将以本次勾选结果为准，<strong>未勾选的现有店铺会被解绑为公共店铺</strong>。适用于人员交接。'
        : '追加绑定：把勾选的店铺加到此业务员名下；已被他人持有的店铺会<strong>转移</strong>过来。';
    }
  }

  function togglePick(id, checked) {
    if (checked) picker.selected.add(id); else picker.selected.delete(id);
    refreshPickerView();
  }

  function pickerSelectAll(checked) {
    const kw = (picker.keyword || '').toLowerCase();
    picker.stores.forEach(s => {
      if (picker.group && (s.group || '默认') !== picker.group) return;
      if (kw && !(String(s.alias || '').toLowerCase().includes(kw)
               || String(s.storeId || s.store_id || '').toLowerCase().includes(kw))) return;
      if (checked) picker.selected.add(s.id); else picker.selected.delete(s.id);
    });
    refreshPickerView();
  }

  async function init() {
    if (!isAdmin()) return;
    await refresh();
  }

  return {
    render, init, refresh, switchTab, setKeyword,
    openBatchAssign, openMemberStores, openCreateAccount, openEditAccount, deleteAccount,
    unbindStore, unbindGroup, assignSelectedUnassigned, toggleAllUnassigned,
    setPickerKeyword, setPickerGroup, setRemark, onModeChange, togglePick, pickerSelectAll,
  };
})();

Router.register('/user-manage', () => {
  const html = UserManagePage.render();
  // 骨架先渲染，数据随后异步填充（refresh 内部更新 #umStats/#umTabs/#umBody）
  requestAnimationFrame(() => UserManagePage.init());
  return html;
});


// 暴露到全局，便于内联 onclick 与外部测试调用
// （顶层 const 只进入脚本词法作用域，不会自动挂到 window）
window.UserManagePage = UserManagePage;

/* ============================================================================
 * 按角色显隐菜单：带 data-admin-only 的导航项仅管理员可见
 *
 * 注意：启动阶段登录态尚未解析完成（LoginGate.ensure 是异步的），
 * 此时 user 为 null，应视为「未知」而隐藏入口；但**绝不能**据此跳转路由，
 * 否则已登录的管理员一进 #/user-manage 就会被误弹回店铺管理。
 * 非管理员真的访问了该路由时，由页面自身渲染「无权限」提示即可。
 * ========================================================================== */
(function bindRoleBasedNav() {
  function apply() {
    let admin = false;
    try { admin = Store?.getUser?.()?.role === 'admin'; } catch (e) { admin = false; }
    document.querySelectorAll('[data-admin-only]').forEach(el => {
      el.style.display = admin ? '' : 'none';
    });
  }
  apply();
  // 注意：Store 是顶层 const（脚本词法作用域），window.Store 并不存在，
  // 必须直接引用标识符，否则订阅不会生效 → 菜单永远停留在隐藏状态。
  try { if (typeof Store !== 'undefined' && Store.subscribe) Store.subscribe(apply); } catch (e) { /* noop */ }
  document.addEventListener('DOMContentLoaded', apply);
})();

