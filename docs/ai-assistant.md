# AI assistant

Inbox Tend can use an AI command-line tool that is installed on the same server to **suggest a task** from a message
(with quoted evidence and an uncertainty note) and to **draft a reply**. It is optional and **off by default**.

## Providers and models

Choose them in **Settings → AI assistant**. The provider list is fixed in the code; the model list is a convenience.

| Provider | Tool | Service that receives the message | Model list |
|---|---|---|---|
| `codex` (default) | [Codex CLI](https://github.com/openai/codex) | OpenAI | Read live from `codex debug models` (what your account offers) |
| `claude` | [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) | Anthropic | Built in: the `sonnet`, `opus`, `haiku` aliases and full model names |

Every list also has **Provider default** (the tool decides) and **Other…** to type a model name that is not listed.
The model name is passed as `-m` (Codex) or `--model` (Claude) after validation (letters, digits and `._:/-` only, so
it cannot be read as an option). Switching provider resets the model to the default. Providers that need an API key are not offered, because
secrets do not belong in web settings.

## What leaves your server

Only when you press an AI button on a message, and only if you enabled the assistant in **Settings**, the text of
*that one message* is sent to the chosen provider through the login of the operating-system user that runs Inbox Tend:
direction, sender, recipients, date, subject and body (first 20,000 characters), plus, if you ticked the option,
up to three small text/CSV attachments (8,000 characters each). The current date and your timezone are added. For a
reply draft, the guidance you typed is added too. Nothing else is sent, nothing is sent in the background, and no
reply is ever sent: results only fill a form or a draft that you review.

The provider's terms and your plan with it (ChatGPT/Codex or Claude) govern how that text is handled. If your mail is
too sensitive for that, leave the assistant off.

## Setup

1. Install the tool you want and sign in **as the user that runs Inbox Tend** (root on the reference server):
   `codex login` and/or `claude auth login`.
2. Open **Settings → AI assistant**. The page shows, for the selected provider, the detected version, its path and
   the login state.
3. Tick **Enable the AI assistant**, pick the provider and model, optionally set an output language and timeout,
   save, then press **Test the AI** (it sends a fixed message with no mail data).

The program that is executed is `codex` / `claude` found on `PATH`, or the path in `INBOX_TEND_CODEX_BIN` /
`INBOX_TEND_CLAUDE_BIN` in `.env`. These cannot be changed from the web UI, so a hijacked web session cannot make the
server run a different program.

## How the tools are run

```
codex  exec --skip-git-repo-check --ephemeral --ignore-user-config --ignore-rules --sandbox read-only \
            -C <empty temporary dir> --color never --output-schema <schema.json> -o <answer.json> [-m MODEL] -
claude -p --output-format json --json-schema '<schema>' --tools "" --no-session-persistence \
            --disable-slash-commands --strict-mcp-config [--model MODEL]
```

- **No tools or a read-only sandbox, an empty working directory, no session files.** Codex also ignores your own
  Codex config and rules. The process environment is limited to `PATH`, `HOME`, locale, proxy and certificate
  variables plus that provider's own login variables, so no Inbox Tend secrets are inherited. The temporary
  directory is deleted afterwards.
- **The message is passed on stdin**, not on the command line, so it does not appear in the process list.
- **One request at a time per user, two overall**, with a timeout (Settings, 10–1800 s). A timed-out process group is killed.

## Treating the email and the answer as untrusted

- The prompt tells the model the email is data, to ignore instructions inside it, and not to run commands or read
  files. The email is fenced in `<email>` tags and any tag-like text inside it is defanged.
- The answer must be JSON matching a schema, and is validated again by Inbox Tend: lengths, enum values, a real
  calendar date, a valid `HH:MM` time. A date without a time becomes 23:59 with a note.
- **Evidence is checked.** Each quote the model gives must appear in the email text, ignoring case, whitespace and
  curly quotes. Quotes that do not are shown with a warning instead of a check mark.
- A suggestion only pre-fills the normal “review suggested task” form. Nothing is saved until you confirm, and the
  task's activity history records `ai_assisted`.

## Settings reference

| Setting | Default | Meaning |
|---|---|---|
| Enable the AI assistant | off | Shows the AI buttons on messages |
| Provider | Codex | `codex` or `claude` |
| Model | provider default | From the list, or typed under *Other…* |
| Output language | empty | Language of titles, descriptions, notes and drafts; empty follows the email |
| Timeout | 60 s | 10–1800 s |
| Also send small text/CSV attachments | off | Up to 3 files, 8,000 characters each |
| Translate messages I open | off | Shows a translation in a third Inbox column; sends every opened message |
| Translate into | empty | Your preferred language, e.g. `English`, `Chinese (Simplified)` |

## Translation

**Settings → Translation** adds an opt-in third column on the Inbox page, to the right of the message, with a
translation of the open message into your preferred language (subject and text; attachments are not translated).
It uses the provider, model and timeout chosen under *AI assistant* but has its own switch, so it works without the
AI buttons being enabled.

- **Different consent from the buttons.** With translation on, *every message you open* is sent to the provider
  automatically, because that is what showing a translation means. Leave it off if that is not acceptable.
- **Each message is translated once per language.** The result is stored in the `message_translations` table of
  the SQLite database, keyed by user, message and target language (case-insensitive). Opening the message again,
  another day or after a restart, reads the database and makes **no AI call and spends no tokens**. A message that
  is already in your language is recorded as such, also without further calls. Only the **Retranslate** button,
  or changing the target language, causes a new call.
- Translations queue per user: if you click through messages quickly they are processed one after another (up to
  the timeout), and a request for a message that was just translated is answered from the saved result.
- Translations are removed when you disconnect Gmail (together with the stored messages), and they are covered by the
  database backups.
- Same safety rules as above: the message is untrusted text, the answer is validated, and the translation is
  shown as plain text only.

## Troubleshooting

- *CLI was not found*: install it, or set `INBOX_TEND_CODEX_BIN` / `INBOX_TEND_CLAUDE_BIN` to the full path in `.env`
  and restart.
- *Is not signed in*: sign in as the same user as the service (`systemctl show -p User inboxtend`; empty means root).
- *Did not accept the model name*: pick another model, or **Provider default**.
- *Did not answer within N seconds*: raise the timeout (max 1800) or pick a faster model. Behind a proxy such as Cloudflare, requests are cut at about 100 s regardless of this setting: the AI call still finishes and a translation is saved, but a task suggestion or reply draft is lost, so keep it near 90 s unless you reach the server directly.
- Details of failures are in `journalctl -u inboxtend`.
