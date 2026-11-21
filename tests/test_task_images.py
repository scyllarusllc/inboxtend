import tempfile
import unittest
from tasks import connection, create_task, get_task, task_image, task_images, save_task_image_review, task_image_review, delete_task

class TaskImageTests(unittest.TestCase):
    def test_images_survive_mail_removal_and_are_user_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            db=connection(directory+'/tasks.sqlite3')
            db.execute("INSERT INTO gmail_messages(user_id,message_id,thread_id,fetched_at) VALUES(1,'mail-1','thread-1',0)")
            for name in ('one.jpg','two.jpg'):
                db.execute("INSERT INTO gmail_attachments(user_id,message_id,filename,mime_type,size_bytes,content) VALUES(1,'mail-1',?,'image/jpeg',4,?)",(name,b'\xff\xd8\xff\x00'))
            db.commit()
            task=create_task(db,{'provider':'gmail','id':'mail-1','thread_id':'thread-1'},
                {'title':'Review images','description':'Inspect both images','priority':'medium'},user_id=1)
            self.assertEqual(task['image_count'],2)
            images=task_images(db,task['id'],1)
            self.assertEqual([image['name'] for image in images],['one.jpg','two.jpg'])
            self.assertIsNone(task_images(db,task['id'],2))
            self.assertIsNone(task_image(db,task['id'],images[0]['id'],2))
            db.execute("DELETE FROM gmail_attachments WHERE user_id=1 AND message_id='mail-1'")
            db.execute("DELETE FROM gmail_messages WHERE user_id=1 AND message_id='mail-1'")
            db.commit()
            self.assertEqual(bytes(task_image(db,task['id'],images[0]['id'],1)['content']),b'\xff\xd8\xff\x00')
            result={'images':[{'number':1,'name':'one.jpg','visible_text_zh':'你好','meaning_zh':'问候'}],
                    'summary_zh':'摘要','task_title':'翻译图片','task_description':'核对图片','actionable':False}
            save_task_image_review(db,task['id'],1,result)
            self.assertEqual(task_image_review(db,task['id'],1)['summary_zh'],'摘要')
            delete_task(db,task['id'],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_attachments').fetchone()[0],0)
            db.close()

if __name__=='__main__': unittest.main()
