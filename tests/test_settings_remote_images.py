import tempfile
import unittest

import settings
from tasks import connection


class RemoteImageSettingTests(unittest.TestCase):
    def test_switches_default_on_and_persist_per_user(self):
        with tempfile.TemporaryDirectory() as directory, connection(directory + '/db.sqlite3') as db:
            self.assertTrue(all(settings.effective(db, 1)[key] for key in settings._BOOLS))
            settings.save(db, 1, {'load_remote_images': False})
            self.assertFalse(settings.effective(db, 1)['load_remote_images'])
            self.assertTrue(settings.effective(db, 2)['load_remote_images'])
            with self.assertRaises(ValueError):
                settings.save(db, 1, {'load_remote_images': 'yes'})
