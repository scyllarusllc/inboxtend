import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import website_jobs
from tasks import connection, create_task


class WebsiteJobTests(unittest.TestCase):
    def test_start_validates_slug_and_only_prepares_local_build(self):
        with tempfile.TemporaryDirectory() as root:
            db_path = str(Path(root) / 'db.sqlite3')
            with connection(db_path) as db:
                task = create_task(db, {'id': 'mail-1', 'provider': 'gmail'},
                                   {'title': 'Website', 'description': '', 'priority': 'medium'}, user_id=1)
            with patch.dict(os.environ, {'INBOX_TEND_TASKS_DIR': str(Path(root) / 'tasks'),
                                      'INBOX_TEND_WEBSITE_ROOT': str(Path(root) / 'sites')}), \
                 patch('website_jobs.threading.Thread') as thread:
                with self.assertRaises(ValueError):
                    website_jobs.start(db_path, 1, task['id'], '../escape')
                job = website_jobs.start(db_path, 1, task['id'], 'site-test')
                self.assertEqual(job['state'], 'running')
                self.assertEqual(job['path'], str(Path(root) / 'sites' / 'site-test'))
                thread.return_value.start.assert_called_once()
                self.assertFalse((Path(root) / 'sites' / 'site-test' / '.git').exists())
                website_jobs._RUNNING.discard(task['id'])

    def test_publish_uses_private_repository_after_ready_state(self):
        with tempfile.TemporaryDirectory() as root:
            task_id = 83
            tasks = Path(root) / 'tasks'
            sites = Path(root) / 'sites'
            (tasks / str(task_id)).mkdir(parents=True)
            site = sites / 'site-test'
            (site / '.git').mkdir(parents=True)
            with patch.dict(os.environ, {'INBOX_TEND_TASKS_DIR': str(tasks), 'INBOX_TEND_WEBSITE_ROOT': str(sites)}), \
                 patch('website_jobs.shutil.which', return_value='/usr/bin/gh'), \
                 patch('website_jobs.subprocess.run', side_effect=[
                     subprocess.CompletedProcess([], 0, b'README.md\0app.py\0', b''),
                     subprocess.CompletedProcess([], 0, 'https://github.com/example/site-test\n', ''),
                     subprocess.CompletedProcess([], 0, 'https://github.com/example/site-test\n', '')]) as run:
                website_jobs._save(task_id, state='ready', slug='site-test', path=str(site))
                result = website_jobs.publish(task_id)
                self.assertEqual(result['state'], 'published')
                self.assertEqual(result['remote_url'], 'https://github.com/example/site-test')
                command = run.call_args_list[1].args[0]
                self.assertEqual(command[:4], ['gh', 'repo', 'create', 'site-test'])
                self.assertIn('--private', command)
                self.assertIn('--push', command)


if __name__ == '__main__':
    unittest.main()
