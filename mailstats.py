"""Numbers for the charts at the top of the Inbox: computed from the local SQLite copy, no Gmail request."""
from __future__ import annotations
import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DAYS = 30
TOP = 6

def _unread(r):
    return '"UNREAD"' in (r['label_ids'] or '') if r['read_override'] is None else r['read_override'] == 0

def compute(db, user_id, tz_name, account_id=None, now=None):
    tz = ZoneInfo(tz_name)
    now = now or time.time()
    today = datetime.fromtimestamp(now, tz).date()
    first = today - timedelta(days=DAYS - 1)
    start_ms = int(datetime.combine(first, datetime.min.time(), tz).timestamp() * 1000)
    scope, args = (' AND account_id=?', (user_id, account_id)) if account_id is not None else ('', (user_id,))
    days = {first + timedelta(days=i): [0, 0] for i in range(DAYS)}   # date -> [received, sent]
    senders, names, topics = Counter(), {}, Counter()
    unread = 0
    for r in db.execute('SELECT message_id,internal_date,in_inbox,in_sent,peer_email,peer_name,label_ids,read_override '
                        'FROM gmail_messages WHERE user_id=? AND (in_inbox=1 OR in_sent=1)' + scope + ' ORDER BY internal_date DESC', args):
        if r['in_inbox'] and _unread(r): unread += 1
        if r['internal_date'] < start_ms: continue
        day = datetime.fromtimestamp(r['internal_date'] / 1000, tz).date()
        if day not in days: continue
        if r['in_inbox']:
            days[day][0] += 1
            who = (r['peer_email'] or '').lower()
            if who:
                senders[who] += 1
                names.setdefault(who, r['peer_name'] or '')   # Newest first, so the latest name wins.
        if r['in_sent']: days[day][1] += 1
    for r in db.execute('SELECT t.topics FROM message_topics t JOIN gmail_messages m ON m.user_id=t.user_id AND m.message_id=t.message_id '
                        'WHERE t.user_id=? AND m.internal_date>=?' + (' AND m.account_id=?' if account_id is not None else ''),
                        (user_id, start_ms) + (() if account_id is None else (account_id,))):
        for code in filter(None, (r[0] or '').split(',')): topics[code] += 1
    series = [{'date': d.isoformat(), 'received': v[0], 'sent': v[1]} for d, v in days.items()]
    week = series[-7:]
    return {'days': series, 'unread': unread, 'today_received': series[-1]['received'],
            'week_received': sum(x['received'] for x in week), 'week_sent': sum(x['sent'] for x in week),
            'senders': [{'address': a, 'name': names.get(a, ''), 'count': n} for a, n in senders.most_common(TOP)],
            'topics': [{'code': c, 'count': n} for c, n in topics.most_common(TOP)], 'window_days': DAYS}
