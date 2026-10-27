import os
import unittest


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class AiSuiteFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(PROJECT_DIR, 'frontend', 'js', 'api.js'), encoding='utf-8') as source:
            cls.api_source = source.read()
        with open(os.path.join(PROJECT_DIR, 'frontend', 'js', 'pages', 'media.js'), encoding='utf-8') as source:
            cls.media_source = source.read()

    def test_gallery_upload_uses_authenticated_request_wrapper(self):
        upload_function = self.api_source.split('async function uploadGallery', 1)[1].split('function updateGalleryAsset', 1)[0]

        self.assertIn("request('/api/gallery/upload'", upload_function)
        self.assertNotIn('fetch(', upload_function)

    def test_suite_prompt_uses_programmatic_text_overlay_consistently(self):
        prompt_builder = self.media_source.split('function buildAISuitePrompt', 1)[1].split('function extractRussianSuiteCopy', 1)[0]

        self.assertIn('供 ERP 后期排入俄语标题和说明', prompt_builder)
        self.assertIn('画面不要出现任何文字', prompt_builder)
        self.assertNotIn('顶部放清晰俄语标题', prompt_builder)

    def test_failed_post_processing_offers_separate_retry_actions(self):
        self.assertIn('retryAISuiteLayout(${index})', self.media_source)
        self.assertIn('retryAISuiteItem(${index})', self.media_source)
        self.assertIn('原始生成图已保留', self.media_source)


if __name__ == '__main__':
    unittest.main()
