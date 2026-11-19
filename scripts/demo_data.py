#!/usr/bin/env python3
"""Build a demo database with FICTIONAL people, mail, tasks, flags, person cards, topics and a translation.

Used for `make demo` and for the screenshots in README and on the home page. Nothing here comes from a real mailbox.
All times are relative to now, so the demo always looks current.

    python3 scripts/demo_data.py [DB_FILE]      # default: /tmp/inboxtend-demo/tasks.sqlite3
"""
from __future__ import annotations
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gmail_web  # noqa: E402
import settings  # noqa: E402
from auth import hash_password  # noqa: E402
from tasks import connection, create_task, update_task  # noqa: E402

USERNAME, PASSWORD = 'demo@example.org', 'demo-password-123'
# Two connected Gmail accounts: the first has no id prefix, later ones do (see gmail_web._store).
ACCOUNTS = {1: {'id': 1, 'prefix': '', 'email': 'jordan.rivera@gmail.com'}, 2: {'id': 2, 'prefix': '2~', 'email': 'jordan@riverside.example'}}
TZ = ZoneInfo('America/New_York')
ME = 'Jordan Rivera <demo@example.org>'
PDF = {'part_id': '1', 'filename': 'Invoice-1042.pdf', 'mime_type': 'application/pdf', 'size': 48211, 'content': b'%PDF-1.4 demo', 'skipped_reason': ''}
CSV = {'part_id': '2', 'filename': 'usage-november.csv', 'mime_type': 'text/csv', 'size': 96, 'content': b'service,hours\napi,1200\nstorage,340\n', 'skipped_reason': ''}


