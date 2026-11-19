"""Install only the requested Argos translation directions, e.g. en_zh es_en."""
from __future__ import annotations
import argparse
from argostranslate import package, translate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pairs', nargs='+', help='Language directions such as en_zh, es_en, en_ja')
    args = parser.parse_args()
    pairs = []
    for pair in args.pairs:
        parts = pair.lower().split('_')
        if len(parts) != 2 or not all(part.isalpha() and 2 <= len(part) <= 3 for part in parts):
            parser.error(f'Invalid language direction: {pair}')
        pairs.append(tuple(parts))
    package.update_package_index()
    available = package.get_available_packages()
    for source, target in pairs:
        found = next((item for item in available if item.from_code == source and item.to_code == target), None)
        if not found:
            parser.error(f'No Argos language pack exists for {source} → {target}')
        print(f'Downloading {source} → {target}…', flush=True)
        package.install_from_path(found.download())
        # Prime sentence splitting now, so opening an email never triggers a model download.
        translate.translate('Hello. This is a test.', source, target)
        print(f'Installed {source} → {target}.', flush=True)


if __name__ == '__main__':
    main()
