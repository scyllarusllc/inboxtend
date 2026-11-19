"""Offline email translation using explicitly installed Argos language packs."""
from __future__ import annotations

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


class TranslationError(Exception):
    pass


def language_code(value: str) -> str:
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


def installed_targets() -> list[dict]:
    try:
        package, _ = _argos()
    except TranslationError:
        return []
    codes = {p.to_code for p in package.get_installed_packages() if getattr(p, 'type', 'translate') == 'translate'}
    return [{'code': code, 'name': LANGUAGES[code]} for code in sorted(codes) if code in LANGUAGES]


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


def translate_message(message: dict, target: str) -> dict:
    target = language_code(target)
    _, argos = _argos()
    subject = (message.get('subject') or '')[:500]
    body = (message.get('body_text') or message.get('snippet') or '')[:40_000]
    sample = (body or subject).strip()
    if not sample:
        raise TranslationError('This message has no text to translate.')
    source = _source(sample)
    if source == target:
        return {'subject':subject, 'body':body, 'source_language':LANGUAGES.get(source, source), 'already_in_language':True}
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
    return {'subject':translated_subject[:500], 'body':translated_body[:40_000],
            'source_language':LANGUAGES.get(source, source), 'already_in_language':False}
