# Front end: Tailwind CSS and Font Awesome

The default development UI is served by **FastAPI** from the page bodies and JavaScript in `static/`, styled with **Tailwind CSS v4** and **Font Awesome Free**. Shared interactions are moving into self-hosted **Stimulus** controllers without adding a frontend server. `make dev` offers a refresh when page or script files change and swaps compiled CSS without reloading the page. The optional Next.js shell can be run with `make dev-next`; it requires Node.js.

| What | Where |
|---|---|
| Design system source (Tailwind + our components) | `static/src/app.css` |
| Compiled stylesheet (committed, minified) | `static/styles.css` |
| Page bodies / shared header and footer | `static/pages/*.html`, `static/layout.html` |
| Icons (committed copy) | `static/vendor/fontawesome/` |
| Country flags (committed copy, 257 SVGs) | `static/vendor/flag-icons/`; the accepted codes are generated into `countries.py` |
| Next.js navigation | `frontend/app/[[...segments]]/page.jsx` |
| Shared interaction controllers | `static/stimulus-app.js`, `static/vendor/stimulus/` |
| Build tooling | `package.json`, `package-lock.json` |

## Working on the styles

```bash
npm ci                 # once
npm run watch:css      # recompiles static/styles.css while you edit
make css               # one-off build, also refreshes the full-page HTML copies
npm run vendor         # re-copy Font Awesome and the flag icons after changing a version in package.json
```

Tailwind scans `static/pages`, `static/layout.html`, `static/*.js` and `app.py` for class names, so a class used in
any of them (including the ones the scripts put on generated elements) is compiled. CI rebuilds the stylesheet and fails
if the committed one differs.

## Why the CSS looks the way it does

The content security policy is `default-src 'self'; style-src 'self'; script-src 'self'`: no inline styles, no inline
scripts, no CDN. That rules out Tailwind's in-browser build and any icon CDN, which is why the CSS is precompiled and
Font Awesome is self-hosted (`/static/vendor/...`, served by `app.py` for `.css` and `.woff2` files only, with a
one-day cache).

## Rules that keep every page consistent

1. **One palette.** Slate neutrals; indigo for actions, selection and links; emerald, amber and rose for status.
2. **One control look, set in the base layer.** Every `<button>`, `<input>`, `<select>` and `<textarea>` is styled
   without any class, so a control someone forgot to style still matches. Variants only recolour: `.action` is the
   one main action of a form or bar, `.danger` is destructive.
3. **One card** (`.panel`), **one badge** (`.badge` and the tones used by `.task-pill`, `.read-badge`, `.count-badge`),
   **one segmented control** (`.folder-tabs`), **one form grid** (`.task-form`, also used by the suggested-task form).
4. **Layout with utilities, repeated pieces with components.** One-off layout uses Tailwind utilities in the HTML;
   anything the scripts create more than once (`.message`, `.task-item`, `.attachment-row`, …) is a named component in
   `static/src/app.css`, built from utilities with `@apply`, so the JavaScript only needs a class name.
5. **Icons** come from `icon(name)` in `static/common.js` (a small name-to-Font-Awesome map) or an `<i class="fa-solid
   fa-…">` in the HTML. Text that the scripts rewrite (tab labels with counts) carries no icon.
6. **Grid children get `min-w-0`** (`.workspace-grid > *`). Without it, a single long line forces its column wider than
   the screen.
6a. **Topic icons** are small round badges (`.topic`, one colour per topic via `data-topic`) in list rows and labelled `.topic-chip` buttons on the open message. The topic list lives in `topics.py` and in the `TOPICS` map of `static/common.js`; add a topic in both and give it a colour in `static/src/app.css`.
6b. **Flags are SVG images** (`fi fi-jp`, from flag-icons), not emoji: flag emoji do not render on Windows. A flag is only shown when the AI guessed a country; low confidence is dimmed, and the tooltip says why. "x ago" labels (`agoSpan()`) are refreshed every 30 s and carry the exact time in the tooltip.
7. **The mail list is split by age** (`.date-heading`, `data-age` on each row: today green, yesterday amber, earlier plain). The day is the calendar day in the timezone chosen in Settings (`ageOf()` in `static/common.js`), a conversation or person counts by its newest message, and the selected row (`aria-pressed`) always overrides the tint.
8. Full width pages; forms (Settings) are limited to a readable width next to a section navigation.

Third-party licenses: see `THIRD_PARTY_LICENSES.md`.
