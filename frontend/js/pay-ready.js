/**
 * 充值可用性判定（纯函数，无 DOM / 无网络依赖，便于用 `node --test` 直接单测）。
 *
 * 口径（重要）：**不能只看 `enabled`**。
 * 现在有多个渠道（微信 / 支付宝），主通道可能被冻结而备选通道可用，
 * 也可能主通道可用而备选未配好。判定顺序：
 *   1) active_channel（后端算好的「真正可用渠道」）
 *   2) primary_channel 就绪则用它
 *   3) 任意一个渠道就绪
 *   4) 都没有 → 不可充值
 * 兼容旧后端响应（只有 enabled/ready 两个字段）。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.PayReady = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const CHANNEL_SHORT = { wxpay: '微信', alipay: '支付宝' };

  function payReadiness(st) {
    if (!st || typeof st !== 'object') {
      return { canPay: false, channel: null, label: '', reason: 'no_status', channels: {}, readyChannels: [], primary: null, legacy: false };
    }
    const channels = (st.channels && typeof st.channels === 'object' && !Array.isArray(st.channels)) ? st.channels : {};
    const names = Object.keys(channels);

    // 旧后端：只有 enabled / ready
    if (!names.length) {
      const legacyReady = !!(st.enabled && st.ready);
      return {
        canPay: legacyReady,
        channel: legacyReady ? 'wxpay' : null,
        label: legacyReady ? '微信扫码' : '',
        reason: legacyReady ? 'legacy_ready' : 'legacy_disabled',
        channels: {},
        readyChannels: legacyReady ? ['wxpay'] : [],
        primary: null,
        legacy: true
      };
    }

    const readyChannels = Array.isArray(st.ready_channels)
      ? st.ready_channels.filter((n) => n in channels)
      : names.filter((n) => channels[n] && channels[n].ready);

    const primary = st.primary_channel && st.primary_channel in channels ? st.primary_channel : null;
    let active = st.active_channel && st.active_channel in channels ? st.active_channel : null;
    if (!active) {
      if (primary && channels[primary] && channels[primary].ready) active = primary;
      else active = readyChannels[0] || null;
    }
    // 后端说 active 但该渠道其实没就绪 → 以就绪标志为准，避免误判成可充值
    if (active && channels[active] && !channels[active].ready) active = readyChannels[0] || null;

    const canPay = typeof st.can_pay === 'boolean' ? (st.can_pay && !!active) : !!active;
    const label = active && channels[active] && channels[active].label ? channels[active].label : '';

    let reason = 'ok';
    if (!canPay) {
      reason = readyChannels.length ? 'backend_says_no' : 'no_channel_ready';
    } else if (primary && active !== primary) {
      reason = 'fallback_from_primary';
    }
    return { canPay, channel: active, label, reason, channels, readyChannels, primary, legacy: false };
  }

  /** 渠道短名（用于订单列表徽标）：后端 label「支付宝（电脑网站支付）」→「支付宝」 */
  function channelShort(st, name) {
    if (st && st.channels && st.channels[name] && st.channels[name].label) {
      return String(st.channels[name].label).replace(/（[^）]*）/g, '').replace(/扫码$/, '');
    }
    return CHANNEL_SHORT[name] || (name || '-');
  }

  return { payReadiness, channelShort };
});
