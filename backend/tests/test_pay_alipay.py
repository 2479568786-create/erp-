# -*- coding: utf-8 -*-
"""A1：支付宝 RSA2 签名 / 异步通知验签 / 金额换算单测。

固定向量来源：本地一次性 RSA-2048 测试密钥对生成签名后，**用 OpenSSL 3.0.12 独立复核**：
  原始 320 字节待签串 → `Verified OK`；把 `app_id=2021…` 改成 `2022…` → `Verification failure`。
所以本文件里的期望签名不是「自己验自己」，而是外部工具确认过的真值。

密钥是一次性测试密钥（明文泄露无害，**绝不可用于生产**），以 base64 存放以避免密钥扫描误报。
"""
import base64
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from cryptography.hazmat.primitives import serialization  # noqa: E402

from services import alipay_service  # noqa: E402

# ---- 一次性测试密钥（OpenSSL 复核过向量）----
TEST_PRIVATE_PKCS1_B64 = "LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQpNSUlFb3dJQkFBS0NBUUVBcmVUSTBEaGNzYUQrakE3ZlIyb3F1dVVtVm0vcWpLSDZRTU5RNkk5dTZGZmc0S0dqCjlmaFdtNFcxMFBmYVJaRGtsbVM5VEJxQjNBR0R5R3d0blRVZUJ4dDBtY1Z6a3JBVFpBaUUvNFNxNDAvQkcxL2MKL2pWWDl6MFZYcjRWVEMxbjE5VlA3NkxLMnNnZnZlTlRDLzRUNzJtdGpGaGN5RXp2cUUxSG5rOWVBVCs2RXdJRApSQ2lpa2ZCVXpLVnp5Vnd1VG5TR1dROWROZ0NLeWhITXJMb0twK01UUDl4cDlmd3JjbVFmWVhSRlJJYmNRR1lECkJZM1lnYlRHQWc2UTVlZlU1anZlQmNDaDlCTVJ1TGYvR0N5b291U01qbVZKaXMyNkpDcmlIWGdpRWFHZk1Za28KdmhFcTl2QVBHMXNiT2Z3c09meDVMZTVXenhkcG04ck9ZMHdsNndJREFRQUJBb0lCQUFGTDBjVmZqaTFTeG1MegozMjB3V09Zd3ZCTHIzYU16dCtKUXNtTXhZb0cvMmZEQ3M2NnJMYllXRG85aXNaRlFMYXdEVHpmb2dLeExwSmhUCldobXdZaXRxWkhtYTg2d1Z2T2REZXY1T2xsbUFhQytVak5pbVBDUWZJL0FqV1pSS1NGcGZBQ3dJbWh0OFgyUXkKZEJXRVROQzlpR29oNUhzbnU2ZVUvK0lwdEVrdEZjZ3hzRGtOdWJLZE0rRXhVM1IvNG8vUnJ3NWNGUitUalFLSAprMDRmMlludVovQVFPY0pIeDRqRXNmbzlqMXpIaDdZdVd3S0F0Mk1WYVQvdFZsYWhGdWZSQWN6NWoxT21XMTRyCkVPK1ZMckhtOVczMXJiYTBoRlo2YXF6QnJJeDR0R0oraUZwVTRkU2w0ZWl4elVaaGpYc2VGOUk0a1pSNXBlYUkKTGZhSFczVUNnWUVBMWE1MG4wdGNqWU1WNGhrNlFibkNvOTlyTmI5OFJzK3hqWExwd1haQW9BcTQ2djVTa0JKaQpmcC9zYVEzWURGZG9xRzZ0MU1FNmZPUGlZejFBNjN6NEFjU0Mwa3hSZDBkdjZiSnpiTUd5VkVwTDhGYStMUjRkCndOeWgrc1lOamNxZ2RBUWlzVGEvSHRxaEFLazdibFYrc2JqSmVGcVFUdEZSTG82bGZFT1RwRWNDZ1lFQTBGVWIKdHVoUkQyQ29aMzBTU29WVlliWnc0WHBTSFNFSzBVY2d5eXh2b3Mxb0VHMTVMSyt5N1VGeVY4VEpQSzNEaTcvZAowQUwyWldxU0E3emxuUXJvTlMzU1pxZzJyRVNFT2xwcHVaQlM0VVdtVXVTSW9BWGFJelRNS2hGRnZFcUtTSzZmCmFDUGpwaXFiek9ZWFRuZVQwcmZzak5oblRuUVhReFJ3eVgrM2R6MENnWUJFeVVFZm1sbno4QlBXVWZxdjk4V1IKalRWcyt2UmRzelJiN1hjVVlTWFNuMEVCM05ibExCWHEzejV5WHhPQ2ZZemlVNmEveTJneGxnWmxuZTFOSVZDUQorUnFINml0N2MxS3liMUM2M091ME9LcVRkc2tIcXg4MjI0K0ZNOFpmTTdCTlNKdUdZckpKazJXcTJjK1k2QXE5CjJWZDUxK1dHaWRsUlhIZ25OWFhNdHdLQmdRQ1BvdTVOeUpVQzFqQ2wwWHpIOW0vc3c3M0dKL0R2ZG5XZjNydUUKZVdtcXhKWklKVUpUaGZ3R1NvWk1iemdUNXZZWXB6dUdqRlJYamdlSUhqZnRjN1N2M1p6Zi9JQmRlNHZ6TTUwTApQSzkzOCs1OTNtVTRkM0J6cjVaSU5zcHV6TEhVOU5paEU0V0Vib0ltM2trWlo2WjZ0U2FaNFNZdzJDZWxyelVvCktDOVZQUUtCZ0FEa3Q1WkdNZm1zZEYwVXg4dVVJNFhFMFNkZmxwMFRJNUFaeEtMb0k5Q01IaVN5L1dOcUE3Z3MKVms4QVVRc21WTU85clNkVGhTbm5tTkd0WThFb2dyNWhHT2oxWU5IYVk1aFpRQml1ZnNSOHZPVHY1OGNFUE45awp0d0dWK25Qb0E4Y3ZYNWtUT04vSDVjZ29PMldGdmJTZEN5TmVFanNhTndnWEdiT0o2WExmCi0tLS0tRU5EIFJTQSBQUklWQVRFIEtFWS0tLS0tCg=="
TEST_PRIVATE_PKCS8_B64 = "LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCk1JSUV2UUlCQURBTkJna3Foa2lHOXcwQkFRRUZBQVNDQktjd2dnU2pBZ0VBQW9JQkFRQ3Q1TWpRT0Z5eG9QNk0KRHQ5SGFpcTY1U1pXYitxTW9mcEF3MURvajI3b1YrRGdvYVAxK0ZhYmhiWFE5OXBGa09TV1pMMU1Hb0hjQVlQSQpiQzJkTlI0SEczU1p4WE9Tc0JOa0NJVC9oS3JqVDhFYlg5eitOVmYzUFJWZXZoVk1MV2ZYMVUvdm9zcmF5Qis5CjQxTUwvaFB2YWEyTVdGeklUTytvVFVlZVQxNEJQN29UQWdORUtLS1I4RlRNcFhQSlhDNU9kSVpaRDEwMkFJcksKRWN5c3VncW40eE0vM0duMS9DdHlaQjloZEVWRWh0eEFaZ01GamRpQnRNWUNEcERsNTlUbU85NEZ3S0gwRXhHNAp0LzhZTEtpaTVJeU9aVW1LemJva0t1SWRlQ0lSb1o4eGlTaStFU3IyOEE4Yld4czUvQ3c1L0hrdDdsYlBGMm1iCnlzNWpUQ1hyQWdNQkFBRUNnZ0VBQVV2UnhWK09MVkxHWXZQZmJUQlk1akM4RXV2ZG96TzM0bEN5WXpGaWdiL1oKOE1LenJxc3R0aFlPajJLeGtWQXRyQU5QTitpQXJFdWttRk5hR2JCaUsycGtlWnJ6ckJXODUwTjYvazZXV1lCbwpMNVNNMktZOEpCOGo4Q05abEVwSVdsOEFMQWlhRzN4ZlpESjBGWVJNMEwySWFpSGtleWU3cDVULzRpbTBTUzBWCnlER3dPUTI1c3AwejRURlRkSC9pajlHdkRsd1ZINU9OQW9lVFRoL1ppZTVuOEJBNXdrZkhpTVN4K2oyUFhNZUgKdGk1YkFvQzNZeFZwUCsxV1ZxRVc1OUVCelBtUFU2WmJYaXNRNzVVdXNlYjFiZld0dHJTRVZucHFyTUdzakhpMApZbjZJV2xUaDFLWGg2TEhOUm1HTmV4NFgwamlSbEhtbDVvZ3Q5b2RiZFFLQmdRRFZyblNmUzF5Tmd4WGlHVHBCCnVjS2ozMnMxdjN4R3o3R05jdW5CZGtDZ0NyanEvbEtRRW1KK24reHBEZGdNVjJpb2JxM1V3VHA4NCtKalBVRHIKZlBnQnhJTFNURkYzUjIvcHNuTnN3YkpVU2t2d1ZyNHRIaDNBM0tINnhnMk55cUIwQkNLeE5yOGUycUVBcVR0dQpWWDZ4dU1sNFdwQk8wVkV1anFWOFE1T2tSd0tCZ1FEUVZSdTI2RkVQWUtobmZSSktoVlZodG5EaGVsSWRJUXJSClJ5RExMRytpeldnUWJYa3NyN0x0UVhKWHhNazhyY09MdjkzUUF2WmxhcElEdk9XZEN1ZzFMZEptcURhc1JJUTYKV21tNWtGTGhSYVpTNUlpZ0Jkb2pOTXdxRVVXOFNvcElycDlvSStPbUtwdk01aGRPZDVQU3QreU0yR2RPZEJkRApGSERKZjdkM1BRS0JnRVRKUVIrYVdmUHdFOVpSK3EvM3haR05OV3o2OUYyek5GdnRkeFJoSmRLZlFRSGMxdVVzCkZlcmZQbkpmRTRKOWpPSlRwci9MYURHV0JtV2Q3VTBoVUpENUdvZnFLM3R6VXJKdlVMcmM2N1E0cXBOMnlRZXIKSHpiYmo0VXp4bDh6c0UxSW00Wmlza21UWmFyWno1am9DcjNaVjNuWDVZYUoyVkZjZUNjMWRjeTNBb0dCQUkraQo3azNJbFFMV01LWFJmTWYyYit6RHZjWW44TzkyZFovZXU0UjVhYXJFbGtnbFFsT0YvQVpLaGt4dk9CUG05aGluCk80YU1WRmVPQjRnZU4rMXp0Sy9kbk4vOGdGMTdpL016blFzOHIzZno3bjNlWlRoM2NIT3Zsa2cyeW03TXNkVDAKMktFVGhZUnVnaWJlU1JsbnBucTFKcG5oSmpEWUo2V3ZOU2dvTDFVOUFvR0FBT1MzbGtZeCtheDBYUlRIeTVRagpoY1RSSjErV25STWprQm5Fb3VnajBJd2VKTEw5WTJvRHVDeFdUd0JSQ3laVXc3MnRKMU9GS2VlWTBhMWp3U2lDCnZtRVk2UFZnMGRwam1GbEFHSzUreEh5ODVPL254d1E4MzJTM0FaWDZjK2dEeHk5Zm1STTQzOGZseUNnN1pZVzkKdEowTEkxNFNPeG8zQ0JjWnM0bnBjdDg9Ci0tLS0tRU5EIFBSSVZBVEUgS0VZLS0tLS0K"
TEST_PUBLIC_B64 = "LS0tLS1CRUdJTiBQVUJMSUMgS0VZLS0tLS0KTUlJQklqQU5CZ2txaGtpRzl3MEJBUUVGQUFPQ0FROEFNSUlCQ2dLQ0FRRUFyZVRJMERoY3NhRCtqQTdmUjJvcQp1dVVtVm0vcWpLSDZRTU5RNkk5dTZGZmc0S0dqOWZoV200VzEwUGZhUlpEa2xtUzlUQnFCM0FHRHlHd3RuVFVlCkJ4dDBtY1Z6a3JBVFpBaUUvNFNxNDAvQkcxL2MvalZYOXowVlhyNFZUQzFuMTlWUDc2TEsyc2dmdmVOVEMvNFQKNzJtdGpGaGN5RXp2cUUxSG5rOWVBVCs2RXdJRFJDaWlrZkJVektWenlWd3VUblNHV1E5ZE5nQ0t5aEhNckxvSwpwK01UUDl4cDlmd3JjbVFmWVhSRlJJYmNRR1lEQlkzWWdiVEdBZzZRNWVmVTVqdmVCY0NoOUJNUnVMZi9HQ3lvCm91U01qbVZKaXMyNkpDcmlIWGdpRWFHZk1Za292aEVxOXZBUEcxc2JPZndzT2Z4NUxlNVd6eGRwbThyT1kwd2wKNndJREFRQUIKLS0tLS1FTkQgUFVCTElDIEtFWS0tLS0tCg=="

