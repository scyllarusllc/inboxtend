import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { headers } from 'next/headers';
import { redirect, notFound } from 'next/navigation';
import LegacyScripts from '../legacy-scripts';

const pages = {
  '': ['home', [], false],
  login: ['login', ['login.js'], false],
  privacy: ['privacy', [], false],
  'user/inbox': ['inbox', ['common.js', 'inbox.js', 'vendor/chartjs/chart.umd.min.js', 'stats.js'], true],
  'user/tasks': ['tasks', ['common.js', 'tasks.js'], true],
  'user/settings': ['settings', ['common.js', 'settings.js'], true],
};
const root = process.cwd();

async function signedIn() {
  const requestHeaders = await headers();
  const response = await fetch((process.env.INBOX_TEND_API_ORIGIN || 'http://127.0.0.1:8765') + '/api/session', {
    headers: { cookie: requestHeaders.get('cookie') || '' }, cache: 'no-store',
  }).catch(() => null);
  return response?.ok || false;
}

export default async function Page({ params }) {
  const key = ((await params).segments || []).join('/');
  if (key === 'user/dashboard') redirect('/user/inbox');
  const selected = pages[key];
  if (!selected) notFound();
  const [name, scripts, protectedPage] = selected;
  const authenticated = await signedIn();
  if (protectedPage && !authenticated) redirect('/login');
  if (key === 'login' && authenticated) redirect('/user/inbox');
  const content = readFileSync(join(root, 'static/pages', name + '.html'), 'utf8');
  const nav = [
    ['Inbox', '/user/inbox', 'fa-inbox'], ['Tasks', '/user/tasks', 'fa-list-check'],
    ...(authenticated ? [['Settings', '/user/settings', 'fa-gear']] : [['Sign in', '/login', 'fa-right-to-bracket']]),
  ];
  return <>
    <a className="skip-link" href="#main">Skip to main content</a>
    <header className="site-header"><div className="header-inner">
      <a className="brand" href={authenticated ? '/user/inbox' : '/'}><span className="brand-mark"><i className="fa-solid fa-inbox" /></span><span className="text-lg tracking-tight">Inbox <strong>Tend</strong></span></a>
      <nav className="primary-nav" aria-label="Main navigation">{nav.map(([label, href, icon]) => <a key={href} className="nav-link" href={href} aria-current={key === href.slice(1) ? 'page' : undefined}><i className={'fa-solid ' + icon + ' fa-fw'} aria-hidden="true" /><span>{label}</span></a>)}
        {authenticated && <button id="logout" type="button"><i className="fa-solid fa-right-from-bracket fa-fw" aria-hidden="true" /><span>Sign out</span></button>}
      </nav><span className="environment-badge hidden xl:inline-flex"><span className="status-dot" />Preview workspace</span>
    </div></header>
    <div dangerouslySetInnerHTML={{ __html: content }} />
    <footer className="site-footer"><span>Inbox Tend · Local-first preview</span><span><a href="/privacy">Privacy</a> · Scyllarus LLC · <a href="/LICENSE">AGPL-3.0</a> · <a href="https://github.com/scyllarusllc/inboxtend">Source code</a></span></footer>
    <LegacyScripts scripts={scripts} />
  </>;
}
