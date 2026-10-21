/**
 * OZON ERP - Background Service Worker
 * 处理扩展后台逻辑、消息通信
 */

if (typeof importScripts === 'function') {
  importScripts('config.js');
}

const RETRY_ALARM_NAME = 'ozon_collect_retry';
const PENDING_QUEUE_KEY = 'collect_pending_queue';
const DEFAULT_QUEUE_OPTIONS = { maxItems: 20, maxAttempts: 2 };
const LOCAL_API_URL = typeof OZON_CONFIG !== 'undefined' ? OZON_CONFIG.LOCAL_API_URL : '';
const EXTENSION_RUNTIME_CONFIG_KEY = 'extension_runtime_config';
const COLLECTION_CONTEXT_KEY = typeof OZON_CONFIG !== 'undefined' ? OZON_CONFIG.COLLECTION_CONTEXT_KEY : 'collection_context';
const DESKTOP_API_URL_KEY = 'desktop_api_url';

function createPendingQueue(options = {}) {
  const settings = {
    maxItems: options.maxItems ?? DEFAULT_QUEUE_OPTIONS.maxItems,
    maxAttempts: options.maxAttempts ?? DEFAULT_QUEUE_OPTIONS.maxAttempts,
  };
  let items = Array.isArray(options.items) ? options.items.map(cloneQueueItem) : [];

  function cloneQueueItem(item) {
    return {
      source_url: item.source_url,
      payload: item.payload,
      attempts: Number(item.attempts || 0),
      queued_at: item.queued_at,
    };
  }

  function findIndex(sourceUrl) {
    return items.findIndex((item) => item.source_url === sourceUrl);
  }

  return {
    enqueue(payload) {
      if (!payload?.source_url) {
        throw new Error('source_url is required');
      }
      const nextItem = {
        source_url: payload.source_url,
        payload,
        attempts: 0,
        queued_at: new Date().toISOString(),
      };
      const existingIndex = findIndex(payload.source_url);
      if (existingIndex >= 0) {
        items.splice(existingIndex, 1);
      }
      items.push(nextItem);
      if (items.length > settings.maxItems) {
        items = items.slice(items.length - settings.maxItems);
      }
      return cloneQueueItem(nextItem);
    },
    pending() {
      return items.map(cloneQueueItem);
    },
    nextRetry() {
      return items.length ? cloneQueueItem(items[0]) : null;
    },
    markFailed(sourceUrl) {
      const index = findIndex(sourceUrl);
      if (index < 0) return null;
      const current = items[index];
      const attempts = Number(current.attempts || 0) + 1;
      if (attempts >= settings.maxAttempts) {
        items.splice(index, 1);
        return null;
      }
      items[index] = {
        ...current,
        attempts,
      };
      return cloneQueueItem(items[index]);
    },
    markSucceeded(sourceUrl) {
      const index = findIndex(sourceUrl);
      if (index < 0) return false;
      items.splice(index, 1);
      return true;
    },
  };
}

function getChromeStorageLocal() {
  return typeof chrome !== 'undefined' && chrome.storage?.local ? chrome.storage.local : null;
}

async function loadPendingQueue() {
  const storage = getChromeStorageLocal();
  const queue = createPendingQueue(DEFAULT_QUEUE_OPTIONS);
  if (!storage) return queue;
  const result = await storage.get(PENDING_QUEUE_KEY);
  return createPendingQueue({
    ...DEFAULT_QUEUE_OPTIONS,
    items: Array.isArray(result[PENDING_QUEUE_KEY]) ? result[PENDING_QUEUE_KEY] : [],
  });
}

async function getApiBaseUrl() {
  const storage = getChromeStorageLocal();
  if (!storage) return LOCAL_API_URL;
  const result = await storage.get(DESKTOP_API_URL_KEY);
  return result[DESKTOP_API_URL_KEY] || LOCAL_API_URL;
}

