/**
 * OZON ERP - Popup 脚本
 */

document.addEventListener('DOMContentLoaded', () => {
  const statusDot = document.getElementById('statusDot');
  const statusText = document.getElementById('statusText');
  const openPanelBtn = document.getElementById('openPanelBtn');
  const openDesktopBtn = document.getElementById('openDesktopBtn');
  const apiUrlInput = document.getElementById('apiUrl');
  const historyList = document.getElementById('historyList');
  const extensionVersion = document.getElementById('extensionVersion');
  const connectionStatus = document.getElementById('connectionStatus');
  const updateStatus = document.getElementById('updateStatus');
  const apiAddressValue = document.getElementById('apiAddressValue');
  const operatorName = document.getElementById('operatorName');
  const targetStore = document.getElementById('targetStore');
  const batchId = document.getElementById('batchId');
  const storeSelect = document.getElementById('storeSelect');
  const confirmStoreBtn = document.getElementById('confirmStoreBtn');
  const switchStoreBtn = document.getElementById('switchStoreBtn');
  const contextKey = OZON_CONFIG.COLLECTION_CONTEXT_KEY || 'collection_context';
  const contextStateKey = OZON_CONFIG.COLLECTION_CONTEXT_STATE_KEY || 'collection_context_state';
  const installedVersion = chrome.runtime.getManifest().version;
  let desktopRunning = false;
  let hasConfirmedContext = false;
  let contextState = 'unknown';
  apiUrlInput.value = OZON_CONFIG.LOCAL_API_URL;
  extensionVersion.textContent = installedVersion;
  apiAddressValue.textContent = apiUrlInput.value;

  // 加载保存的API地址
  chrome.storage.local.get('desktop_api_url', (result) => {
    if (result.desktop_api_url) {
      apiUrlInput.value = result.desktop_api_url;
      apiAddressValue.textContent = result.desktop_api_url;
    }
  });

  // 保存API地址
  apiUrlInput.addEventListener('change', () => {
    const nextApiUrl = apiUrlInput.value.trim();
    chrome.storage.local.set({ desktop_api_url: nextApiUrl });
    apiAddressValue.textContent = nextApiUrl;
    checkDesktopStatus();
    refreshCollectionContext();
  });

  // 检查桌面软件状态
  async function checkDesktopStatus() {
    const apiUrl = apiUrlInput.value.trim();
    try {
      // 通过 background service worker 检查（避免扩展页面 CORS 问题）
      const result = await new Promise((resolve) => {
        chrome.runtime.sendMessage({ type: 'CHECK_DESKTOP_STATUS', apiUrl }, (response) => {
          resolve(response || { running: false });
        });
      });
      if (result.running) {
        desktopRunning = true;
        statusDot.classList.add('online');
        statusDot.classList.remove('offline');
        statusText.textContent = result.logged_in ? 'OZON ERP已连接' : 'OZON ERP已连接（未登录）';
        connectionStatus.textContent = 'ERP 已连接';
      } else {
        desktopRunning = false;
        statusDot.classList.add('offline');
        statusDot.classList.remove('online');
        statusText.textContent = 'OZON ERP未启动';
        connectionStatus.textContent = 'ERP 未连接';
      }
      updateOpenPanelState();
      renderVersionStatus(updateStatus, result.config || {}, installedVersion);
      apiAddressValue.textContent = (result.config && result.config.api_url) || apiUrl;
      refreshCollectionContext();
    } catch (e) {
      statusDot.classList.add('offline');
      statusDot.classList.remove('online');
      statusText.textContent = 'OZON ERP未启动';
      connectionStatus.textContent = 'ERP 未连接';
      updateStatus.textContent = '无法获取版本要求';
      desktopRunning = false;
      renderUnavailableContext();
    }
  }

  checkDesktopStatus();

  async function refreshCollectionContext() {
    await renderStoredContext();
    const apiUrl = apiUrlInput.value.trim();
    try {
      const result = await new Promise((resolve) => {
        chrome.runtime.sendMessage({ type: 'LOAD_ASSIGNMENT_STORES', apiUrl }, (response) => {
          resolve(response || { success: false, error: '未收到响应' });
        });
      });
      if (!result.success) throw new Error(result.error || '无法加载店铺列表');
      const data = result.data || {};
      await setContextState('available_unconfirmed');
      operatorName.textContent = data.operator_name || operatorName.textContent;
      renderStoreOptions(data.stores || []);
      const current = await getStoredContext();
      if (current?.store_id) {
        storeSelect.value = String(current.store_id);
        await setContextState('confirmed');
      } else {
        renderPendingContext();
      }
    } catch (e) {
      renderUnavailableContext();
    }
  }

  function renderStoreOptions(stores) {
    storeSelect.innerHTML = '';
    if (!stores.length) {
      storeSelect.appendChild(new Option('待归属', ''));
      confirmStoreBtn.disabled = true;
      return;
    }
    stores.forEach((store) => {
      storeSelect.appendChild(new Option(store.name, String(store.id)));
    });
    confirmStoreBtn.disabled = false;
  }

  async function createCollectionContext(storeId) {
    const apiUrl = apiUrlInput.value.trim();
    const result = await new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: 'CONFIRM_COLLECTION_CONTEXT', apiUrl, storeId: Number(storeId) }, (response) => {
        resolve(response || { success: false, error: '未收到响应' });
      });
    });
    if (!result.success) throw new Error(result.error || '无法创建采集批次');
    await setContextState('confirmed');
    renderContext(result.context);
    return result.context;
  }

  async function clearCollectionContext() {
    await new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: 'CLEAR_COLLECTION_CONTEXT' }, () => resolve());
    });
    await setContextState('available_unconfirmed');
    renderPendingContext();
  }

  async function setContextState(nextState) {
    contextState = nextState;
    await chrome.storage.local.set({ [contextStateKey]: nextState });
  }

  async function getStoredContext() {
    const result = await new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: 'GET_COLLECTION_CONTEXT' }, (response) => {
        resolve(response || { success: false, context: null });
      });
    });
    return result.context || null;
  }

  async function renderStoredContext() {
    const context = await getStoredContext();
    if (context) renderContext(context);
    else renderPendingContext();
  }

  function renderContext(context) {
    operatorName.textContent = context.operator_name || '待归属';
    targetStore.textContent = context.store_name || '待归属';
    batchId.textContent = context.collection_batch_id || '待归属';
    hasConfirmedContext = Boolean(context.store_id && context.operator_member_id && context.collection_batch_id);
    contextState = hasConfirmedContext ? 'confirmed' : contextState;
    updateOpenPanelState();
  }

  function renderPendingContext() {
    operatorName.textContent = '待归属';
    targetStore.textContent = '待归属';
    batchId.textContent = '待归属';
    hasConfirmedContext = false;
    updateOpenPanelState();
  }

  async function renderUnavailableContext() {
    await setContextState('unavailable');
    await chrome.storage.local.remove(contextKey);
    operatorName.textContent = '待归属';
    targetStore.textContent = 'ERP未连接';
    batchId.textContent = '待归属';
    hasConfirmedContext = false;
    updateOpenPanelState();
  }

  function updateOpenPanelState() {
    const mustConfirm = contextState === 'available_unconfirmed' && !hasConfirmedContext;
    openPanelBtn.disabled = mustConfirm;
    openPanelBtn.textContent = mustConfirm ? '请先确认店铺' : '在当前页面打开采集面板';
  }

  confirmStoreBtn.addEventListener('click', async () => {
    const storeId = storeSelect.value;
    if (!storeId) return;
    try {
      await clearCollectionContext();
      await createCollectionContext(storeId);
    } catch (e) {
      renderPendingContext();
      alert('无法创建采集批次，将按待归属采集');
    }
  });

  switchStoreBtn.addEventListener('click', async () => {
    await clearCollectionContext();
  });

  // 打开采集面板
  openPanelBtn.addEventListener('click', async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const context = await getStoredContext();
    if (contextState === 'available_unconfirmed' && (!context?.store_id || !context?.operator_member_id || !context?.collection_batch_id)) {
      renderPendingContext();
      alert('请先确认店铺');
      return;
    }
    if (!['1688.com', 'taobao.com', 'tmall.com'].some(host => tab.url.includes(host))) {
      alert('请在1688或淘宝商品详情页使用此功能');
      return;
    }
    chrome.tabs.sendMessage(tab.id, { type: 'OPEN_PANEL' }, (response) => {
      if (chrome.runtime.lastError) {
        // content script可能还没注入，刷新页面
        alert('请刷新商品页面后重试');
      } else {
        window.close();
      }
    });
  });

  // 启动桌面软件
  openDesktopBtn.addEventListener('click', () => {
    // 尝试通过自定义协议打开
    chrome.tabs.create({ url: 'ozon://open', active: false }, (tab) => {
      setTimeout(() => {
        chrome.tabs.remove(tab.id);
      }, 1000);
    });
    setTimeout(checkDesktopStatus, 2000);
  });

  // 加载采集历史
  chrome.storage.local.get('collect_history', (result) => {
    const history = result.collect_history || [];
    if (history.length === 0) return;
    historyList.innerHTML = history.slice(0, 5).map(item => `
      <div class="history-item">
        <div class="htitle">${item.title}</div>
        <div class="htime">${item.time}</div>
      </div>
    `).join('');
  });

  // 帮助链接
  document.getElementById('helpLink').addEventListener('click', (e) => {
    e.preventDefault();
    chrome.tabs.create({ url: 'https://www.ozon.ru' }); // 后续替换为帮助文档
  });
});

function renderVersionStatus(updateStatusElement, config, installedVersion) {
  const minVersion = config.extension_min_version || installedVersion;
  const recommendedVersion = config.extension_recommended_version || minVersion;

  if (compareVersions(installedVersion, minVersion) < 0) {
    updateStatusElement.textContent = '低于最低版本，请立即更新';
    return;
  }

  if (compareVersions(installedVersion, recommendedVersion) < 0) {
    updateStatusElement.textContent = '建议更新到 ' + recommendedVersion;
    return;
  }

  updateStatusElement.textContent = '已是最新要求版本';
}

function compareVersions(left, right) {
  const leftParts = String(left || '').split('.').map(Number);
  const rightParts = String(right || '').split('.').map(Number);
  const length = Math.max(leftParts.length, rightParts.length);

  for (let index = 0; index < length; index += 1) {
    const leftValue = leftParts[index] || 0;
    const rightValue = rightParts[index] || 0;
    if (leftValue > rightValue) return 1;
    if (leftValue < rightValue) return -1;
  }

  return 0;
}