VECTOR_PARAMS = {'app_id': '2021000000000001', 'method': 'alipay.trade.precreate', 'format': 'JSON', 'charset': 'utf-8', 'sign_type': 'RSA2', 'timestamp': '2026-11-06 12:00:00', 'version': '1.0', 'notify_url': 'https://example.test/api/pay/alipay/notify', 'biz_content': '{"out_trade_no":"RC20261106120000000001","total_amount":"10.00","subject":"Geekfast ERP recharge 10 points","timeout_express":"15m"}'}
VECTOR_SIGN_STRING = "app_id=2021000000000001&biz_content={\"out_trade_no\":\"RC20261106120000000001\",\"total_amount\":\"10.00\",\"subject\":\"Geekfast ERP recharge 10 points\",\"timeout_express\":\"15m\"}&charset=utf-8&format=JSON&method=alipay.trade.precreate&notify_url=https://example.test/api/pay/alipay/notify&timestamp=2026-11-06 12:00:00&version=1.0"
VECTOR_SIGN_B64 = "SPhPa2xlYseNydJOcBYB0Q1BuZ2NFL6lVgi8hlPpSbrnB2Gw/kw6Ke359YUEbc5AKo4+GQeZwNXBt2uFmMJ5XKFTCouGiN9oI5L4DZ7LHepLfjXQYuqvvnnmwmbpH0tNVtQ+k7+tsIS0vtTNUhEeucKeliqm68/3pWxw060Oz7SskMYsDK3CUbv30epHNwEycFqIaUB5nQ3QiUCpirqvs00GWRpdcSLdj8+3yJabkRMUV1hKMmAtAn9f4GCOk855Kur+UAs1V6ELD54/6mBYIoiVOaSlFEKH77SRCGs6RrrTktM+Lcjd3v2jNJh1fofRHibyQwj8K4W+K+0jxXnfvA=="


