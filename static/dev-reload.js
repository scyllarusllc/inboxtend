'use strict';
// Development only. Keep the current message and form state until the reader refreshes.
let devSnapshot;
let devChecking = false;
let devUpdateNotice;

function offerDevRefresh() {
  if (!devUpdateNotice) {
    devUpdateNotice = document.createElement('div');
    devUpdateNotice.className = 'dev-update-banner';
    devUpdateNotice.setAttribute('role', 'status');
    const label = document.createElement('span');
    label.textContent = 'Development update ready';
    const refresh = document.createElement('button');
    refresh.type = 'button';
    refresh.textContent = 'Refresh when ready';
    refresh.addEventListener('click', () => location.reload());
    const later = document.createElement('button');
    later.type = 'button';
    later.textContent = 'Later';
    later.addEventListener('click', () => { devUpdateNotice.hidden = true; });
    devUpdateNotice.append(label, refresh, later);
    document.body.append(devUpdateNotice);
  }
  devUpdateNotice.hidden = false;
}

function updateDevStyles(stamp) {
  const current = document.querySelector('link[rel="stylesheet"][href^="/static/styles.css"]');
  if (!current) return;
  const next = current.cloneNode();
  next.href = '/static/styles.css?dev=' + stamp;
  next.addEventListener('load', () => current.remove(), {once: true});
  next.addEventListener('error', () => next.remove(), {once: true});
  current.after(next);
}

async function checkDevVersion() {
  if (devChecking) return;
  devChecking = true;
  try {
    const response = await fetch('/api/dev/version?page=' + encodeURIComponent(location.pathname), {cache: 'no-store'});
    if (!response.ok) return;
    const snapshot = await response.json();
    if (devSnapshot) {
      if (snapshot.styles !== devSnapshot.styles) updateDevStyles(snapshot.styles);
      if (snapshot.server !== devSnapshot.server || snapshot.markup !== devSnapshot.markup ||
          snapshot.scripts !== devSnapshot.scripts) offerDevRefresh();
    }
    devSnapshot = snapshot;
  } catch (_) { /* The Python reload briefly closes the connection. */ }
  finally { devChecking = false; }
}

checkDevVersion();
setInterval(checkDevVersion, 1000);
