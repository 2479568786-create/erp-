/**
 * 顶部导航栏组件
 * 管理 Tab 切换、搜索、用户操作
 */
const Header = (() => {
  let elements = {};

  function init() {
    // 缓存 DOM 引用
    elements.tabs = document.querySelectorAll('#headerTabs .header-tab');
    elements.searchInput = document.getElementById('globalSearch');

    // 绑定 Tab 切换事件
    elements.tabs.forEach((tab) => {
      tab.addEventListener('click', () => handleTabSwitch(tab));
    });

    // 搜索框交互
    if (elements.searchInput) {
      elements.searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleSearch();
      });
    }

    // 用户区：真实用户名/角色渲染 + 下拉菜单（含退出登录）
    renderUserMenu();

    // 新增操作按钮
    const newActionBtn = document.getElementById('newActionBtn');
    if (newActionBtn) {
      newActionBtn.addEventListener('click', () => {
        Modal.show({
          title: '新增操作',
          body: `
            <div class="form-group">
              <label class="form-label">选择操作类型</label>
              <select class="form-select">
                <option>手动添加商品</option>
                <option>批量导入 URL</option>
                <option>创建发布任务</option>
              </select>
            </div>
          `,
          footer: [
            { text: '取消', class: 'btn-ghost' },
            { text: '确认', class: 'btn-primary', onClick: () => Toast.show('功能开发中...', 'info') },
          ],
        });
      });
    }
  }

  /** 角色展示文案 */
  function roleText(role) {
    const map = { admin: '管理员', salesperson: '业务员', operator: '运营员', guest: '未登录' };
    return map[role] || role || '运营员';
  }

  /** 渲染用户区：头像、昵称、角色 + 点击呼出下拉（退出登录） */
  function renderUserMenu() {
    const avatarBox = document.getElementById('userAvatar');
    if (!avatarBox) return;
    const user = Store?.getUser?.() || null;
    const name = (user && (user.nickname || user.username)) || '未登录';
    const role = user ? roleText(user.role) : '未登录';
    const initial = name.charAt(0).toUpperCase() || 'U';

    avatarBox.innerHTML = `
      <div class="w-9 h-9 rounded-full bg-gradient-to-br from-brand-400 to-brand-600 flex items-center justify-center text-white text-sm font-semibold shadow-theme-sm">${initial}</div>
      <div class="hidden lg:flex flex-col leading-tight">
        <span class="text-sm font-medium text-gray-800">${name}</span>
        <span class="text-xs text-gray-500">${role}</span>
      </div>
      <i data-lucide="chevron-down" class="hidden lg:block text-gray-400" style="width:16px;height:16px;"></i>
    `;
    try { lucide?.createIcons?.(); } catch (e) { /* ignore */ }

    // 点击呼出下拉菜单（含退出登录）
    const menuId = 'headerUserMenu';
    avatarBox.addEventListener('click', (e) => {
      e.stopPropagation();
      const existing = document.getElementById(menuId);
      if (existing) { existing.remove(); return; }
      const menu = document.createElement('div');
      menu.id = menuId;
      Object.assign(menu.style, {
        position: 'fixed', right: '24px', top: '64px', zIndex: 120,
        background: '#fff', border: '1px solid #e4e7ec', borderRadius: '10px',
        boxShadow: '0 12px 32px rgba(16,24,40,0.16)', minWidth: '180px', padding: '6px',
        fontSize: '14px',
      });
      const logoutBtn = document.createElement('button');
      logoutBtn.textContent = '退出登录';
      Object.assign(logoutBtn.style, {
        display: 'block', width: '100%', textAlign: 'left', padding: '8px 12px',
        background: 'none', border: '0', borderRadius: '6px', cursor: 'pointer',
        color: '#d92d20',
      });
      logoutBtn.onmouseenter = () => logoutBtn.style.background = '#fef3f2';
      logoutBtn.onmouseleave = () => logoutBtn.style.background = 'none';
      logoutBtn.addEventListener('click', () => { menu.remove(); window.LoginGate?.logout(); });
      menu.appendChild(logoutBtn);
      document.body.appendChild(menu);
    });
    document.addEventListener('click', () => {
      document.getElementById(menuId)?.remove();
    }, { once: false });
  }

  /** 处理 Tab 切换 */
  function handleTabSwitch(activeTab) {
    elements.tabs.forEach((t) => t.classList.remove('active'));
    activeTab.classList.add('active');

    const tabName = activeTab.dataset.tab;
    switch (tabName) {
      case 'engine':
        Router.navigate('/ai-write');
        break;
      case 'products':
        Router.navigate('/collect');
        break;
      case 'publish':
        Router.navigate('/publish');
        break;
      default:
        Router.navigate('/home');
    }
  }

  /** 处理搜索 */
  function handleSearch() {
    const query = elements.searchInput?.value?.trim();
    if (!query) return;
    console.log('[Header] 搜索:', query);
    Toast.show(`搜索「${query}」的结果...`, 'info');
  }

  /** 设置激活的 Tab */
  function setActiveTab(tabKey) {
    elements.tabs.forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.tab === tabKey);
    });
  }

  return { init, setActiveTab, refreshUser: renderUserMenu };
})();