async function savePendingQueue(queue) {
  const storage = getChromeStorageLocal();
  if (!storage) return;
  await storage.set({ [PENDING_QUEUE_KEY]: queue.pending() });
}

async function getCollectionContext() {
  const storage = getChromeStorageLocal();
  if (!storage) return null;
  const result = await storage.get(COLLECTION_CONTEXT_KEY);
  return result[COLLECTION_CONTEXT_KEY] || null;
}

async function setCollectionContext(context) {
  const storage = getChromeStorageLocal();
  if (!storage) return context;
  await storage.set({ [COLLECTION_CONTEXT_KEY]: context });
  return context;
}

async function clearCollectionContext() {
  const storage = getChromeStorageLocal();
  if (!storage) return;
  await storage.remove(COLLECTION_CONTEXT_KEY);
}

function ensureRetryAlarm() {
  if (typeof chrome === 'undefined' || !chrome.alarms?.create) return;
  chrome.alarms.create(RETRY_ALARM_NAME, { periodInMinutes: 5 });
}

async function enqueuePendingCollection(payload) {
  const queue = await loadPendingQueue();
  const queued = queue.enqueue(payload);
  await savePendingQueue(queue);
  ensureRetryAlarm();
  return queued;
}

async function processPendingRetry() {
  const queue = await loadPendingQueue();
  const nextItem = queue.nextRetry();
  if (!nextItem) return { processed: false };

  const result = await submitCollection(nextItem.payload);
  if (result.success) {
    queue.markSucceeded(nextItem.source_url);
  } else {
    queue.markFailed(nextItem.source_url);
  }
  await savePendingQueue(queue);
  return {
    processed: true,
    success: result.success,
    sourceUrl: nextItem.source_url,
  };
}

async function findWorkbenchToken(apiUrl) {
  if (typeof chrome === 'undefined' || !chrome.tabs?.query || !chrome.scripting?.executeScript) {
    return '';
  }
  const tabs = await chrome.tabs.query({});
  const target = tabs.find((tab) => typeof tab.url === 'string' && tab.url.startsWith(apiUrl));
  if (!target?.id) return '';
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: target.id },
      func: () => localStorage.getItem('ozon_token') || '',
    });
    return typeof result?.result === 'string' ? result.result : '';
  } catch (error) {
    return '';
  }
}

async function fetchWorkbench(path, options = {}) {
  const apiUrl = options.apiUrl || await getApiBaseUrl();
  const headers = new Headers(options.headers || {});
  if (!headers.has('Content-Type') && options.body) {
    headers.set('Content-Type', 'application/json');
  }
  const token = await findWorkbenchToken(apiUrl);
  if (token) {
    headers.set('Authorization', 'Bearer ' + token);
  }
  const response = await fetch(apiUrl + path, {
    method: options.method || 'GET',
    headers,
    body: options.body,
    signal: options.signal,
  });
  return { response, apiUrl, token };
}

async function fetchAssignmentStores(apiUrl) {
  const { response } = await fetchWorkbench('/api/assignment/stores', { apiUrl });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || ('HTTP ' + response.status));
  }
  return data;
}

