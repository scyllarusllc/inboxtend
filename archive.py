"""Archived senders: every received message from an address on the list is marked read and filed under Archived.

Local only. Inbox Tend never moves or deletes mail on the server; read state follows the user's existing
"also change Gmail" setting, like every other read/unread change. Removing a sender puts its mail back in the Inbox."""
from __future__ import annotations

import time

import gmail_web
import settings as prefs


def _address(value):
    value = (value or '').strip().lower()
    return value if 3 <= len(value) <= 254 and '@' in value and ' ' not in value else ''


def senders(db, user_id):
    """The archived list with how many stored messages each sender has under Archived."""
    return [{'address': r['address'], 'name': r['name'], 'added': r['created_at'], 'messages': r['n']}
            for r in db.execute(
                'SELECT s.address,s.name,s.created_at,(SELECT COUNT(*) FROM gmail_messages m WHERE m.user_id=s.user_id AND m.archived=1 '
                "AND m.peer_email=s.address) AS n FROM archived_senders s WHERE s.user_id=? ORDER BY s.created_at DESC,s.address", (user_id,))]


def is_archived(db, user_id, address):
    return bool(address) and db.execute('SELECT 1 FROM archived_senders WHERE user_id=? AND address=?', (user_id, address)).fetchone() is not None


def apply(db, config, user_id):
    """File every not yet archived received message from an archived sender and mark it read. Returns the count."""
    rows = db.execute("SELECT m.message_id,m.label_ids,m.read_override FROM gmail_messages m JOIN archived_senders s ON s.user_id=m.user_id AND s.address=m.peer_email "
                      "WHERE m.user_id=? AND m.archived=0 AND m.label_ids NOT LIKE '%\"SENT\"%'", (user_id,)).fetchall()
    if not rows:
        return 0
    ids = [r['message_id'] for r in rows]
    with db:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            db.execute(f"UPDATE gmail_messages SET archived=1 WHERE user_id=? AND message_id IN ({','.join('?' * len(chunk))})", (user_id, *chunk))
    unread = [r['message_id'] for r in rows if gmail_web._is_unread(r)]
    if unread:
        write = prefs.effective(db, user_id, config)['gmail_write_read']
        gmail_web.mark_all_read(db, config, user_id, unread, write=write)
    return len(ids)


def add_sender(db, config, user_id, message_id):
    """Add the sender of one received message to the list and archive their mail. Returns {address, name, archived}
    or None when the message is unknown; raises ValueError when there is no sender to archive (sent mail, no address)."""
    row = db.execute('SELECT label_ids,peer_email,peer_name FROM gmail_messages WHERE user_id=? AND message_id=?', (user_id, message_id)).fetchone()
    if row is None:
        return None
    address = _address(row['peer_email'])
    if '"SENT"' in (row['label_ids'] or '') or not address:
        raise ValueError('Only mail you received has a sender that can be archived')
    with db:
        db.execute('INSERT OR IGNORE INTO archived_senders(user_id,address,name,created_at) VALUES (?,?,?,?)', (user_id, address, row['peer_name'] or '', int(time.time())))
    return {'address': address, 'name': row['peer_name'] or '', 'archived': apply(db, config, user_id)}


def remove_sender(db, user_id, address):
    """Take a sender off the list and put their mail back in the Inbox. Returns how many messages went back, or None."""
    address = _address(address)
    with db:
        if not db.execute('DELETE FROM archived_senders WHERE user_id=? AND address=?', (user_id, address)).rowcount:
            return None
        return db.execute('UPDATE gmail_messages SET archived=0 WHERE user_id=? AND archived=1 AND peer_email=?', (user_id, address)).rowcount
