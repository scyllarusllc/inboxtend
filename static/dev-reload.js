'use strict';
// Development only: one FastAPI port serves the page, scripts, styles and API.
let devVersion;
async function checkDevVersion() {
  try {
    const response = await fetch('/api/dev/version', {cache: 'no-store'});
    if (!response.ok) return;
    const {version} = await response.json();
    if (devVersion && version !== devVersion) location.reload();
    devVersion = version;
  } catch (_) { /* The Python reload briefly closes the connection. */ }
}
checkDevVersion();
setInterval(checkDevVersion, 1000);