def build(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ('', '-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)
    db = connection(path)
    now = int(time.time())
    midnight = int(datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())   # start of today, in the demo timezone
    n = [0]
    inbox, sent = [], []

    def header_date(ts, offset_hours):
        return datetime.fromtimestamp(ts, timezone(timedelta(hours=offset_hours))).strftime('%a, %d %b %Y %H:%M:%S ') + '%+03d00' % offset_hours

    def add(subject, who, ts, body, *, offset=-5, unread=False, mine=False, thread=None, listed=True, attachments=(), topics=(), acct=1):
        n[0] += 1
        account = ACCOUNTS[acct]
        mid = account['prefix'] + f'demo{n[0]:03d}'
        labels = ['SENT'] if mine else ['INBOX'] + (['UNREAD'] if unread else [])
        frm, to = (ME, who) if mine else (who, ME)
        gmail_web._store(db, 1, {
            'id': f'demo{n[0]:03d}', 'thread_id': thread or f'th{n[0]:03d}', 'snippet': ' '.join(body.split())[:140], 'internal_date': ts * 1000,
            'label_ids': labels, 'size_estimate': len(body), 'all_headers': [],
            'headers': {'subject': subject, 'from': frm, 'to': to, 'date': header_date(ts, offset)},
            'body_text': body, 'body_html': '', 'attachments': [dict(a) for a in attachments]}, 1, account=account)
        if listed:
            (sent if mine else inbox).append(mid)
        if topics:
            db.execute('INSERT OR REPLACE INTO message_topics(user_id,message_id,topics,provider,guessed_at) VALUES (1,?,?,?,?)', (mid, ','.join(topics), 'demo', now))
        return mid

    def history(who, thread_prefix, count, offset, reply_hours=3, acct=1):
        """Older back-and-forth, outside the synced window: gives the person a history (reply speed, hours)."""
        for i in range(count):
            day = (midnight - (45 + i * 52) * 86400) // 86400 * 86400
            t0 = day + ((9 - offset) % 24) * 3600          # 09:00 in the sender's own timezone
            thread = f'old-{thread_prefix}{i}'             # Never the id of a thread shown in the list
            add('Re: earlier thread', who, t0, 'Thanks, noted. I will send the update shortly.', offset=offset, thread=thread, listed=False, acct=acct)
            add('Earlier thread', who, t0 - reply_hours * 3600, 'Could you send the latest numbers when you have a moment?', mine=True, offset=-5, thread=thread, listed=False, acct=acct)

    with db:
        for table in ('gmail_messages', 'gmail_attachments', 'message_topics', 'message_translations', 'sender_countries', 'tasks', 'task_events',
                      'user_settings', 'gmail_connections', 'user_sessions', 'users'):
            db.execute(f'DELETE FROM {table}')
        stamp = datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO users(id,username,password_hash,role,created_at,updated_at) VALUES (1,?,?,?,?,?)',
                   (USERNAME, hash_password(PASSWORD), 'admin', stamp, stamp))

        # ---- today
        maria = 'María García <maria.garcia@empresa.com.mx>'
        m_id = add('Propuesta de renovación del contrato', maria, now - 240,
                   'Hola Jordan,\n\nGracias por la reunión de ayer. Adjunto la propuesta de renovación del contrato: el precio anual es de 24.000 USD, '
                   'con pago trimestral. ¿Podríamos confirmar la fecha de firma antes del viernes a las 17:00?\n\nQuedo atenta a sus comentarios.\n\n'
                   'Saludos cordiales,\nMaría García\nDirectora de Compras\nEmpresa SA de CV\nAv. Paseo de la Reforma 222, 06600 Ciudad de México\nTel. +52 55 1234 5678',
                   offset=-6, unread=True, topics=('money', 'meeting'), acct=2)
        db.execute('INSERT OR REPLACE INTO message_translations(user_id,message_id,language_key,language,subject,body,source_language,same_language,provider,model,created_at) '
                   'VALUES (1,?,?,?,?,?,?,0,?,?,?)', (m_id, 'english', 'English', 'Contract renewal proposal',
                   'Hello Jordan,\n\nThank you for yesterday\'s meeting. I am attaching the contract renewal proposal: the annual price is USD 24,000, paid quarterly. '
                   'Could we confirm the signing date before Friday at 5:00 pm?\n\nI look forward to your comments.\n\nKind regards,\nMaría García\n'
                   'Purchasing Director\nEmpresa SA de CV\nAv. Paseo de la Reforma 222, 06600 Mexico City\nTel. +52 55 1234 5678', 'Spanish', 'codex', 'provider default', now - 200))
        history(maria, 'thm', 4, -6, 4, acct=2)
        hiro = 'Hiroshi Tanaka <h.tanaka@acme.co.jp>'
        add('Re: Quarterly review', hiro, now - 1080,
            'Hi Jordan,\n\nThe numbers look good on our side. Can we move the review to Thursday morning my time? I can share the updated deck tonight.\n\n'
            'Best regards,\nHiroshi Tanaka\nSales Manager, Acme Corporation\nShibuya, Tokyo\nTEL +81-3-1234-5678', offset=9, unread=True, thread='thh0', topics=('job', 'meeting'), acct=2)
        add('Quarterly review', hiro, now - 30 * 3600, 'Please find the agenda for the quarterly review attached. Let me know if anything is missing.', offset=9, thread='thh0', topics=('job', 'meeting'), acct=2)
        history(hiro, 'thh', 5, 9, 3, acct=2)
        add('Invoice #1042 for November', 'CloudHost Billing <billing@cloudhost.example>', now - 1620,
            'Your invoice for November is attached. Amount due: $1,240.00. Payment is due within 14 days by card or bank transfer.\n\nThank you for your business.',
            offset=0, unread=True, attachments=(PDF, CSV), topics=('money',), acct=2)

        # Fictional example for the Inbox Tend -> AgentSteers mock handoff.
        # The example is local-only and never comes from a real mailbox.
        demo_site_id = add(
            'Student registration page needs attention',
            'Campus Web Team <webteam@campus.example>',
            now - 2400,
            'Hello Jordan,\\n\\nPlease investigate why the student registration form sometimes fails. '
            'Prepare a list of proposed changes, but do not modify or publish the live website.\\n\\n'
            'Thanks,\\nCampus Web Team',
            offset=-5, unread=True, topics=('job', 'tech'))

        # ---- yesterday
        add('Staff Engineer opportunity at Contoso', 'Sarah Lee <sarah@talentco.io>', midnight - 3 * 3600,
            'Hi Jordan, I came across your profile and think you would be a great fit for a Staff Engineer role at Contoso (remote, $180k-$210k). '
            'Are you open to a quick chat this week?\n\nSarah Lee\nTechnical Recruiter, TalentCo\nAustin, TX', offset=-6, topics=('job', 'money'))
        history('Sarah Lee <sarah@talentco.io>', 'ths', 2, -6, 5)
        add('Saturday?', 'Alex Chen <alex.chen@example.org>', midnight - 6 * 3600, 'hey!! u coming saturday? lol bring snacks, mum says hi', offset=-5, unread=True, topics=('friends_family',))
        add('Dinner tonight?', 'Sam Taylor <sam.taylor@example.org>', midnight - 9 * 3600, 'I miss you, my love. Dinner tonight at our place? I will bring the wine.\n\nYours always,\nSam', offset=-5, unread=True, topics=('love',))
        add('Your flight to Lisbon is confirmed', 'SkyAir <noreply@skyair.example>', midnight - 15 * 3600,
            'Booking reference LX42AB. Flight SA 301, New York to Lisbon, departs 09 Dec at 18:45. Check-in opens 24 hours before departure.', offset=0, topics=('travel',))

        # ---- earlier
        dana = 'Dana Park <dana.park@example.org>'
        add('Lunch on Friday?', dana, midnight - 2 * 86400 + 5 * 3600, 'Are you free for lunch on Friday? There is a new ramen place near the office.', offset=-5, thread='thd0', topics=('friends_family', 'food'))
        add('Re: Lunch on Friday?', dana, midnight - 2 * 86400 + 8 * 3600, 'Friday works. 12:30?', mine=True, thread='thd0', topics=('friends_family', 'food'))
        history(dana, 'thd', 3, -5, 2)
        add('Rechnung Oktober', 'Hans Müller <hans.mueller@firma.de>', midnight - 3 * 86400,
            'Guten Tag Jordan,\n\nanbei die Rechnung für Oktober über 3.480,00 EUR, zahlbar innerhalb von 30 Tagen.\n\nMit freundlichen Grüßen\nHans Müller\nFirma GmbH, Hamburg\nTel. +49 40 1234567', offset=1, topics=('money',))
        add('Security alert: new sign-in', 'Accounts <no-reply@accounts.example>', midnight - 4 * 86400,
            'A new sign-in to your account from Chrome on Windows was detected. If this was not you, reset your password now.', offset=0, topics=('security',))
        add('Design Weekly #42', 'Design Weekly <news@designweekly.example>', midnight - 5 * 86400,
            'This week in design: accessible forms, colour systems that scale and what is next for typography. Read the full issue online. Unsubscribe | View in browser', offset=0, topics=('news',))
        add('Your order has shipped', 'Shop <orders@shop.example>', midnight - 6 * 86400,
            'Your order #88231 (running shoes, size 42) has shipped. Tracking number 1Z999. Estimated delivery Thursday.', offset=0, topics=('shopping',))
        add('Appointment reminder', 'Riverside Clinic <clinic@riverside.example>', midnight - 8 * 86400,
            'This is a reminder of your appointment with Dr. Lee on Monday at 09:30. Please bring your insurance card.', offset=-5, topics=('health', 'meeting'))
        add('Draft services agreement', 'Bennett & Co <contracts@bennett.co.uk>', midnight - 11 * 86400,
            'Attached is the draft services agreement. Please review the indemnification and termination clauses before signing.\n\nKind regards,\nPriya Nair\nSolicitor, Bennett & Co, London', offset=0, topics=('legal',), acct=2)
        add('Weekend sale: 50% off everything', 'Store <deals@store.example>', midnight - 13 * 86400,
            'Huge sale! Use code SAVE50 for 50 percent off everything, this weekend only. Hurry, limited stock.', offset=0, topics=('promo',))

        db.execute('UPDATE gmail_messages SET in_inbox=0, in_sent=0')
        for ids, column in ((inbox, 'in_inbox'), (sent, 'in_sent')):
            if ids:
                db.execute(f"UPDATE gmail_messages SET {column}=1 WHERE message_id IN ({','.join('?' * len(ids))})", ids)

        # ---- what the AI would have guessed (stored directly: the demo makes no AI request)
        for peer, country, conf, reason, city, company, role, language, relation, formality, brevity in (
                ('maria.garcia@empresa.com.mx', 'MX', 'high', '.com.mx domain, +52 phone code, Mexico City address', 'Mexico City', 'Empresa SA de CV', 'Purchasing Director', 'Spanish', 'client', 'formal', 'medium'),
                ('h.tanaka@acme.co.jp', 'JP', 'high', '.co.jp domain, +81 phone code, Tokyo address', 'Tokyo', 'Acme Corporation', 'Sales Manager', 'English', 'client', 'formal', 'short'),
                ('sarah@talentco.io', 'US', 'high', 'Austin, TX signature, -06:00 timezone', 'Austin', 'TalentCo', 'Technical Recruiter', 'English', 'recruiter', 'casual', 'short'),
                ('hans.mueller@firma.de', 'DE', 'high', '.de domain, +49 phone code, Hamburg', 'Hamburg', 'Firma GmbH', '', 'German', 'vendor', 'formal', 'short'),
                ('contracts@bennett.co.uk', 'GB', 'high', '.co.uk domain, London signature', 'London', 'Bennett & Co', 'Solicitor', 'English', 'service', 'formal', 'medium'),
                ('billing@cloudhost.example', '', 'low', '', '', 'CloudHost', '', 'English', 'service', 'formal', 'short'),
                ('dana.park@example.org', '', 'low', '', '', '', '', 'English', 'friend_family', 'casual', 'short'),
                ('alex.chen@example.org', '', 'low', '', '', '', '', 'English', 'friend_family', 'casual', 'short'),
                ('sam.taylor@example.org', '', 'low', '', '', '', '', 'English', 'friend_family', 'casual', 'short'),
                ('news@designweekly.example', '', 'low', '', '', 'Design Weekly', '', 'English', 'newsletter', 'casual', 'long'),
                ('no-reply@accounts.example', '', 'low', '', '', '', '', 'English', 'automated', 'formal', 'short'),
                ('noreply@skyair.example', '', 'low', '', '', 'SkyAir', '', 'English', 'automated', 'formal', 'short'),
                ('orders@shop.example', '', 'low', '', '', 'Shop', '', 'English', 'automated', 'formal', 'short'),
                ('clinic@riverside.example', '', 'low', '', '', 'Riverside Clinic', '', 'English', 'service', 'formal', 'short'),
                ('deals@store.example', '', 'low', '', '', 'Store', '', 'English', 'newsletter', 'casual', 'short')):
            db.execute("INSERT OR REPLACE INTO sender_countries(user_id,peer_email,country,confidence,reason,provider,guessed_at,basis,city,company,role,language,relationship,formality,brevity) "
                       "VALUES (1,?,?,?,?,'demo',?,'profile',?,?,?,?,?,?,?)", (peer, country, conf, reason, now, city, company, role, language, relation, formality, brevity))

        for acct, synced, scopes, remote in (
                (1, now - 420, 'https://www.googleapis.com/auth/gmail.modify', {'all': 1210, 'threads': 880, 'inbox': 640, 'inbox_unread': 12, 'sent': 190}),
                (2, now - 300, 'https://www.googleapis.com/auth/gmail.readonly', {'all': 930, 'threads': 650, 'inbox': 846, 'inbox_unread': 19, 'sent': 122})):
            a = ACCOUNTS[acct]
            db.execute("INSERT INTO gmail_connections(id,user_id,email,refresh_token,connected_at,last_sync,scopes,prefix,remote_counts) VALUES (?,1,?,x'00',?,?,?,?,?)",
                       (a['id'], a['email'], now - 86400 * 9, synced, scopes, a['prefix'], json.dumps(dict(remote, checked_at=now))))

    settings.save(db, 1, {'timezone': 'America/New_York', 'inbox_view': 'threads', 'mark_read_on_open': False, 'ai_enabled': True, 'ai_provider': 'codex',
                          'flags_enabled': True, 'topics_enabled': True, 'translate_enabled': True, 'translate_language': 'English'})

    import flags, topics   # The demo must never ask an AI anything: every listed message and every sender is already filled in.
    assert flags.pending_count(db, 1) == 0 and topics.pending_count(db, 1) == 0, 'demo data would trigger AI requests'

    def due(days, hour):
        return (datetime.now(TZ) + timedelta(days=days)).strftime('%Y-%m-%d'), f'{hour:02d}:00'
    for key, title, desc, priority, days, hour, status, owner in (
            ('contract', 'Confirm the contract renewal with María', 'Signing date before Friday 17:00; check the quarterly payment terms.', 'high', 1, 17, 'in_progress', 'Jordan'),
            ('review', 'Reply to Hiroshi about the quarterly review', 'He proposes Thursday morning Tokyo time.', 'high', 0, 21, 'new', 'Jordan'),
            ('invoice', 'Pay invoice #1042', 'CloudHost, $1,240.00, due within 14 days.', 'medium', 10, 12, 'new', ''),
            ('domain', 'Renew the domain', 'The registrar says it expires this week.', 'high', -2, 9, 'new', 'Jordan'),
            ('hotel', 'Book a hotel in Lisbon', '', 'low', None, 0, 'waiting', ''),
            ('report', 'Send the October report', '', 'medium', -9, 17, 'done', 'Jordan')):
        date, time_ = due(days, hour) if days is not None else (None, None)
        task = create_task(db, {'id': f'task-{key}', 'provider': 'gmail', 'thread_id': ''},
                           {'title': title, 'description': desc, 'priority': priority, 'owner': owner, 'due_date': date, 'due_time': time_,
                            'due_timezone': 'America/New_York'}, 1)
        if status != 'new':
            update_task(db, task['id'], {'revision': task['revision'], 'status': status}, 1)
    create_task(db,
                {'id': demo_site_id, 'provider': 'demo', 'thread_id': ''},
                {'title': 'Review student registration form issue (mock handoff)',
                 'description': 'Investigate the reported registration form failures and propose changes. Do not publish or edit the live site.',
                 'priority': 'medium', 'owner': 'Jordan', 'due_date': None, 'due_time': None,
                 'due_timezone': 'America/New_York'}, 1)
    db.close()


if __name__ == '__main__':
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/inboxtend-demo/tasks.sqlite3')
    build(target)
    print(f'Demo database written to {target}\nSign in with {USERNAME} / {PASSWORD}\n'
          f'Run:  INBOX_TEND_DB={target} INBOX_TEND_PORT=8781 python3 app.py   (or: make demo)')
