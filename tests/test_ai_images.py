import unittest
from unittest.mock import patch
import ai

PNG = b'\x89PNG\r\n\x1a\n' + b'test-image-bytes'
PREFS = {'ai_provider':'claude','ai_timeout':60,'timezone':'UTC'}
MESSAGE = {'direction':'received','from':'sender@example.org','to':'owner@example.org','subject':'',
           'body_text':'', 'date':'2026-11-21'}

class ImageReviewTests(unittest.TestCase):
    def test_extracts_original_text_for_each_image_without_translation(self):
        with patch.object(ai,'run_ai',return_value={'images':[{'number':2,'text':'原文二'}, {'number':1,'text':'Original one'}]}) as run:
            result=ai.extract_image_text({},[('one.png','image/png',PNG),('two.png','image/png',PNG)],PREFS)
        self.assertEqual(result,[{'number':1,'text':'Original one'},{'number':2,'text':'原文二'}])
        self.assertIn('Do not translate',run.call_args.args[2])
        self.assertEqual(len(run.call_args.kwargs['images']),2)

    def test_image_review_keeps_order_and_bounded_fields(self):
        response = {'images':[{'number':2,'visible_text_zh':'第二张','meaning_zh':'第二张含义'},
                              {'number':1,'visible_text_zh':'第一张','meaning_zh':'第一张含义'},
                              {'number':99,'visible_text_zh':'bad','meaning_zh':'bad'}],
                    'summary_zh':'总结','task_title':'查看图片','task_description':'核对信息','actionable':True}
        with patch.object(ai,'run_ai',return_value=response) as run:
            result=ai.review_images({},MESSAGE,[('one.png','image/png',PNG),('two.png','image/png',PNG)],PREFS)
        self.assertEqual([item['number'] for item in result['images']],[1,2])
        self.assertEqual(result['task_title'],'查看图片')
        self.assertEqual(len(run.call_args.kwargs['images']),2)
        self.assertIn('in Chinese',run.call_args.args[2])

    def test_rejects_nonimage_and_oversize_before_provider_call(self):
        with patch.object(ai,'run_ai') as run:
            with self.assertRaises(ai.AIError):
                ai.review_images({},MESSAGE,[('unsafe.svg','image/png',b'<svg>')],PREFS)
            with self.assertRaises(ai.AIError):
                ai.review_images({},MESSAGE,[('large.png','image/png',PNG+b'0'*ai.MAX_IMAGE_BYTES)],PREFS)
            run.assert_not_called()

if __name__=='__main__': unittest.main()