async function createCollectionContext(storeId, apiUrl) {
  const existing = await getCollectionContext();
  if (existing && Number(existing.store_id) !== Number(storeId)) {
    await clearCollectionContext();
  }
  const { response } = await fetchWorkbench('/api/collection-contexts', {
    apiUrl,
    method: 'POST',
    body: JSON.stringify({ store_id: Number(storeId) }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || ('HTTP ' + response.status));
  }
  return setCollectionContext({
    ...data,
    confirmed_at: new Date().toISOString(),
  });
}

if (typeof chrome !== 'undefined' && chrome.runtime?.onMessage) {
  // 安装时初始化
  chrome.runtime.onInstalled.addListener(() => {
    console.log('[OZON ERP] 扩展已安装');
    ensureRetryAlarm();
  });

  chrome.runtime.onStartup?.addListener(() => {
    ensureRetryAlarm();
  });

  // 监听来自content script和popup的消息
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log('[OZON ERP] 收到消息:', message.type);

    switch (message.type) {
      case 'SUBMIT_COLLECTION':
        // 由 service worker 提交采集数据（绕过页面混合内容限制）
        submitCollection(message.payload)
          .then(async (result) => {
            if (result.success) {
              sendResponse(result);
              return;
            }
            await enqueuePendingCollection(message.payload);
            sendResponse({ success: false, queued: true, error: result.error });
          })
          .catch((error) => sendResponse({ success: false, error: error.message }));
        return true; // 异步响应

      case 'COLLECT_IMAGES':
        // 商品图片采集（服务端下载入库 ERP 图库，同样走 service worker 代发）
        submitImageCollection(message.payload)
          .then((result) => sendResponse(result))
          .catch((error) => sendResponse({ success: false, error: error.message }));
        return true; // 异步响应

      case 'COLLECT_SUCCESS':
        // 采集成功，可以在这里做通知或打开结果页
        chrome.notifications?.create?.({
          type: 'basic',
          iconUrl: 'icons/icon48.png',
          title: 'OZON ERP',
          message: '商品采集成功，已发送到OZON ERP',
        });
        // 采集成功后自动打开工作台，避免业务员只看到浏览器内提示而找不到结果。
        getApiBaseUrl()
          .then((apiUrl) => chrome.tabs.create({ url: apiUrl + '/', active: true }))
          .finally(() => sendResponse({ success: true }));
        return true;
 
      case 'GET_COLLECTION_CONTEXT':
        getCollectionContext().then((context) => sendResponse({ success: true, context }));
        return true;

      case 'CLEAR_COLLECTION_CONTEXT':
        clearCollectionContext().then(() => sendResponse({ success: true }));
        return true;

      case 'LOAD_ASSIGNMENT_STORES':
        fetchAssignmentStores(message.apiUrl || LOCAL_API_URL)
          .then((data) => sendResponse({ success: true, data }))
          .catch((error) => sendResponse({ success: false, error: error.message }));
        return true;

      case 'CONFIRM_COLLECTION_CONTEXT':
        createCollectionContext(message.storeId, message.apiUrl || LOCAL_API_URL)
          .then((context) => sendResponse({ success: true, context }))
          .catch((error) => sendResponse({ success: false, error: error.message }));
        return true;

      case 'CHECK_DESKTOP_STATUS':
        // 检查 ERP 是否运行
        checkDesktopStatus(message.apiUrl || LOCAL_API_URL).then(sendResponse);
        return true; // 异步响应

      case 'OPEN_DESKTOP_APP':
        // （已废弃）通过协议打开老桌面软件
        chrome.tabs.create({ url: 'ozon://open' }, () => {
          sendResponse({ success: true });
        });
        return true;

      default:
        sendResponse({ success: false, error: '未知消息类型' });
    }
  });

  chrome.alarms?.onAlarm.addListener((alarm) => {
    if (alarm?.name !== RETRY_ALARM_NAME) return;
    processPendingRetry().catch((error) => {
      console.error('[OZON ERP] 重试队列处理失败:', error);
    });
  });
}

// 提交采集数据到OZON ERP（service worker 发起，带超时与错误处理）
async function submitCollection(payload) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
  try {
    const { response } = await fetchWorkbench('/api/collect/from-extension', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      return { success: true, data };
    }
    return { success: false, error: data.detail || ('HTTP ' + response.status) };
  } catch (e) {
    return { success: false, error: e.name === 'AbortError' ? '请求超时' : e.message };
  } finally {
    clearTimeout(timer);
  }
}

// 采集商品图片到 ERP 图库（服务端下载，20 张最长约 1 分钟，超时放宽）
async function submitImageCollection(payload) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120000);
  try {
    const { response } = await fetchWorkbench('/api/collect/images', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      return { success: true, data };
    }
    return { success: false, error: data.detail || ('HTTP ' + response.status) };
  } catch (e) {
    return { success: false, error: e.name === 'AbortError' ? '图片采集超时' : e.message };
  } finally {
    clearTimeout(timer);
  }
}

