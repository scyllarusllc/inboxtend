import unittest
from unittest.mock import patch

from mail_content import readable_body
import local_translation


class ReadableMailTests(unittest.TestCase):
    def test_full_html_email_shows_message_without_head_css_or_tags(self):
        html = '<!doctype html><html><head><style>body { margin: 0 }</style></head>' \
               '<body><p>Hi everyone 💛</p><p>Tuesday moves to December 1.</p></body></html>'
        text = readable_body(html)
        self.assertIn('Hi everyone 💛', text)
        self.assertIn('Tuesday moves to December 1.', text)
        self.assertNotIn('margin: 0', text)
        self.assertNotIn('<p>', text)

    def test_html_alternative_and_plain_text_remain_distinct(self):
        self.assertEqual(readable_body('', '<p>Only HTML</p>'), 'Only HTML')
        self.assertEqual(readable_body('Plain message', '<p>Other version</p>'), 'Plain message')

    def test_translation_receives_visible_text(self):
        class Language:
            def __init__(self, code): self.code = code
            def get_translation(self, other): return True
        class Argos:
            def get_installed_languages(self): return [Language('en'), Language('zh')]
            def translate(self, text, source, target): return '译文：' + text
        markup = '<!doctype html><html><head><style>body { margin: 0 }</style></head><body><p>Meeting moved.</p></body></html>'
        with patch.object(local_translation, '_argos', return_value=(None, Argos())), \
             patch.object(local_translation, '_source', return_value='en'):
            result = local_translation.translate_message({'subject': 'Update', 'body_text': markup}, 'zh')
        self.assertEqual(result['body'], '译文：Meeting moved.')


if __name__ == '__main__':
    unittest.main()
