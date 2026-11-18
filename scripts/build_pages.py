"""Keep static full-page artifacts in sync for existing running demo processes."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import PAGES, render_page  # noqa: E402

for name in PAGES:
    (ROOT / 'static' / f'{name}.html').write_bytes(render_page(name))
print('Built shared-layout HTML pages')
