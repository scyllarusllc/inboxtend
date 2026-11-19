"""Offline email translation using explicitly installed Argos language packs."""
from __future__ import annotations
import threading

LANGUAGES = {
    'ar':'Arabic', 'de':'German', 'en':'English', 'es':'Spanish', 'fr':'French',
    'hi':'Hindi', 'id':'Indonesian', 'it':'Italian', 'ja':'Japanese', 'ko':'Korean',
    'nl':'Dutch', 'pl':'Polish', 'pt':'Portuguese', 'ru':'Russian', 'th':'Thai',
    'tr':'Turkish', 'vi':'Vietnamese', 'zh':'Chinese (Simplified)',
    'zt':'Chinese (Traditional)',
}
ALIASES = {name.casefold(): code for code, name in LANGUAGES.items()}
ALIASES.update({'chinese':'zh', 'simplified chinese':'zh', 'traditional chinese':'zt',
                'portuguese (brazilian)':'pt'})
MAX_TRANSLATION_CHARS = 5_000  # Local model inference grows with message length.


class TranslationError(Exception):
    pass


def language_code(value: str) -> str:
    if not isinstance(value, str):
        raise TranslationError('Choose a valid language.')
    code = (value or '').strip().casefold()
    code = ALIASES.get(code, code)
    if code not in LANGUAGES:
        raise TranslationError('Choose an installed translation language in Settings.')
    return code


def _argos():
    try:
        from argostranslate import package, translate
    except ImportError as exc:
        raise TranslationError('Local translation is not installed on this server. Install the translation dependencies and language packs.') from exc
    return package, translate


def runtime_ready() -> bool:
    try:
        _argos()
        from langdetect import detect_langs  # noqa: F401
        return True
    except (TranslationError, ImportError):
        return False


def installed_targets() -> list[dict]:
    try:
        package, _ = _argos()
    except TranslationError:
        return []
    codes = {p.to_code for p in package.get_installed_packages() if getattr(p, 'type', 'translate') == 'translate'}
    return [{'code': code, 'name': LANGUAGES[code]} for code in sorted(codes) if code in LANGUAGES]


def installed_pairs() -> list[dict]:
    try:
        package, _ = _argos()
    except TranslationError:
        return []
    pairs = {(p.from_code, p.to_code) for p in package.get_installed_packages()
             if getattr(p, 'type', 'translate') == 'translate' and p.from_code in LANGUAGES and p.to_code in LANGUAGES}
    return [{'source': source, 'target': target, 'label': f'{LANGUAGES[source]} → {LANGUAGES[target]}'}
            for source, target in sorted(pairs)]


_INSTALL_LOCK = threading.Lock()
_INSTALL = {'state':'idle', 'source':'', 'target':'', 'message':''}


def install_status() -> dict:
    with _INSTALL_LOCK:
        return dict(_INSTALL)


def start_install(source: str, target: str) -> dict:
    source, target = language_code(source), language_code(target)
    if source == target:
        raise TranslationError('Choose two different languages.')
    _argos()  # Fail before starting a background job when dependencies are absent.
    with _INSTALL_LOCK:
        if _INSTALL['state'] in ('downloading', 'installing'):
            raise TranslationError('A language pack is already being installed. Wait for it to finish.')
        _INSTALL.update(state='downloading', source=source, target=target, message='Checking and downloading the language pack…')
    threading.Thread(target=_install_worker, args=(source, target), daemon=True).start()
    return install_status()


def _install_worker(source: str, target: str) -> None:
    try:
        package, translate = _argos()
        package.update_package_index()
        found = next((item for item in package.get_available_packages()
                      if item.from_code == source and item.to_code == target), None)
        if found is None:
            raise TranslationError(f'No Argos language pack is available for {LANGUAGES[source]} → {LANGUAGES[target]}.')
        downloaded = found.download()
        with _INSTALL_LOCK:
            _INSTALL.update(state='installing', message='Installing and preparing the language pack…')
        package.install_from_path(downloaded)
        # Prime sentence splitting during setup, never during the first email translation.
        translate.translate('Hello. This is a test.', source, target)
        with _INSTALL_LOCK:
            _INSTALL.update(state='ready', message=f'{LANGUAGES[source]} → {LANGUAGES[target]} is ready.')
    except Exception as exc:
        with _INSTALL_LOCK:
            _INSTALL.update(state='error', message=str(exc)[:200] or 'Could not install the language pack.')


def _source(text: str) -> str:
    try:
        from langdetect import DetectorFactory, detect_langs
        DetectorFactory.seed = 0
        detected = detect_langs(text[:3000])
    except Exception as exc:
        raise TranslationError('Could not identify the email language. Check the message text and installed language packs.') from exc
    if not detected or detected[0].prob < .65:
        raise TranslationError('Could not identify the email language confidently.')
    code = detected[0].lang
    return {'zh-cn':'zh', 'zh-tw':'zt'}.get(code, code)


def translate_message(message: dict, target: str, max_chars: int = MAX_TRANSLATION_CHARS) -> dict:
    target = language_code(target)
    _, argos = _argos()
    subject = (message.get('subject') or '')[:500]
    full_body = message.get('body_text') or message.get('snippet') or ''
    body = full_body[:max_chars]
    truncated = len(full_body) > max_chars
    sample = (body or subject).strip()
    if not sample:
        raise TranslationError('This message has no text to translate.')
    source = _source(sample)
    if source == target:
        return {'subject':subject, 'body':full_body, 'source_language':LANGUAGES.get(source, source), 'already_in_language':True}
    installed = argos.get_installed_languages()
    from_lang = next((lang for lang in installed if lang.code == source), None)
    to_lang = next((lang for lang in installed if lang.code == target), None)
    if not from_lang or not to_lang or not from_lang.get_translation(to_lang):
        raise TranslationError(f'No offline translation pack is installed for {LANGUAGES.get(source, source)} → {LANGUAGES.get(target, target)}. Ask the server operator to install that direction.')
    try:
        translated_subject = argos.translate(subject, source, target) if subject else ''
        translated_body = argos.translate(body, source, target) if body else ''
    except Exception as exc:
        raise TranslationError('Offline translation failed. Check the installed language packs.') from exc
    if truncated:
        translated_body += f'\n\n[Only the first {max_chars:,} characters were translated to keep local translation responsive. Read the original message for the rest.]'
    return {'subject':translated_subject[:500], 'body':translated_body,
            'source_language':LANGUAGES.get(source, source), 'already_in_language':False}