def _priv(pkcs8=False):
    raw = base64.b64decode(TEST_PRIVATE_PKCS8_B64 if pkcs8 else TEST_PRIVATE_PKCS1_B64)
    return serialization.load_pem_private_key(raw, password=None)


def _pub():
    return serialization.load_pem_public_key(base64.b64decode(TEST_PUBLIC_B64))


class SignVectorTests(unittest.TestCase):
    def test_sign_string_matches_openssl_verified_vector(self):
        self.assertEqual(alipay_service.sign_string(VECTOR_PARAMS), VECTOR_SIGN_STRING)
        self.assertEqual(len(VECTOR_SIGN_STRING.encode('utf-8')), 320)

    def test_sign_matches_fixed_vector(self):
        self.assertEqual(alipay_service.sign(VECTOR_PARAMS, private_key=_priv()), VECTOR_SIGN_B64)

    def test_sign_pkcs1_and_pkcs8_keys_are_equivalent(self):
        self.assertEqual(alipay_service.sign(VECTOR_PARAMS, private_key=_priv(True)),
                         alipay_service.sign(VECTOR_PARAMS, private_key=_priv()))

    def test_sign_string_excludes_sign_fields_and_empty_values(self):
        params = {'c': '3', 'a': '1', 'b': '', 'sign': 'x', 'sign_type': 'RSA2', 'd': None}
        self.assertEqual(alipay_service.sign_string(params), 'a=1&c=3')

    def test_sign_string_keeps_biz_content_verbatim(self):
        biz = '{"out_trade_no":"RC1","total_amount":"10.00"}'
        text = alipay_service.sign_string({'biz_content': biz, 'app_id': '1'})
        self.assertEqual(text, 'app_id=1&biz_content=' + biz)
        self.assertNotIn('%7B', text)      # 不做二次 URL 编码

    def test_sign_string_utf8_subject(self):
        text = alipay_service.sign_string({'subject': '图片点数充值', 'app_id': '1'})
        self.assertEqual(text, 'app_id=1&subject=图片点数充值')
        self.assertEqual(len(text.encode('utf-8')), len('app_id=1&subject=图片点数充值'.encode('utf-8')))


