# AI assistant (Codex CLI)

Inbox Tend can use the [Codex CLI](https://github.com/openai/codex) that is installed on the same server to
**suggest a task** from a message (with quoted evidence and an uncertainty note) and to **draft a reply**.
It is optional and **off by default**.

## What leaves your server

Only when you press an AI button on a message, and only if you enabled the assistant in **Settings**, the text of
*that one message* is sent to OpenAI through the Codex login of the operating-system user that runs Inbox Tend:
direction, sender, recipients, date, subject and body (first 20,000 characters), plus, if you ticked the option,
up to three small text/CSV attachments (8,000 characters each). The current date and your timezone are added. For a
reply draft, the guidance you typed is added too. Nothing else is sent, nothing is sent in the background, and no
reply is ever sent: results only fill a form or a draft that you review.

OpenAI's terms and your ChatGPT/Codex plan govern how that text is handled. If your mail is too sensitive for that,
leave the assistant off.

## Setup

1. Install the Codex CLI and sign in **as the user that runs Inbox Tend** (root on the reference server): `codex login`.
2. Open **Settings → AI assistant (Codex CLI)**. The page shows the detected version and login state.
3. Tick **Enable the AI assistant**, optionally set a model, an output language and a timeout, save, then press
   **Test Codex** (it sends a fixed message with no mail data).

The program that is executed is `codex` found on `PATH`, or the path in `INBOX_TEND_CODEX_BIN` in `.env`. It cannot
be changed from the web UI, so a hijacked web session cannot make the server run a different program.

## How Codex is run

```
codex exec --skip-git-repo-check --ephemeral --ignore-user-config --ignore-rules \
           --sandbox read-only -C <empty temporary dir> --color never \
           --output-schema <schema.json> -o <answer.json> -    # prompt on stdin
```

- **Read-only sandbox, empty working directory, no session files, your own Codex config and rules ignored.** The
  environment passed to the process is limited to `PATH`, `HOME`, locale, proxy and certificate variables, so no
  Inbox Tend secrets are inherited. The temporary directory is deleted afterwards.
- **The message is passed on stdin**, not on the command line, so it does not appear in the process list.
- **One request at a time per user, two overall**, with a timeout (Settings, 10–95 s). A timed-out process group is killed.

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
| Model | empty | Passed as `-m`; empty uses Codex's default |
| Output language | empty | Language of titles, descriptions, notes and drafts; empty follows the email |
| Timeout | 60 s | 10–95 s |
| Also send small text/CSV attachments | off | Up to 3 files, 8,000 characters each |

## Troubleshooting

- *Codex CLI was not found*: install it, or set `INBOX_TEND_CODEX_BIN=/full/path/to/codex` in `.env` and restart.
- *Codex is not signed in*: run `codex login` as the same user as the service (`systemctl show -p User inboxtend`;
  empty means root).
- *Did not answer within N seconds*: raise the timeout (max 95) or pick a faster model.
- Details of failures are in `journalctl -u inboxtend`.
