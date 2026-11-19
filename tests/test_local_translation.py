import unittest
from unittest.mock import patch

import local_translation as tr
import settings


class Language:
    def __init__(self, code, routes=()):
        self.code, self.routes = code, routes

    def get_translation(self, other):
        return object() if other.code in self.routes else None


class Argos:
    def __init__(self, languages):
        self.languages = languages
        self.calls = []

    def get_installed_languages(self):
        return self.languages

    def translate(self, text, source, target):
        self.calls.append((text, source, target))
        return f'{target}: {text}'


class LocalTranslationTests(unittest.TestCase):
    def test_legacy_language_names_normalize_to_codes(self):
        self.assertEqual(tr.language_code('Chinese (Simplified)'), 'zh')
        self.assertEqual(tr.language_code('Japanese'), 'ja')
        self.assertEqual(settings._clean('translate_language', 'Chinese (Simplified)'), 'zh')
        with self.assertRaises(tr.TranslationError): tr.language_code('Klingon')

    def test_subject_and_body_use_installed_offline_route(self):
        engine = Argos([Language('es', ('en',)), Language('en')])
        with patch.object(tr, '_argos', return_value=(None, engine)), patch.object(tr, '_source', return_value='es'):
            result = tr.translate_message({'subject':'Hola', 'body_text':'Buenos días'}, 'en')
        self.assertEqual(result['subject'], 'en: Hola')
        self.assertEqual(result['body'], 'en: Buenos días')
        self.assertEqual(result['source_language'], 'Spanish')
        self.assertEqual(len(engine.calls), 2)

    def test_missing_direction_is_explicit_and_same_language_needs_no_route(self):
        engine = Argos([Language('es'), Language('en')])
        with patch.object(tr, '_argos', return_value=(None, engine)), patch.object(tr, '_source', return_value='es'):
            with self.assertRaisesRegex(tr.TranslationError, 'No offline translation pack'):
                tr.translate_message({'subject':'Hola'}, 'en')
            same = tr.translate_message({'subject':'Hola'}, 'es')
        self.assertTrue(same['already_in_language'])
        self.assertEqual(engine.calls, [])


if __name__ == '__main__': unittest.main()
