import unittest

from mail_content import readable_body


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


if __name__ == '__main__':
    unittest.main()