// 检查 ERP 运行状态
async function checkDesktopStatus(apiUrl) {
  try {
    const config = await fetchRuntimeConfig(apiUrl);
    if (config.online) {
      const token = await findWorkbenchToken(apiUrl);
      if (token) {
        try {
          const { response } = await fetchWorkbench('/api/auth/me', { apiUrl });
          if (response.ok) {
            const me = await response.json();
            config.logged_in = true;
            config.operator_name = me.name || '';
          }
        } catch (error) {
          config.logged_in = false;
        }
      }
      return {
        running: true,
        logged_in: config.logged_in === true || !config.demo_mode,
        config,
      };
    }
    return {
      running: false,
      logged_in: false,
      config,
      error: config.message,
    };
  } catch (e) {
    const config = buildOfflineRuntimeConfig(apiUrl);
    return { running: false, logged_in: false, config, error: config.message };
  }
}

async function fetchRuntimeConfig(apiUrl) {
  try {
    const response = await fetch(apiUrl + '/api/config', {
      method: 'GET',
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) {
      throw new Error('HTTP ' + response.status);
    }
    const data = await response.json();
    const config = {
      ...data,
      api_url: apiUrl,
      online: true,
      source: 'remote',
      message: '已连接 ERP',
      image_filter: normalizeImageFilter(data.image_filter),
    };
    await chrome.storage.local.set({ [EXTENSION_RUNTIME_CONFIG_KEY]: config });
    return config;
  } catch (error) {
    const offlineConfig = buildOfflineRuntimeConfig(apiUrl);
    await chrome.storage.local.set({ [EXTENSION_RUNTIME_CONFIG_KEY]: offlineConfig });
    return offlineConfig;
  }
}

function buildOfflineRuntimeConfig(apiUrl) {
  let port = 5000;
  try {
    const parsed = new URL(apiUrl);
    port = parsed.port ? Number(parsed.port) : (parsed.protocol === 'https:' ? 443 : 80);
  } catch (e) {
    port = 5000;
  }
  return {
    api_url: apiUrl,
    online: false,
    source: 'defaults',
    service: 'ozon-erp',
    port: port,
    logged_in: false,
    demo_mode: false,
    extension_min_version: '1.3.0',
    extension_recommended_version: '1.3.0',
    image_filter: normalizeImageFilter(),
    message: '无法连接 ERP，已使用本地默认配置',
  };
}

function normalizeImageFilter(imageFilter) {
  return {
    min_width: imageFilter?.min_width ?? OZON_CONFIG.IMAGE_FILTER_DEFAULTS.minWidth,
    min_height: imageFilter?.min_height ?? OZON_CONFIG.IMAGE_FILTER_DEFAULTS.minHeight,
    max_candidates: imageFilter?.max_candidates ?? OZON_CONFIG.IMAGE_FILTER_DEFAULTS.maxCandidates,
  };
}

// 监听标签页更新，在支持的商品页面显示来源标识
if (typeof chrome !== 'undefined' && chrome.tabs?.onUpdated) {
  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.status === 'complete' && tab.url) {
      if (tab.url.includes('1688.com')) {
        chrome.action.setBadgeText({ tabId, text: '1688' });
        chrome.action.setBadgeBackgroundColor({ tabId, color: '#ff6600' });
      } else if (tab.url.includes('taobao.com') || tab.url.includes('tmall.com')) {
        chrome.action.setBadgeText({ tabId, text: '淘宝' });
        chrome.action.setBadgeBackgroundColor({ tabId, color: '#f04438' });
      }
    }
  });
}

if (typeof module !== 'undefined') {
  module.exports = {
    createPendingQueue,
    RETRY_ALARM_NAME,
    COLLECTION_CONTEXT_KEY,
    getApiBaseUrl,
  };
}
