// Copies the parts of Font Awesome Free that the app uses into static/vendor/fontawesome (committed, so that
// running the app needs no Node). Re-run after changing the version in package.json:  npm run vendor:fa
import {cpSync, mkdirSync, readFileSync, rmSync} from 'node:fs';
const from = 'node_modules/@fortawesome/fontawesome-free', to = 'static/vendor/fontawesome';
rmSync(to, {recursive: true, force: true});
mkdirSync(`${to}/css`, {recursive: true}); mkdirSync(`${to}/webfonts`, {recursive: true});
for (const f of ['fontawesome.min.css', 'solid.min.css', 'regular.min.css']) cpSync(`${from}/css/${f}`, `${to}/css/${f}`);
for (const f of ['fa-solid-900.woff2', 'fa-regular-400.woff2']) cpSync(`${from}/webfonts/${f}`, `${to}/webfonts/${f}`);
cpSync(`${from}/LICENSE.txt`, `${to}/LICENSE.txt`);
const {version} = JSON.parse(readFileSync(`${from}/package.json`, 'utf8'));
console.log(`Font Awesome Free ${version} copied to ${to}`);
