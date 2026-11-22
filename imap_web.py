"""IMAP import and read/unread flag updates for the local mail cache."""
import email
import imaplib
import json
import re
import ssl
import time
from email import policy
from email.utils import parsedate_to_datetime
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os
import gmail_web
from tasks import peer_for

class IMAPError(RuntimeError): pass

def account(db, user_id, account_id):
    row = db.execute('SELECT * FROM imap_connections WHERE id=? AND user_id=?', (account_id, user_id)).fetchone()
    if not row: raise IMAPError('Unknown IMAP account')
    return row

def add(db, config, user_id, address, host, username, password):
    if not isinstance(address, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', address) or len(address)>254: raise ValueError('Invalid email address')
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', host) or '..' in host: raise ValueError('Invalid IMAP host')
    if not isinstance(username, str) or not username or len(username)>254: raise ValueError('Invalid IMAP username')
    if not isinstance(password, str) or not password or len(password)>256: raise ValueError('Invalid IMAP password')
    try:
        nonce = os.urandom(12)
        cipher = nonce + AESGCM(gmail_web._key(config)).encrypt(nonce,password.encode(),str(user_id).encode())
    except gmail_web.GmailConnectionError: raise IMAPError('Set INBOX_TEND_TOKEN_KEY before connecting IMAP') from None
    with imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=15) as client:
        client.login(username, password)
    with db:
        address = address.lower()
        existing = db.execute('SELECT id FROM imap_connections WHERE user_id=? AND email=?',(user_id,address)).fetchone()
        if existing:
            account_id = existing['id']
            db.execute('UPDATE imap_connections SET host=?,username=?,password=?,connected_at=? WHERE id=? AND user_id=?',
                       (host.lower(),username,cipher,int(time.time()),account_id,user_id))
        else:
            next_id = db.execute('SELECT MIN(id) FROM imap_connections').fetchone()[0]
            account_id = min(0,next_id or 0)-1
            db.execute('INSERT INTO imap_connections(id,user_id,email,host,username,password,connected_at) VALUES (?,?,?,?,?,?,?)',
                       (account_id,user_id,address,host.lower(),username,cipher,int(time.time())))
    return account_id

def remove(db, user_id, account_id):
    account(db,user_id,account_id)
    ids = 'SELECT message_id FROM gmail_messages WHERE user_id=? AND account_id=?'
    for table in ('message_topics','message_websites','message_translations','gmail_attachments'):
        db.execute(f'DELETE FROM {table} WHERE user_id=? AND message_id IN ({ids})',(user_id,user_id,account_id))
    db.execute('DELETE FROM gmail_messages WHERE user_id=? AND account_id=?',(user_id,account_id))
    db.execute('DELETE FROM imap_connections WHERE user_id=? AND id=?',(user_id,account_id))

def _text(part):
    try: return part.get_content()
    except (UnicodeError, LookupError, ValueError): return part.get_payload(decode=True).decode('utf-8','replace')

