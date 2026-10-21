/**
 * 模态弹窗组件
 * 统一管理弹窗的显示、隐藏与内容渲染
 *
 * 关键约束（历史事故修复）：
 * 1. Modal 是「单栈」结构，close() 默认关闭栈顶。业务代码如果想关闭某个
 *    特定子弹窗，必须用 Modal.close({ target }) 显式指定，否则子弹窗被
 *    ESC/遮罩/双击提前关掉后，close() 会误关下层的「编辑商品」大弹窗，
 *    触发 beforeClose 守卫 → 用户以为在选图，结果整个编辑弹窗被关掉丢内容。
 * 2. beforeClose 守卫可能 push 一个 confirm 子弹窗，因此 close() 必须
 *    防重入：同一个 overlay 只会被真正关闭一次。
 * 3. confirm()/alert() 被 ESC 或遮罩关闭时也必须 resolve，否则调用方
 *    await 永久挂起（保存并发布整条链路会卡死）。
 */
const Modal = (() => {
  // 弹窗堆栈，支持嵌套弹窗
  const stack = [];
  const BASE_Z_INDEX = 999;

  // 比 Modal 更高层级的自定义浮层：它们打开时 ESC 不应触发 Modal 关闭
  const HIGHER_LAYER_SELECTORS = [
    '#richEditorHost', '#richPreviewHost', '#categorySelectorModal', '#batchProgressModal',
  ];

  function _getTop() {
    return stack.length > 0 ? stack[stack.length - 1] : null;
  }

  /** 是否存在比 Modal 更高层级的浮层（富文本编辑器、类目选择器等） */
  function _hasHigherLayer() {
    for (const sel of HIGHER_LAYER_SELECTORS) {
      const el = document.querySelector(sel);
      if (el && el.offsetParent !== null) return true;
    }
    return false;
  }

  /** 解析 close 的目标 overlay：显式 target > 栈顶 */
  function _resolveTarget(target) {
    if (!target) return _getTop();
    if (typeof target === 'string') return document.querySelector(target);
    if (target instanceof Element) {
      // 允许传弹窗内部元素，自动上溯到 overlay
      return target.closest('.modal-overlay') || target;
    }
    return _getTop();
  }

  /**
   * 显示模态弹窗
   * @param {object} options - 弹窗配置
   * @param {string} options.title - 弹窗标题
   * @param {string} options.body - 弹窗内容（HTML字符串）
   * @param {Array} options.footer - 底部按钮配置 [{text, class, onClick}]
   * @param {string} [options.size] - 尺寸：sm/md/lg/xl
   * @param {function} [options.onClose] - 关闭回调
   * @param {function} [options.onCancel] - 被 ESC/遮罩/关闭按钮关闭时的回调（confirm/alert 用）
   * @param {function} [options.beforeClose] - 关闭前守卫，返回 false 阻止关闭；可返回 Promise<boolean>
   * @returns {Element} 本次创建的 overlay 元素（可用于 Modal.close({target})）
   */
  function show(options = {}) {
    const overlay = document.createElement('div');
    const sizeClass = options.size === 'xl' ? 'modal-xl' : options.size === 'lg' ? 'modal-lg' : options.size === 'sm' ? 'modal-sm' : options.size === 'xs' ? 'modal-xs' : '';
    overlay.className = `modal-overlay${options.size ? ' ' + sizeClass : ''}`;
    // 根据堆栈深度设置 z-index，确保新弹窗在旧弹窗之上
    overlay.style.zIndex = BASE_Z_INDEX + stack.length;
    overlay.innerHTML = `
      <div class="modal-dialog" role="dialog" aria-modal="true" aria-label="${options.title || '对话框'}">
        <div class="modal-header">
          <h2>${options.title || '提示'}</h2>
          <button class="modal-close" aria-label="关闭">&times;</button>
        </div>
        <div class="modal-body">${options.body || ''}</div>
        ${options.footer ? `
          <div class="modal-footer">
            ${options.footer.map(btn =>
              `<button class="btn ${btn.class || 'btn-primary'}" data-action="${btn.text}">${btn.text}</button>`
            ).join('')}
          </div>
        ` : ''}
      </div>`;

    document.body.appendChild(overlay);

    // 关闭回调
    overlay._onClose = options.onClose;
    // 取消回调（ESC / 点遮罩 / 右上角 X）
    overlay._onCancel = options.onCancel;
    // 关闭前守卫
    overlay._beforeClose = options.beforeClose;
    // 防重入标记
    overlay._closing = false;
    // 标记：footer 按钮触发的关闭不算「取消」
    overlay._footerAction = false;

    // 压入堆栈
    stack.push(overlay);

    // 关闭按钮（视为取消）
    overlay.querySelector('.modal-close').addEventListener('click', () => close());

    // 点击遮罩层关闭（视为取消）
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close();
    });

    // ESC 键关闭（仅关闭最顶层弹窗；且更高层级浮层打开时不响应）
    const escHandler = (e) => {
      if (e.key !== 'Escape') return;
      if (_getTop() !== overlay) return;
      if (_hasHigherLayer()) return;
      close();
    };
    document.addEventListener('keydown', escHandler);
    overlay._escHandler = escHandler;

    // 底部按钮事件
    if (options.footer) {
      overlay.querySelectorAll('.modal-footer .btn').forEach((btn, idx) => {
        btn.addEventListener('click', () => {
          const actionConfig = options.footer[idx];
          // 先标记为 footer 动作：即使 onClick 内部同步调用了 close()，
          // 也不会被当成「取消」触发 onCancel。
          overlay._footerAction = true;
          if (actionConfig.onClick) actionConfig.onClick();
          else close({ target: overlay });
        });
      });
    }

    // 重新初始化图标
    if (window.lucide) lucide.createIcons();

    // 表单元素聚焦
    const firstInput = overlay.querySelector('.form-input, .form-textarea');
    if (firstInput) setTimeout(() => firstInput.focus(), 100);

    // 打开回调
    if (options.onOpen) options.onOpen();

    return overlay;
  }

  /**
   * 关闭指定弹窗（默认栈顶）
   * @param {object} [opts] - { force?: boolean 跳过守卫, target?: Element|string 指定要关闭的弹窗 }
   * @returns {Promise<boolean>} 是否真正关闭
   */
  async function close(opts = {}) {
    const overlay = _resolveTarget(opts.target);
    if (!overlay) return false;

    // 防重入：守卫 await 期间（或 200ms 卸载动画期间）重复调用直接忽略，
    // 避免「双击确认按钮」关掉下一层弹窗。
    if (overlay._closing) return false;

    // 执行关闭前守卫（force=true 时跳过，用于程序化确认后关闭）
    if (!opts.force && typeof overlay._beforeClose === 'function') {
      overlay._closing = true;
      let result;
      try {
        result = await overlay._beforeClose();
      } catch (e) {
        console.warn('[Modal] beforeClose 守卫异常:', e);
        overlay._closing = false;
        return false;
      }
      if (result === false) {
        overlay._closing = false;
        return false;
      }
    } else {
      overlay._closing = true;
    }

    overlay.classList.add('closing');

    // 清理事件监听
    if (overlay._escHandler) {
      document.removeEventListener('keydown', overlay._escHandler);
      overlay._escHandler = null;
    }

    // 从堆栈中移除
    const idx = stack.indexOf(overlay);
    if (idx !== -1) stack.splice(idx, 1);

    // 取消语义：ESC / 点遮罩 / 右上角 X（非 footer 按钮、非 force）时通知调用方，
    // 保证 confirm()/alert() 的 Promise 一定被 resolve。
    if (!overlay._footerAction && !opts.force && typeof overlay._onCancel === 'function') {
      try { overlay._onCancel(); } catch (e) { console.warn('[Modal] onCancel 异常:', e); }
    }
    overlay._onCancel = null;

    setTimeout(() => {
      if (overlay._onClose) overlay._onClose();
      overlay.remove();
    }, 200);
    return true;
  }

  /**
   * 强制关闭（跳过 beforeClose 守卫），用于程序化确认后关闭
   * @param {object} [opts] - { target?: Element|string }
   */
  function forceClose(opts = {}) {
    return close({ force: true, target: opts.target });
  }

  /** 当前栈顶 overlay（子弹窗关闭时用 Modal.close({target}) 指定） */
  function currentOverlay() {
    return _getTop();
  }

  /**
   * 确认对话框
   * @param {string} message - 提示消息
   * @returns {Promise<boolean>} ESC / 遮罩 / 取消 都会 resolve(false)
   */
  function confirm(message) {
    return new Promise((resolve) => {
      let settled = false;
      const done = (val) => { if (!settled) { settled = true; resolve(val); } };
      show({
        title: '确认操作',
        size: 'sm',
        body: `<p style="font-size:14px;color:#6B7280;white-space:pre-wrap;">${message}</p>`,
        onCancel: () => done(false),
        onClose: () => done(false),
        footer: [
          { text: '取消', class: 'btn-ghost', onClick: () => { const ov = _getTop(); done(false); close({ target: ov }); } },
          { text: '确定', class: 'btn-primary', onClick: () => { const ov = _getTop(); done(true); close({ target: ov }); } },
        ],
      });
    });
  }

  /**
   * 提示对话框（仅确定按钮）
   * @param {string} message
   * @param {string} [title]
   * @returns {Promise<boolean>}
   */
  function alert(message, title) {
    return new Promise((resolve) => {
      let settled = false;
      const done = () => { if (!settled) { settled = true; resolve(true); } };
      show({
        title: title || '提示',
        size: 'sm',
        body: `<p style="font-size:14px;color:#6B7280;white-space:pre-wrap;">${message}</p>`,
        onCancel: done,
        onClose: done,
        footer: [
          { text: '知道了', class: 'btn-primary', onClick: () => { const ov = _getTop(); done(); close({ target: ov }); } },
        ],
      });
    });
  }

  return { show, close, forceClose, confirm, alert, currentOverlay };
})();