class VerifyNotifyTests(unittest.TestCase):
    def _form(self, **over):
        form = dict(VECTOR_PARAMS)
        form['sign'] = VECTOR_SIGN_B64
        form.update(over)
        return form

    def test_accepts_openSSL_verified_vector(self):
        ok, reason = alipay_service.verify_notify(self._form(), public_key=_pub())
        self.assertTrue(ok, reason)
        self.assertEqual(reason, 'ok')

    def test_accepts_via_public_key_path_from_env(self):
        tmp = tempfile.mkdtemp(prefix='alipay_pub_')
        path = os.path.join(tmp, 'alipay_public_key.pem')
        with open(path, 'wb') as f:
            f.write(base64.b64decode(TEST_PUBLIC_B64))
        with patch.dict(os.environ, {'ALIPAY_PUBLIC_KEY_PATH': path}):
            ok, reason = alipay_service.verify_notify(self._form())
        self.assertTrue(ok, reason)

    def test_rejects_tampered_amount(self):
        form = self._form()
        form['biz_content'] = form['biz_content'].replace('"10.00"', '"0.01"')
        ok, reason = alipay_service.verify_notify(form, public_key=_pub())
        self.assertFalse(ok)
        self.assertEqual(reason, 'bad_signature:InvalidSignature')

    def test_rejects_added_param(self):
        ok, reason = alipay_service.verify_notify(self._form(trade_status='TRADE_SUCCESS'),
                                                  public_key=_pub())
        self.assertFalse(ok)
        self.assertTrue(reason.startswith('bad_signature'), reason)

    def test_rejects_missing_sign(self):
        form = dict(VECTOR_PARAMS)
        ok, reason = alipay_service.verify_notify(form, public_key=_pub())
        self.assertFalse(ok)
        self.assertEqual(reason, 'missing_sign')

    def test_rejects_non_rsa2_sign_type(self):
        ok, reason = alipay_service.verify_notify(self._form(sign_type='RSA'), public_key=_pub())
        self.assertFalse(ok)
        self.assertEqual(reason, 'bad_sign_type:RSA')

    def test_rejects_bad_base64(self):
        ok, reason = alipay_service.verify_notify(self._form(sign='###not-base64###'),
                                                  public_key=_pub())
        self.assertFalse(ok)
        self.assertEqual(reason, 'bad_sign_encoding')

    def test_rejects_empty_params(self):
        self.assertEqual(alipay_service.verify_notify({}, public_key=_pub()),
                         (False, 'empty_params'))

    def test_ignores_empty_optional_fields(self):
        # 支付宝偶尔会带空值字段，空值不参与签名，不应导致验签失败
        ok, reason = alipay_service.verify_notify(self._form(refund_fee='', body=''),
                                                  public_key=_pub())
        self.assertTrue(ok, reason)


