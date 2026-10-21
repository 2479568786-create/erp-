/**
 * 账号墙（登录门禁）组件
 *
 * 职责：
 * 1. 全屏登录遮罩：未登录 / Token 失效时强制显示，阻止使用系统
 * 2. 登录流程：调用 /api/auth/login，成功后写入 Store（token+user）
 * 3. 登出入口：清除登录态并回到登录墙
 *
 * 设计：自包含 DOM（不依赖 Modal），样式遵循系统浅色 SaaS 风格
 */
const LoginGate = (() => {
  let overlay = null;
  let shown = false;

  // ============ 样式（内联注入一次） ============
  const CSS = `
    #loginGateOverlay {
      position: fixed; inset: 0; z-index: 9999;
      display: flex; align-items: center; justify-content: center;
      background: linear-gradient(135deg, #eef4ff 0%, #f8fbff 50%, #eef6ff 100%);
      font-family: -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    }
    #loginGateCard {
      width: 400px; max-width: calc(100vw - 32px);
      background: #ffffff; border-radius: 16px;
      box-shadow: 0 20px 60px rgba(37, 99, 235, 0.18), 0 2px 8px rgba(16, 24, 40, 0.06);
      padding: 36px 40px 32px;
    }
    #loginGateCard .lg-logo {
      display: flex; align-items: center; justify-content: center; gap: 10px;
      margin-bottom: 8px;
    }
    #loginGateCard .lg-logo .lg-badge {
      width: 44px; height: 44px; border-radius: 12px;
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      display: flex; align-items: center; justify-content: center;
      color: #fff; font-weight: 700; font-size: 18px;
      box-shadow: 0 6px 16px rgba(37, 99, 235, 0.35);
    }
    #loginGateCard .lg-emblem {
      width: 24px; height: 24px; object-fit: contain;
    }
    #loginGateCard h1 { text-align: center; font-size: 20px; font-weight: 600; color: #1d2939; margin: 4px 0 2px; }
    #loginGateCard .lg-sub { text-align: center; font-size: 13px; color: #667085; margin-bottom: 24px; }
    #loginGateCard .lg-field { margin-bottom: 16px; }
    #loginGateCard .lg-field label { display: block; font-size: 13px; color: #475467; margin-bottom: 6px; font-weight: 500; }
    #loginGateCard .lg-field input {
      width: 100%; box-sizing: border-box; height: 42px; padding: 0 14px;
      border: 1px solid #d0d5dd; border-radius: 8px; font-size: 14px; color: #1d2939;
      background: #fcfcfd; outline: none; transition: border-color .15s, box-shadow .15s;
    }
    #loginGateCard .lg-field input:focus { border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37,99,235,.12); background: #fff; }
    #loginGateCard .lg-pw-wrap { position: relative; }
    #loginGateCard .lg-pw-wrap input { padding-right: 42px; }
    #loginGateCard .lg-pw-toggle {
      position: absolute; right: 4px; top: 50%; transform: translateY(-50%);
      width: 34px; height: 34px; border: 0; border-radius: 6px; background: transparent;
      color: #667085; cursor: pointer; font-size: 15px; line-height: 1;
      display: flex; align-items: center; justify-content: center;
    }
    #loginGateCard .lg-pw-toggle:hover { background: #f2f4f7; color: #344054; }
    #loginGateCard .lg-error {
      display: none; margin: 0 0 14px; padding: 8px 12px;
      background: #fef3f2; border: 1px solid #fecdca; color: #d92d20;
      border-radius: 8px; font-size: 13px;
    }
    #loginGateCard .lg-error.visible { display: block; }
    #loginGateBtn {
      width: 100%; height: 44px; border: 0; border-radius: 8px;
      background: linear-gradient(135deg, #2563eb, #1d4ed8); color: #fff;
      font-size: 15px; font-weight: 600; cursor: pointer;
      box-shadow: 0 6px 14px rgba(37, 99, 235, 0.3);
      transition: opacity .15s, transform .1s;
    }
    #loginGateBtn:hover { opacity: .92; }
    #loginGateBtn:active { transform: translateY(1px); }
    #loginGateBtn:disabled { opacity: .6; cursor: not-allowed; }
    #loginGateCard .lg-foot { margin-top: 18px; text-align: center; font-size: 12px; color: #98a2b3; }
  `;

  function injectStyle() {
    if (document.getElementById('loginGateStyle')) return;
    const style = document.createElement('style');
    style.id = 'loginGateStyle';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  // ============ 遮罩渲染 ============
  function show() {
    if (shown) return;
    injectStyle();
    shown = true;

    overlay = document.createElement('div');
    overlay.id = 'loginGateOverlay';
    overlay.innerHTML = `
      <div id="loginGateCard">
        <div class="lg-logo">
          <div class="lg-badge"><i data-lucide="smile" style="width:22px;height:22px;"></i></div>
          <img class="lg-emblem" src="https://upload.wikimedia.org/wikipedia/commons/1/1e/Silhouette_-_black_-_Coat_of_Arms_of_the_Russian_Federation.svg" alt="俄罗斯联邦国徽">
        </div>
        <h1>GeekOzon ERP</h1>
        <div class="lg-sub">请登录后使用系统</div>
        <div class="lg-error" id="loginGateError"></div>
        <div class="lg-field">
          <label>用户名</label>
          <input id="lgUsername" type="text" placeholder="请输入用户名" autocomplete="username" />
        </div>
        <div class="lg-field">
          <label>密码</label>
          <div class="lg-pw-wrap">
            <input id="lgPassword" type="password" placeholder="请输入密码" autocomplete="current-password" />
            <button id="lgPwToggle" type="button" class="lg-pw-toggle" title="显示/隐藏密码">👁</button>
          </div>
        </div>
        <button id="loginGateBtn" type="button">登 录</button>
        <div class="lg-foot">管理员分配账号后即可登录 · 仅限内部人员使用</div>
      </div>
    `;
    document.body.appendChild(overlay);
    window.lucide?.createIcons?.();

    const usernameInput = overlay.querySelector('#lgUsername');
    const passwordInput = overlay.querySelector('#lgPassword');
    const pwToggle = overlay.querySelector('#lgPwToggle');
    const errorBox = overlay.querySelector('#loginGateError');
    const btn = overlay.querySelector('#loginGateBtn');

    pwToggle.addEventListener('click', () => {
      const isPw = passwordInput.type === 'password';
      passwordInput.type = isPw ? 'text' : 'password';
      pwToggle.textContent = isPw ? '🙈' : '👁';
      passwordInput.focus();
    });

    function submit() {
      const u = usernameInput.value.trim();
      const p = passwordInput.value.trim();
      if (!u || !p) { showError('请输入用户名和密码'); return; }
      btn.disabled = true;
      btn.textContent = '登录中...';
      Api.login({ username: u, password: p }).then((res) => {
        if (res && res.code === 200) {
          Store.setState({ user: res.data.user, token: res.data.token }, 'login-gate');
          hide();
          // 登录成功后完整重载，保证各页面按新身份拉数据
          window.location.reload();
        } else {
          btn.disabled = false;
          btn.textContent = '登 录';
          showError(res?.msg || '登录失败，请重试');
        }
      }).catch(() => {
        btn.disabled = false;
        btn.textContent = '登 录';
        showError('网络异常，请稍后重试');
      });
    }

    function showError(msg) {
      errorBox.textContent = msg;
      errorBox.classList.add('visible');
    }

    btn.addEventListener('click', submit);
    passwordInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    usernameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    setTimeout(() => usernameInput.focus(), 60);
  }

  function hide() {
    if (!overlay) return;
    overlay.remove();
    overlay = null;
    shown = false;
  }

  function isShown() { return shown; }

  /** 启动时校验登录态：已登录返回 true；否则显示账号墙返回 false */
  async function ensure() {
    try {
      const res = await Api.userInfo();
      if (res && res.data && res.data.isLoggedIn) {
        const u = res.data.user;
        if (u && u.id) {
          Store.setState({ user: u }, 'login-gate');
          return true;
        }
      }
    } catch (e) { /* 网络异常也显示登录墙 */ }
    Store.setState({ user: null }, 'login-gate');
    show();
    return false;
  }

  /** 退出登录 */
  async function logout() {
    try { await Api.logout(); } catch (e) { /* ignore */ }
    Store.setState({ user: null, token: null }, 'login-gate');
    window.location.reload();
  }

  return { show, hide, ensure, logout, isShown };
})();

// 挂到全局，供 api.js 401 拦截与 header 使用
window.LoginGate = LoginGate;