def set_seen(db, config, user_id, account_id, message_ids, seen):
    """Update only \\Seen, grouped by folder. Reject stale UIDs after a folder reset."""
    row = account(db,user_id,account_id)
    try:
        cipher = bytes(row['password'])
        password = AESGCM(gmail_web._key(config)).decrypt(cipher[:12],cipher[12:],str(user_id).encode()).decode()
    except Exception: raise IMAPError('Cannot decrypt IMAP password') from None
    groups = {}
    for mid in message_ids:
        match = re.fullmatch(r'imap-(-\d+)~(\d+)~(inbox|sent)~(\d+)',mid)
        if not match or int(match[1]) != account_id:
            raise IMAPError('Invalid IMAP message ID')
        groups.setdefault((match[3],match[2]),[]).append(match[4])
    if not groups: return
    try:
        with imaplib.IMAP4_SSL(row['host'],993,ssl_context=ssl.create_default_context(),timeout=30) as client:
            client.login(row['username'],password)
            for (folder,validity),uids in groups.items():
                name = 'INBOX'
                if folder == 'sent':
                    typ,boxes = client.list()
                    if typ != 'OK': raise IMAPError('Cannot list IMAP folders')
                    name = next((line.decode('utf-8','replace').rsplit(' ',1)[-1].strip('"') for line in boxes or [] if b'\\Sent' in line),None)
                    if not name: raise IMAPError('Sent folder is unavailable')
                typ,_ = client.select(name,readonly=False)
                if typ != 'OK': raise IMAPError('Cannot open IMAP folder for flag update')
                current = (client.response('UIDVALIDITY')[1] or [b''])[0].decode()
                if current != validity: raise IMAPError('IMAP folder changed; refresh mail before marking it read')
                for start in range(0,len(uids),100):
                    uid_set = ','.join(uids[start:start+100])
                    typ,_ = client.uid('store',uid_set,'+FLAGS.SILENT' if seen else '-FLAGS.SILENT','(\\Seen)')
                    if typ != 'OK': raise IMAPError('IMAP server rejected the read/unread change')
    except (imaplib.IMAP4.error,OSError) as exc:
        raise IMAPError('IMAP read/unread update failed') from exc