class AmountTests(unittest.TestCase):
    def test_yuan_to_cents(self):
        cases = {'10.00': 1000, '0.01': 1, '10': 1000, '1.5': 150, '200.00': 20000}
        for text, cents in cases.items():
            self.assertEqual(alipay_service.amount_to_cents(text), cents, text)

    def test_rejects_three_decimals(self):
        for bad in ('10.001', '0.001'):
            with self.assertRaises(ValueError):
                alipay_service.amount_to_cents(bad)

    def test_rejects_zero_negative_and_garbage(self):
        for bad in ('0', '0.00', '-1', 'abc', '', None, '1e3'):
            with self.assertRaises(ValueError):
                alipay_service.amount_to_cents(bad)

    def test_cents_to_yuan(self):
        self.assertEqual(alipay_service.cents_to_yuan(1000), '10.00')
        self.assertEqual(alipay_service.cents_to_yuan(1), '0.01')
        self.assertEqual(alipay_service.cents_to_yuan(12345), '123.45')


class ConfigTests(unittest.TestCase):
    def test_enabled_flag_and_defaults(self):
        with patch.dict(os.environ, {'ALIPAY_ENABLED': '1', 'ALIPAY_APPID': '2021000000000001'}, clear=False):
            cfg = alipay_service.config()
            self.assertTrue(cfg['enabled'])
            self.assertEqual(cfg['app_id'], '2021000000000001')
            self.assertFalse(cfg['sandbox'])

    def test_sandbox_gateway_switch(self):
        with patch.dict(os.environ, {'ALIPAY_GATEWAY': alipay_service.SANDBOX_GATEWAY}, clear=False):
            cfg = alipay_service.config()
            self.assertTrue(cfg['sandbox'])
            self.assertIn('alipaydev', cfg['gateway'])

    def test_missing_config_lists_names_only(self):
        env = {'ALIPAY_APPID': '2021000000000001',
               'ALIPAY_NOTIFY_URL': 'https://example.test/api/pay/alipay/notify',
               'ALIPAY_PRIVATE_KEY_PATH': '/nonexistent/app_private_key.pem',
               'ALIPAY_PUBLIC_KEY_PATH': '/nonexistent/alipay_public_key.pem'}
        with patch.dict(os.environ, env, clear=True):
            missing = alipay_service.missing_config()
        self.assertIn('ALIPAY_PRIVATE_KEY_FILE', missing)
        self.assertIn('ALIPAY_PUBLIC_KEY_FILE', missing)
        self.assertNotIn('ALIPAY_APPID', missing)
        self.assertNotIn('2021000000000001', ' '.join(missing))

    def test_common_params_shape(self):
        with patch.dict(os.environ, {'ALIPAY_APPID': '2021000000000001',
                                     'ALIPAY_NOTIFY_URL': 'https://example.test/n'}, clear=False):
            params = alipay_service.build_common_params(
                'alipay.trade.precreate',
                {'out_trade_no': 'RC1', 'total_amount': '10.00', 'subject': '测试'},
                timestamp='2026-11-06 12:00:00')
        self.assertEqual(params['sign_type'], 'RSA2')
        self.assertEqual(params['charset'], 'utf-8')
        self.assertEqual(params['notify_url'], 'https://example.test/n')
        self.assertIn('"out_trade_no":"RC1"', params['biz_content'])
        self.assertNotIn('sign', params)


if __name__ == '__main__':
    unittest.main()
