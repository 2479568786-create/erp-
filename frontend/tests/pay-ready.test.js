/**
 * A3：充值可用性判定（js/pay-ready.js）的 Node 单测。
 * 运行：node --test frontend/tests/
 */
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const PayReady = require(path.join(__dirname, '..', 'js', 'pay-ready.js'));

const CH = (ready, label) => ({ ready, enabled: ready, label, missing_count: ready ? 0 : 1 });

test('支付宝主通道就绪 → 可充值，渠道=支付宝', () => {
  const st = {
    primary_channel: 'alipay', active_channel: 'alipay', can_pay: true,
    ready_channels: ['alipay'],
    channels: { wxpay: CH(false, '微信扫码'), alipay: CH(true, '支付宝扫码') },
  };
  const r = PayReady.payReadiness(st);
  assert.equal(r.canPay, true);
  assert.equal(r.channel, 'alipay');
  assert.equal(r.label, '支付宝扫码');
  assert.equal(r.reason, 'ok');
  assert.deepEqual(r.readyChannels, ['alipay']);
});

test('主通道支付宝未就绪、微信就绪 → 自动落到微信（不能只看 enabled）', () => {
  const st = {
    primary_channel: 'alipay', active_channel: 'wxpay', can_pay: true,
    ready_channels: ['wxpay'],
    channels: { wxpay: CH(true, '微信扫码'), alipay: CH(false, '支付宝扫码') },
  };
  const r = PayReady.payReadiness(st);
  assert.equal(r.canPay, true);
  assert.equal(r.channel, 'wxpay');
  assert.equal(r.primary, 'alipay');
  assert.equal(r.reason, 'fallback_from_primary');
});

test('两个渠道都未就绪 → 不可充值', () => {
  const st = {
    primary_channel: 'alipay', active_channel: null, can_pay: false,
    ready_channels: [],
    channels: { wxpay: CH(false, '微信扫码'), alipay: CH(false, '支付宝扫码') },
  };
  const r = PayReady.payReadiness(st);
  assert.equal(r.canPay, false);
  assert.equal(r.channel, null);
  assert.equal(r.reason, 'no_channel_ready');
});

test('后端说能付但 active 渠道未就绪 → 以就绪标志为准（防误判）', () => {
  const st = {
    primary_channel: 'alipay', active_channel: 'alipay', can_pay: true,
    ready_channels: [],
    channels: { wxpay: CH(false, '微信扫码'), alipay: CH(false, '支付宝扫码') },
  };
  const r = PayReady.payReadiness(st);
  assert.equal(r.canPay, false);
  assert.equal(r.channel, null);
});

test('没有 channels 字段（旧后端）→ 用 enabled+ready 兼容判定', () => {
  const r = PayReady.payReadiness({ enabled: true, ready: true, channel: 'wechat_native' });
  assert.equal(r.canPay, true);
  assert.equal(r.channel, 'wxpay');
  assert.equal(r.legacy, true);

  const r2 = PayReady.payReadiness({ enabled: true, ready: false });
  assert.equal(r2.canPay, false);
  assert.equal(r2.legacy, true);
});

test('status 为 null/空 → 不可充值且不抛异常', () => {
  for (const bad of [null, undefined, {}, 0, 'x']) {
    const r = PayReady.payReadiness(bad);
    assert.equal(r.canPay, false);
  }
  assert.equal(PayReady.payReadiness(null).reason, 'no_status');
});

test('active_channel 缺失时按 primary → 就绪渠道顺序回退', () => {
  const st = {
    primary_channel: 'alipay', ready_channels: ['wxpay'],
    channels: { wxpay: CH(true, '微信扫码'), alipay: CH(false, '支付宝扫码') },
  };
  const r = PayReady.payReadiness(st);
  assert.equal(r.channel, 'wxpay');
  assert.equal(r.canPay, true);
});

test('channelShort 把「支付宝扫码」「支付宝（电脑网站支付）」都显示成「支付宝」', () => {
  const st = { channels: { alipay: { label: '支付宝扫码' }, wxpay: { label: '微信扫码' } } };
  const st2 = { channels: { alipay: { label: '支付宝（电脑网站支付）' } } };
  assert.equal(PayReady.channelShort(st2, 'alipay'), '支付宝');
  assert.equal(PayReady.channelShort(st, 'alipay'), '支付宝');
  assert.equal(PayReady.channelShort(st, 'wxpay'), '微信');
  assert.equal(PayReady.channelShort(null, 'alipay'), '支付宝');
  assert.equal(PayReady.channelShort(null, 'unknown'), 'unknown');
});