def sync(db, config, user_id, account_id, report=lambda **kw: None):
    row = account(db,user_id,account_id)
    try:
        cipher = bytes(row['password'])
        password = AESGCM(gmail_web._key(config)).decrypt(cipher[:12],cipher[12:],str(user_id).encode()).decode()
    except Exception: raise IMAPError('Cannot decrypt IMAP password') from None
    import settings
    prefs = settings.effective(db,user_id,config)
    limit = int(prefs['sync_max_messages'])
    attachment_max = int(prefs['attachment_max_mb'])*1024*1024
    with imaplib.IMAP4_SSL(row['host'],993,ssl_context=ssl.create_default_context(),timeout=30) as client:
        try: client.login(row['username'],password)
        except imaplib.IMAP4.error: raise IMAPError('IMAP login failed') from None
        typ, boxes = client.list()
        if typ != 'OK': raise IMAPError('Cannot list IMAP folders')
        sent = None
        for raw in boxes or []:
            line = raw.decode('utf-8','replace')
            if '\\Sent' in line:
                sent = line.rsplit(' ',1)[-1].strip('"'); break
        folders = [('INBOX','inbox')]
        if sent and prefs['sync_sent']: folders.append((sent,'sent'))
        total=done=0
        for folder,kind in folders:
            typ,_ = client.select(folder,readonly=True)
            if typ!='OK':
                if kind=='sent': continue
                raise IMAPError('Cannot open inbox')
            validity = (client.response('UIDVALIDITY')[1] or [b'0'])[0].decode()
            typ,data = client.uid('search',None,'ALL')
            if typ!='OK': raise IMAPError('Cannot search IMAP folder')
            uids = (data[0] or b'').split()[-limit:]
            total += len(uids); report(total=total,done=done)
            prefix = f'imap-{account_id}~{validity}~{kind}~'
            for uid in uids:
                mid = prefix+uid.decode()
                saved = db.execute('SELECT has_full FROM gmail_messages WHERE user_id=? AND message_id=?',(user_id,mid)).fetchone()
                typ, records = client.uid('fetch',uid,'(FLAGS)' if saved and saved['has_full'] else '(BODY.PEEK[] FLAGS)')
                if typ!='OK': continue
                metadata = b' '.join(part[0] if isinstance(part,tuple) else part for part in records if isinstance(part,(tuple,bytes)))
                flags_match = re.search(rb'FLAGS\s*\(([^)]*)\)',metadata,re.IGNORECASE)
                if not flags_match: continue
                flags = flags_match.group(1).decode('ascii','replace')
                labels = ['SENT' if kind=='sent' else 'INBOX']
                if '\\Seen' not in flags: labels.append('UNREAD')
                if saved and saved['has_full']:
                    db.execute('UPDATE gmail_messages SET label_ids=?,in_inbox=?,in_sent=? WHERE user_id=? AND message_id=?',
                               (json.dumps(labels),int(kind=='inbox'),int(kind=='sent'),user_id,mid))
                    db.commit()
                    done+=1; report(done=done)
                    continue
                pair = next((x for x in records if isinstance(x,tuple)),None)
                if not pair: continue
                raw = pair[1]
                msg = email.message_from_bytes(raw,policy=policy.default)
                body=''; html=''; attachments=[]
                for part in msg.walk():
                    if part.is_multipart(): continue
                    filename = part.get_filename()
                    if filename:
                        content = part.get_payload(decode=True) or b''
                        attachments.append((str(filename)[:255],part.get_content_type(),len(content),content if len(content)<=attachment_max else None))
                    elif part.get_content_type()=='text/plain' and not body: body=str(_text(part))
                    elif part.get_content_type()=='text/html' and not html: html=str(_text(part))
                try: stamp=int(parsedate_to_datetime(str(msg['Date'])).timestamp())*1000
                except (ValueError,TypeError,OverflowError): stamp=int(time.time()*1000)
                headers={k:str(v) for k,v in msg.items()}
                from_addr=headers.get('From',''); to_addr=headers.get('To','')
                peer,peer_name=peer_for(json.dumps(labels),from_addr,to_addr)
                snippet=' '.join(body.split())[:200]
                thread=prefix+'thread~'+(str(msg['Message-ID'] or uid.decode()).strip('<>')[:180])
                db.execute('''INSERT INTO gmail_messages(user_id,message_id,thread_id,from_addr,subject,date_header,snippet,internal_date,in_inbox,fetched_at,to_addr,cc_addr,label_ids,headers_json,body_text,body_html,size_estimate,peer_email,peer_name,has_full,account_id,in_sent)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,message_id) DO UPDATE SET label_ids=excluded.label_ids, in_inbox=excluded.in_inbox,in_sent=excluded.in_sent,body_text=excluded.body_text,body_html=excluded.body_html''',
                  (user_id,mid,thread,from_addr,headers.get('Subject',''),headers.get('Date',''),snippet,stamp,int(kind=='inbox'),int(time.time()),to_addr,headers.get('Cc',''),json.dumps(labels),json.dumps([{'name':k,'value':v} for k,v in headers.items()]),body,html,len(raw),peer,peer_name,1,account_id,int(kind=='sent')))
                turned = gmail_web.attachment_rotations(db,user_id,mid)
                db.execute('DELETE FROM gmail_attachments WHERE user_id=? AND message_id=?',(user_id,mid))
                for n,(filename,mime,size,content) in enumerate(attachments):
                    db.execute('INSERT INTO gmail_attachments(user_id,message_id,part_id,filename,mime_type,size_bytes,content,skipped_reason,rotation) VALUES(?,?,?,?,?,?,?,?,?)',(user_id,mid,str(n),filename,mime,size,content,'' if content is not None else 'larger than the size limit',turned.get(str(n),0)))
                db.commit()  # Release the writer lock before the next network fetch.
                done+=1; report(done=done)
            if kind=='inbox':
                active={prefix+uid.decode() for uid in uids}
                for old in db.execute('SELECT message_id FROM gmail_messages WHERE user_id=? AND account_id=? AND in_inbox=1',(user_id,account_id)).fetchall():
                    if old['message_id'].startswith(prefix) and old['message_id'] not in active:
                        db.execute('UPDATE gmail_messages SET in_inbox=0 WHERE user_id=? AND message_id=?',(user_id,old['message_id']))
            db.commit()
    db.execute('UPDATE imap_connections SET last_sync=? WHERE id=?',(int(time.time()),account_id)); db.commit()
