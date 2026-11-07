/**
 * 充值中心 - Recharge Page（微信 Native 扫码充值 + 人工充值兜底）
 *
 * 接口：
 *   GET  /api/pay/status                      支付开关 / 档位（业务员只拿到 enabled/ready/tiers）
 *   POST /api/pay/orders                      建单 → code_url（1 点 = 1 元）
 *   GET  /api/pay/orders/<out_trade_no>       查单（轮询到账）
 *   POST /api/pay/orders/<out_trade_no>/close 关单
 *   GET  /api/pay/orders                      最近充值记录（本人；管理员可 ?account_id=）
 *   GET  /api/user/info                       当前账号 + 余额 + 单图点数
 *   GET  /api/users                           管理员：账号列表（人工充值时选账号）
 *   POST /api/image-billing/accounts/<id>/topup 管理员：人工加点（现有接口）
 *   GET  /api/image-billing/topups            管理员：人工充值流水（现有接口）
 *
 * 口径：WECHATPAY_ENABLED=0 或缺料时页面降级为「人工充值指引」，不把 503 抛给业务员。
 */
const RechargePage = (() => {
  'use strict';

  const STATUS_TEXT = {
    created: '待支付',
    paid: '已支付·入账中',
    credited: '已到账',
    closed: '已关闭',
    refunded: '已退款',
  };
  const POLL_MS = 2500;

  const state = {
    loaded: false,
    user: null,
    isAdmin: false,
    status: null,          // /api/pay/status 的 data
    statusError: '',
    points: null,          // 选中的档位
    order: null,           // 当前订单（建单返回 + 轮询状态）
    expiresAt: 0,
    accounts: [],
    accountId: null,       // 管理员代充目标的 account id
    countdownTimer: null,
    pollTimer: null,
    fastRemaining: 0,       // 回跳后前几次用 0.8s 加密轮询
    banner: null,          // { type, html }
  };

  /* ---------------- 小工具 ---------------- */

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function money(cents) {
    const n = Number(cents || 0) / 100;
    return '¥' + n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function pointsText(v) {
    const n = Number(v || 0);
    return Number.isInteger(n) ? String(n) : n.toFixed(2);
  }

  /** 后端时间串是 UTC（datetime('now')），转本地显示 */
  function fmtTime(raw) {
    if (!raw) return '-';
    const iso = String(raw).includes('T') ? String(raw) : String(raw).replace(' ', 'T') + 'Z';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(raw);
    return d.toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  }

  function statusBadge(st) {
    const key = STATUS_TEXT[st] ? st : 'closed';
    return `<span class="rc-status ${key}">${STATUS_TEXT[st] || esc(st || '-')}</span>`;
  }

  /** 业务员看自己的账号；管理员可切换到任意账号 */
  function activeAccount() {
    if (!state.isAdmin) return state.user || {};
    if (!state.accountId || (state.user && state.accountId === state.user.id)) return state.user || {};
    return state.accounts.find((a) => a.id === state.accountId) || state.user || {};
  }

  function accountLabel(acc) {
    if (!acc) return '-';
    return acc.nickname || acc.username || ('#' + acc.id);
  }

  function pricePerImage() {
    const v = Number((state.user && state.user.imagePricePoints) || 0);
    return v > 0 ? v : null;
  }

  function setBanner(type, html) {
    state.banner = html ? { type, html } : null;
    renderBanner();
  }

  /* ---------------- 渲染：骨架 ---------------- */

  function renderPage() {
    return `
      <div class="rc-wrap">
        <div class="online-page-header">
          <div>
            <h2 class="online-page-title">充值中心</h2>
            <p class="online-page-desc">1 点 = 1 元，充值后立即到账，用于 AI 生成商品图</p>
          </div>
          <button class="btn btn-xs btn-primary" type="button" onclick="RechargePage.refreshAll()">刷新</button>
        </div>

        <div id="rcBanner"></div>

        <div class="rc-grid">
          <div>
            <div class="rc-card">
              <div class="rc-card-title">
                <span>当前余额</span>
                <span id="rcAccPickerWrap" style="display:none;">
                  <select class="rc-acc-select" id="rcAccSelect" onchange="RechargePage.onAccountChange(this.value)"></select>
                </span>
              </div>
              <div class="rc-balance-row">
                <div class="rc-balance-num"><span id="rcBalance">--</span><small>点</small></div>
                <div class="rc-balance-meta">
                  <div>账号：<b id="rcAccountName">--</b> <span class="rc-tag" id="rcRoleTag" style="display:none;">管理员</span></div>
                  <div>单价：<b id="rcPrice">--</b> <span class="rc-muted">/ 张（按所选生图模型扣点）</span></div>
                </div>
              </div>
            </div>

            <div class="rc-card">
              <div class="rc-card-title"><span>选择充值档位</span><span class="rc-muted" style="font-size:12px;">1 点 = 1 元</span></div>
              <div class="rc-tier-grid" id="rcTiers">
                <div class="rc-empty">加载中...</div>
              </div>
              <div class="rc-actions">
                <button class="btn btn-sm btn-primary" type="button" id="rcPayBtn" onclick="RechargePage.createOrder()" disabled>
                  生成充值二维码
                </button>
                <span class="rc-muted" style="font-size:12px;" id="rcTierHint">先选一个档位</span>
              </div>
            </div>

            <div class="rc-card" id="rcOrderCard" style="display:none;">
              <div class="rc-card-title">
                <span>扫码支付</span>
                <span class="rc-muted" style="font-size:12px;">剩余 <span class="rc-countdown" id="rcCountdown">--:--</span></span>
              </div>
              <div class="rc-qr-box">
                <div class="rc-qr-canvas" id="rcQrTarget"></div>
                <div class="rc-qr-meta">
                  <div>金额：<b id="rcOrderAmount">--</b></div>
                  <div>到账：<b id="rcOrderPoints">--</b> 点</div>
                  <div>订单号：<b id="rcOrderNo">--</b></div>
                  <div>账号：<b id="rcOrderAccount">--</b></div>
                  <div style="margin-top:8px;">状态：<span id="rcOrderStatus">--</span></div>
                  <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;">
                    <button class="btn btn-xs btn-ghost" type="button" onclick="RechargePage.closeOrder()">关闭订单</button>
                    <button class="btn btn-xs btn-primary" type="button" onclick="RechargePage.resetOrder(true)">重新下单</button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div>
            <div class="rc-card" id="rcAdminCard" style="display:none;">
              <div class="rc-card-title"><span>人工充值（管理员）</span><span class="rc-muted" style="font-size:12px;">走现有点数接口</span></div>
              <div class="rc-form-row">
                <select class="rc-acc-select" id="rcTopupAccount" style="min-width:130px;"></select>
                <input class="rc-acc-select" id="rcTopupPoints" type="number" min="1" step="1" placeholder="点数" style="width:96px;" />
                <input class="rc-acc-select" id="rcTopupReason" type="text" placeholder="备注（如：线下转账）" style="min-width:150px;flex:1;" />
                <button class="btn btn-sm btn-primary" type="button" onclick="RechargePage.adminTopup()">加点数</button>
              </div>
              <div class="rc-muted" style="font-size:12px;margin-top:10px;line-height:1.7;">
                业务员线下转账给你时用它加点；点数立刻生效，并写入充值流水（含操作人）。
              </div>
            </div>

            <div class="rc-card">
              <div class="rc-card-title"><span>最近充值记录</span><span class="rc-muted" style="font-size:12px;" id="rcOrderCount">--</span></div>
              <div id="rcOrderTable"><div class="rc-empty">加载中...</div></div>
            </div>

            <div class="rc-card" id="rcTopupCard" style="display:none;">
              <div class="rc-card-title"><span>人工充值流水</span><span class="rc-muted" style="font-size:12px;">对账用</span></div>
              <div id="rcTopupTable"><div class="rc-empty">加载中...</div></div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderBanner() {
    const el = document.getElementById('rcBanner');
    if (!el) return;
    if (!state.banner) { el.innerHTML = ''; return; }
    el.innerHTML = `<div class="rc-banner ${state.banner.type}">${state.banner.html}</div>`;
  }

  function renderBalance() {
    const acc = activeAccount();
    const bal = document.getElementById('rcBalance');
    const name = document.getElementById('rcAccountName');
    const price = document.getElementById('rcPrice');
    const roleTag = document.getElementById('rcRoleTag');
    const pickerWrap = document.getElementById('rcAccPickerWrap');
    if (bal) bal.textContent = acc && acc.imageBalance != null ? pointsText(acc.imageBalance) : '--';
    if (name) name.textContent = accountLabel(acc);
    if (price) price.textContent = pricePerImage() == null ? '--' : pointsText(pricePerImage()) + ' 点';
    if (roleTag) roleTag.style.display = state.isAdmin ? '' : 'none';
    if (pickerWrap) pickerWrap.style.display = state.isAdmin ? '' : 'none';
  }

  /** 管理员专属卡片（人工充值 / 人工流水）显隐 */
  function renderAdminVisibility() {
    const adminCard = document.getElementById('rcAdminCard');
    const topupCard = document.getElementById('rcTopupCard');
    if (adminCard) adminCard.style.display = state.isAdmin ? '' : 'none';
    if (topupCard) topupCard.style.display = state.isAdmin ? '' : 'none';
  }

  function renderAccountPicker() {
    const sel = document.getElementById('rcAccSelect');
    const topupSel = document.getElementById('rcTopupAccount');
    if (!state.isAdmin) return;
    const opts = state.accounts.map((a) =>
      `<option value="${a.id}">${esc(accountLabel(a))}（${pointsText(a.imageBalance)} 点）</option>`).join('');
    if (sel) {
      sel.innerHTML = opts;
      sel.value = String(state.accountId || (state.user && state.user.id) || '');
    }
    if (topupSel) {
      topupSel.innerHTML = opts;
      topupSel.value = String(state.accountId || (state.user && state.user.id) || '');
    }
  }

  /** 渠道短名（微信/支付宝），用于文案与订单列表 */
  function channelName(name) {
    if (window.PayReady && typeof window.PayReady.channelShort === 'function') {
      return PayReady.channelShort(state.status, name);
    }
    return name === 'alipay' ? '支付宝' : (name === 'wxpay' ? '微信' : (name || '-'));
  }

  /** 当前充值可用性：按 primary_channel/channels 判定，兼容旧后端 */
  function readiness() {
    if (window.PayReady && typeof window.PayReady.payReadiness === 'function') {
      return PayReady.payReadiness(state.status);
    }
    const st = state.status;
    const ok = !!(st && st.enabled && st.ready);
    return { canPay: ok, channel: ok ? 'wxpay' : null, label: ok ? '微信扫码' : '', primary: null };
  }

  function canPay() {
    return !!readiness().canPay;
  }

  function renderTiers() {
    const box = document.getElementById('rcTiers');
    const btn = document.getElementById('rcPayBtn');
    const hint = document.getElementById('rcTierHint');
    if (!box) return;
    const tiers = (state.status && Array.isArray(state.status.tiers) && state.status.tiers.length)
      ? state.status.tiers : [10, 30, 50, 100, 200];
    if (state.points == null) state.points = tiers.includes(10) ? 10 : tiers[0];
    box.innerHTML = tiers.map((t) => {
      const active = Number(t) === Number(state.points) ? ' active' : '';
      const dis = canPay() ? '' : ' rc-tier-disabled';
      return `<button type="button" class="rc-tier${active}${dis}" data-tier="${Number(t)}"
                onclick="RechargePage.selectTier(${Number(t)})" aria-pressed="${active ? 'true' : 'false'}">
                <div class="rc-tier-points">${Number(t)} 点</div>
                <div class="rc-tier-price">¥${Number(t)}</div>
              </button>`;
    }).join('');
    if (btn) btn.disabled = !canPay() || !state.points;
    if (hint) {
      hint.textContent = canPay()
        ? `将到账 ${pointsText(state.points)} 点（¥${pointsText(state.points)}）`
        : '支付通道未开通，暂不可在线充值';
    }
  }

  function renderOrders(rows) {
    const box = document.getElementById('rcOrderTable');
    const count = document.getElementById('rcOrderCount');
    if (!box) return;
    if (!rows || !rows.length) {
      box.innerHTML = '<div class="rc-empty">还没有充值记录</div>';
      if (count) count.textContent = '0 条';
      return;
    }
    if (count) count.textContent = rows.length + ' 条';
    const showAccount = state.isAdmin;
    box.innerHTML = `
      <div style="overflow-x:auto;">
        <table class="rc-table">
          <thead><tr>
            <th>时间</th><th>订单号</th>${showAccount ? '<th>账号</th>' : ''}<th>渠道</th><th>点数</th><th>金额</th><th>状态</th>
          </tr></thead>
          <tbody>
            ${rows.map((r) => `<tr>
              <td>${fmtTime(r.created_at)}</td>
              <td style="font-family:ui-monospace,monospace;font-size:12px;">${esc(r.out_trade_no)}</td>
              ${showAccount ? `<td>${esc(accountLabel(state.accounts.find((a) => a.id === r.account_id) || { id: r.account_id }))}</td>` : ''}
              <td>${esc(channelName(r.channel || 'wxpay'))}</td>
              <td>${pointsText(r.points)}</td>
              <td>${money(r.amount_cents)}</td>
              <td>${statusBadge(r.status)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  }

  function renderTopups(rows, totals) {
    const box = document.getElementById('rcTopupTable');
    if (!box) return;
    const t = totals || {};
    const summary = `<div class="rc-muted" style="font-size:12px;margin-bottom:8px;">
      净额 <b style="color:#101828;">${pointsText(t.total_points || 0)}</b> 点
      · 正向累计 <b>+${pointsText(t.total_positive_points || 0)}</b>
      · 冲销 <b>${pointsText(t.reversal_points || 0)}</b>
    </div>`;
    if (!rows || !rows.length) {
      box.innerHTML = summary + '<div class="rc-empty">暂无人工充值流水</div>';
      return;
    }
    box.innerHTML = summary + `
      <div style="overflow-x:auto;">
        <table class="rc-table">
          <thead><tr><th>时间</th><th>账号</th><th>点数</th><th>余额后</th><th>操作人</th></tr></thead>
          <tbody>
            ${rows.map((r) => {
              const isReversal = r.is_reversal || Number(r.points) < 0;
              const ptsCell = isReversal
                ? `<span style="color:#b42318;font-weight:600;">${pointsText(r.points)}</span>
                   <span class="rc-status closed" title="${esc(r.reason || '冲销')}">冲销</span>`
                : `<span style="color:#067647;font-weight:600;">+${pointsText(r.points)}</span>`;
              return `<tr>
                <td>${fmtTime(r.created_at)}</td>
                <td>${esc(accountLabel(state.accounts.find((a) => a.id === r.account_id) || { id: r.account_id }))}</td>
                <td>${ptsCell}</td>
                <td>${r.balance_after == null ? '-' : pointsText(r.balance_after)}</td>
                <td style="font-size:12px;">${esc(r.operator || '-')}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>`;
  }

  /* ---------------- 数据加载 ---------------- */

  async function loadUser() {
    const res = await API.get('/api/user/info');
    if (res && res.code === 200 && res.data && res.data.user && res.data.user.id) {
      state.user = res.data.user;
      state.isAdmin = res.data.user.role === 'admin';
      if (!state.accountId) state.accountId = state.user.id;
      return true;
    }
    state.user = null;
    return false;
  }

  async function loadAccounts() {
    if (!state.isAdmin) return;
    const res = await API.get('/api/users');
    if (res && res.code === 200) {
      state.accounts = (res.data && res.data.list) || [];
      renderAccountPicker();
    }
  }

  async function loadStatus() {
    const res = await API.get('/api/pay/status');
    if (res && res.code === 200) {
      state.status = res.data || null;
      state.statusError = '';
    } else {
      state.status = null;
      state.statusError = (res && res.msg) || '状态获取失败';
    }
    renderStatusBanner();
    renderTiers();
  }

  function renderStatusBanner() {
    const st = state.status;
    if (!st) {
      setBanner('error', `<strong>支付通道状态获取失败</strong>：${esc(state.statusError || '请稍后重试')}`);
      return;
    }
    const r = readiness();
    if (r.canPay) {
      const label = r.label || '在线支付';
      const switchTip = (r.primary && r.channel && r.primary !== r.channel)
        ? `（主通道${channelName(r.primary)}未就绪，已自动切到${label}）`
        : '';
      setBanner('ok', `<strong>${esc(label)}充值已开通</strong>：选档位 → 生成二维码 → 扫码支付，到账后余额自动刷新。${esc(switchTip)}`);
      return;
    }
    const missingNames = (st.missing && st.missing.length) ? st.missing.join('、') : '';
    const adminHint = state.isAdmin
      ? `可在右栏「人工充值」直接给账号加点数${missingNames ? '；当前缺少配置：' + esc(missingNames) : ''}。`
      : '需要充值时请联系管理员，由管理员在后台给你加点数（点数立即到账）。';
    const primaryLabel = st.primary_channel ? channelName(st.primary_channel) : '在线支付';
    setBanner('warn',
      `<div><strong>${esc(primaryLabel)}通道尚未开通</strong>，当前无法在线扫码充值。<br>` +
      adminHint +
      '<br><span class="rc-muted">开通后本页无需改动：同一页面会直接出现二维码。</span></div>');
  }

  async function loadOrders() {
    const params = { limit: 20 };
    if (state.isAdmin && state.accountId) params.account_id = state.accountId;
    const res = await API.get('/api/pay/orders', params);
    if (res && res.code === 200) {
      renderOrders((res.data && res.data.list) || []);
    } else {
      const box = document.getElementById('rcOrderTable');
      if (box) box.innerHTML = `<div class="rc-empty">充值记录加载失败：${esc((res && res.msg) || '未知错误')}</div>`;
    }
  }

  async function loadTopups() {
    if (!state.isAdmin) return;
    const res = await API.get('/api/image-billing/topups', { limit: 30 });
    if (res && res.code === 200) {
      const data = res.data || {};
      renderTopups(data.list || [], { total_points: data.total_points, total_positive_points: data.total_positive_points, reversal_points: data.reversal_points });
    } else {
      const box = document.getElementById('rcTopupTable');
      if (box) box.innerHTML = '<div class="rc-empty">人工充值流水加载失败</div>';
    }
  }

  /* ---------------- 订单流程 ---------------- */

  function clearTimers() {
    if (state.countdownTimer) { clearInterval(state.countdownTimer); state.countdownTimer = null; }
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
  }

  function paintOrder() {
    const o = state.order;
    const card = document.getElementById('rcOrderCard');
    if (!card) return;
    if (!o) { card.style.display = 'none'; return; }
    card.style.display = '';
    const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
    set('rcOrderAmount', money(o.amount_cents));
    set('rcOrderPoints', pointsText(o.points));
    set('rcOrderNo', o.out_trade_no);
    set('rcOrderAccount', accountLabel(activeAccount()));
    const st = document.getElementById('rcOrderStatus');
    if (st) st.innerHTML = o.status_text || statusBadge(o.status);
    if (o.pay_form) {
      renderPayButton(o);
    } else if (o.code_url) {
      drawQr(o.code_url);
    } else {
      const box = document.getElementById('rcQrTarget');
      if (box) {
        box.style.width = '100%';
        box.style.height = 'auto';
        box.style.border = '0';
        box.style.padding = '0';
        box.innerHTML = '<div style="font-size:13px;color:#475467;line-height:1.7;">'
          + '该订单已跳转支付宝收银台，无需再次扫码；支付结果会自动更新。</div>';
      }
    }
  }

  /* 电脑网站支付（支付宝 page.pay）：服务端给的是自提交表单 HTML，点按钮时插入并 submit */
  function renderPayButton(o) {
    const box = document.getElementById('rcQrTarget');
    if (!box) return;
    box.innerHTML = '';
    box.style.width = '100%';
    box.style.height = 'auto';
    box.style.border = '0';
    box.style.padding = '0';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-primary';
    btn.style.cssText = 'width:100%;height:46px;font-size:15px;font-weight:600;';
    btn.textContent = '去支付宝支付';
    btn.addEventListener('click', () => submitPayForm(o.pay_form));
    box.appendChild(btn);
    const hint = document.createElement('div');
    hint.style.cssText = 'font-size:12px;color:#667085;margin-top:10px;line-height:1.7;';
    hint.textContent = '点击后在支付宝页面完成付款；付款成功回到本页，余额会自动刷新（以支付宝异步通知为准）。';
    box.appendChild(hint);
  }

  function submitPayForm(htmlText) {
    if (!htmlText) return;
    const holder = document.createElement('div');
    holder.style.display = 'none';
    holder.innerHTML = htmlText;
    document.body.appendChild(holder);
    const form = holder.querySelector('form');
    if (form) form.submit();
  }

  function drawQr(codeUrl) {
    const box = document.getElementById('rcQrTarget');
    if (!box) return;
    box.innerHTML = '';
    if (window.QRCode) {
      try {
        // eslint-disable-next-line no-new
        new QRCode(box, {
          text: codeUrl,
          width: 200,
          height: 200,
          correctLevel: QRCode.CorrectLevel.M,
        });
        return;
      } catch (e) {
        console.warn('[Recharge] 二维码渲染失败', e);
      }
    }
    box.innerHTML = `<div style="font-size:12px;color:#b42318;word-break:break-all;padding:8px;">
      二维码组件未加载，请复制支付链接：<br>${esc(codeUrl)}</div>`;
  }

  function startCountdown(seconds) {
    state.expiresAt = Date.now() + Math.max(0, Number(seconds || 0)) * 1000;
    const tick = () => {
      const el = document.getElementById('rcCountdown');
      const left = Math.max(0, Math.floor((state.expiresAt - Date.now()) / 1000));
      if (el) {
        el.textContent = String(Math.floor(left / 60)).padStart(2, '0') + ':' + String(left % 60).padStart(2, '0');
      }
      if (left <= 0) {
        clearTimers();
        if (state.order && state.order.status === 'created') {
          state.order.status = 'closed';
          state.order.status_text = '<span class="rc-status closed">已超时</span>';
          paintOrder();
          setBanner('warn', '<strong>二维码已过期</strong>：请点「重新下单」生成新的二维码。');
        }
      }
    };
    tick();
    clearTimers();
    state.countdownTimer = setInterval(tick, 1000);
  }

  function schedulePoll() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    let delay = POLL_MS;
    if (state.fastRemaining > 0) {
      state.fastRemaining -= 1;
      delay = 800;                         // 回跳后：0.8s × 6 次 ≈ 前 5 秒加密轮询
    }
    state.pollTimer = setTimeout(pollOrder, delay);
  }

  /** 支付宝回跳后立刻确认一次，再进入加密轮询；回跳参数只当提示，到账仍以 notify 为准 */
  async function confirmOrderOnce(outTradeNo) {
    const res = await API.get('/api/pay/orders/' + encodeURIComponent(outTradeNo));
    if (!res || res.code !== 200 || !res.data) {
      setBanner('warn', '<strong>已从支付宝返回</strong>：订单状态暂未能确认，请稍后点「刷新」查看（到账以支付宝通知为准）。');
      return;
    }
    const d = res.data;
    state.order = Object.assign({}, state.order || {}, {
      out_trade_no: d.out_trade_no,
      status: d.status,
      points: d.points != null ? d.points : (state.order && state.order.points),
      amount_cents: d.amount_cents != null ? d.amount_cents : (state.order && state.order.amount_cents),
    });
    if (d.status === 'credited') {
      clearTimers();
      paintOrder();
      setBanner('ok', '<strong>充值成功</strong>：' + pointsText(state.order.points) + ' 点已到账。');
      Toast.show('充值成功，+' + pointsText(state.order.points) + ' 点', 'success');
      await refreshAll();
      return;
    }
    if (d.status === 'closed') {
      clearTimers();
      paintOrder();
      setBanner('warn', '<strong>订单已关闭</strong>：如已付款请稍后刷新，或联系管理员核对。');
      return;
    }
    paintOrder();
    state.fastRemaining = 6;
    if (d.status === 'paid') {
      setBanner('info', '<strong>已支付，正在入账</strong>：余额将在几秒内自动刷新。');
    } else {
      setBanner('info', '<strong>已从支付宝返回</strong>：正在确认到账状态（约 5 秒内自动刷新）。');
    }
    schedulePoll();
  }

  async function pollOrder() {
    const o = state.order;
    if (!o || !o.out_trade_no) return;
    if (o.status === 'credited' || o.status === 'closed' || o.status === 'refunded') return;
    if (Router && Router.currentRoute && Router.currentRoute.path !== '/recharge') return; // 离开页面即停

    const res = await API.get('/api/pay/orders/' + encodeURIComponent(o.out_trade_no));
    if (res && res.code === 200 && res.data) {
      state.order.status = res.data.status;
      if (res.data.paid_at) state.order.paid_at = res.data.paid_at;
      const st = document.getElementById('rcOrderStatus');
      if (st) st.innerHTML = statusBadge(res.data.status);
      if (res.data.status === 'credited') {
        clearTimers();
        setBanner('ok', '<strong>充值成功</strong>：' + pointsText(state.order.points) + ' 点已到账，余额已刷新。');
        Toast.show('充值成功，+' + pointsText(state.order.points) + ' 点', 'success');
        await refreshAll();
        return;
      }
      if (res.data.status === 'paid') {
        const stEl = document.getElementById('rcOrderStatus');
        if (stEl) stEl.innerHTML = '<span class="rc-status paid">已支付·入账中</span>';
      } else if (res.data.status === 'closed') {
        clearTimers();
        setBanner('warn', '<strong>订单已关闭</strong>，如需充值请重新下单。');
        return;
      }
    }
    schedulePoll();
  }

  async function createOrder() {
    const ready = readiness();
    if (!ready.canPay) {
      setBanner('warn', '<strong>在线支付通道尚未开通</strong>，暂时无法在线充值，请走人工充值。');
      return;
    }
    const channel = ready.channel || 'wxpay';
    const channelText = channelName(channel);
    const points = Number(state.points || 0);
    if (!points) { Toast.show('请先选择充值档位', 'warning'); return; }
    const btn = document.getElementById('rcPayBtn');
    if (btn) { btn.disabled = true; btn.textContent = '生成中...'; }
    try {
      const body = { points, channel };
      if (state.isAdmin && state.accountId) body.account_id = state.accountId;
      const res = await API.post('/api/pay/orders', body);
      const hasPayForm = !!(res && res.data && res.data.pay_form);
      if (!res || res.code !== 200 || !res.data || (!res.data.code_url && !hasPayForm)) {
        const msg = (res && res.msg) || '下单失败';
        if (res && res.code === 503) {
          setBanner('warn', `<strong>${esc(channelText)}支付通道尚未开通</strong>：` + esc(msg) + '（请先走人工充值）');
        } else if (res && res.code === 429) {
          Toast.show(msg, 'warning');
        } else {
          setBanner('error', '<strong>下单失败</strong>：' + esc(msg));
        }
        return;
      }
      state.order = res.data;
      state.order.status = 'created';
      paintOrder();
      startCountdown(res.data.expires_in);
      schedulePoll();
      if (res.data.pay_form) {
        setBanner('info', `<b>${esc(channelText)}支付页已就绪</b>：点右侧「去支付宝支付」完成付款，<b>15 分钟内有效</b>，支付后余额自动到账。`);
      } else {
        setBanner('info', `二维码已生成：请用${esc(channelText)}扫一扫支付，<b>15 分钟内有效</b>，支付后余额自动到账。`);
      }
    } finally {
      if (btn) { btn.disabled = !canPay(); btn.textContent = '生成充值二维码'; }
    }
  }

  async function closeOrder() {
    const o = state.order;
    if (!o) return;
    const res = await API.post('/api/pay/orders/' + encodeURIComponent(o.out_trade_no) + '/close', {});
    clearTimers();
    if (res && res.code === 200) {
      Toast.show('订单已关闭', 'success');
    } else {
      Toast.show((res && res.msg) || '关闭失败', 'warning');
    }
    resetOrder(false);
  }

  function resetOrder(refreshList) {
    clearTimers();
    state.order = null;
    state.expiresAt = 0;
    const card = document.getElementById('rcOrderCard');
    if (card) card.style.display = 'none';
    const cd = document.getElementById('rcCountdown');
    if (cd) cd.textContent = '--:--';
    renderStatusBanner();
    if (refreshList) loadOrders();
  }

  /* ---------------- 交互 ---------------- */

  function selectTier(value) {
    if (!canPay()) { Toast.show('支付通道未开通，暂不可在线充值', 'warning'); return; }
    state.points = Number(value);
    renderTiers();
  }

  function onAccountChange(value) {
    state.accountId = Number(value) || null;
    if (state.order) resetOrder(false);
    renderBalance();
    loadOrders();
    const sel = document.getElementById('rcTopupAccount');
    if (sel && state.accountId) sel.value = String(state.accountId);
  }

  async function adminTopup() {
    if (!state.isAdmin) return;
    const accSel = document.getElementById('rcTopupAccount');
    const ptsEl = document.getElementById('rcTopupPoints');
    const reasonEl = document.getElementById('rcTopupReason');
    const accountId = Number(accSel && accSel.value);
    const points = Number(ptsEl && ptsEl.value);
    if (!accountId) { Toast.show('请选择要充值的账号', 'warning'); return; }
    if (!Number.isFinite(points) || points <= 0) { Toast.show('请输入大于 0 的点数', 'warning'); return; }
    const res = await API.post('/api/image-billing/accounts/' + accountId + '/topup', {
      points,
      reason: (reasonEl && reasonEl.value.trim()) || '管理员人工充值',
    });
    if (res && res.code === 200) {
      Toast.show('已充值 ' + pointsText(points) + ' 点', 'success');
      if (ptsEl) ptsEl.value = '';
      if (reasonEl) reasonEl.value = '';
      await refreshAll();
    } else {
      Toast.show((res && res.msg) || '充值失败', 'error');
    }
  }

  async function refreshAll() {
    await loadUser();
    if (!state.user) {
      setBanner('error', '登录状态已失效，请重新登录。');
      return;
    }
    await loadAccounts();
    await loadStatus();
    renderBalance();
    renderAdminVisibility();
    await loadOrders();
    if (state.isAdmin) {
      const card = document.getElementById('rcTopupCard');
      if (card) card.style.display = '';
      await loadTopups();
    }
  }

  /* ---------------- 生命周期 ---------------- */

  async function init() {
    state.loaded = false;
    const ok = await loadUser();
    if (!ok) {
      setBanner('warn', '请先登录后再充值。');
      return;
    }
    state.accountId = state.accountId || state.user.id;
    await loadAccounts();
    renderBalance();
    renderAccountPicker();
    await loadStatus();
    await loadOrders();
    if (state.isAdmin) {
      const card = document.getElementById('rcTopupCard');
      if (card) card.style.display = '';
      await loadTopups();
    }
    renderTiers();
    renderBalance();
    renderAdminVisibility();
    try {
      const q = (Router && Router.currentRoute && Router.currentRoute.query) || {};
      if (q.alipay === 'return') {
        await confirmOrderOnce(q.out_trade_no || '');
      }
    } catch (e) { /* 忽略 */ }
    // 曾在本页发起、仍在 pending 的订单：不自动恢复（刷新后一律以 15 分钟过期为准）
    state.loaded = true;
  }

  function destroy() {
    clearTimers();
    state.order = null;
    state.expiresAt = 0;
    state.status = null;
    state.statusError = '';
    state.points = null;
    state.banner = null;
  }

  if (typeof Router !== 'undefined' && Router.register) {
    Router.register('/recharge', () => {
      destroy();
      const html = renderPage();
      requestAnimationFrame(() => { init(); });
      return html;
    });
  }

  return {
    refreshAll,
    selectTier,
    createOrder,
    closeOrder,
    resetOrder,
    adminTopup,
    onAccountChange,
    _state: state,
  };
})();

window.RechargePage = RechargePage;
