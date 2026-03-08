# Clawmail

AI-powered email triage CLI. Connects to Gmail, iCloud, or other IMAP providers, classifies emails with Claude, and acts on them based on your rules.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Gmail, iCloud, or other IMAP email account with an app-specific password
- [Anthropic API key](https://console.anthropic.com/)

## Installation

### From GitHub (SSH)

```bash
uv tool install git+ssh://git@github.com/tommy/clawmail.git
```

### From source (editable — picks up changes automatically)

```bash
git clone git@github.com:tommy/clawmail.git
cd clawmail
uv tool install --editable .
```

## Setup

```bash
clawmail configure
```

This prompts for your email address, app password, and Anthropic API key. Provider-specific defaults (IMAP host, folder names) are auto-detected from your email domain. Credentials are stored in your OS keychain.

## Usage

```bash
# List available IMAP folders/labels
clawmail folders

# Fetch and display recent emails (read-only)
clawmail fetch
clawmail fetch --days 7 --limit 20 --all

# Classify and act on emails
clawmail process --dry-run          # preview without executing
clawmail process                    # interactive confirmation
clawmail process --yes              # skip confirmation
clawmail process --all              # include read emails
clawmail process --yes --quiet      # quiet automation mode
clawmail process --label "GitHub"   # process a specific label
clawmail process --compare haiku    # compare two models side-by-side
clawmail process --days 7 --limit 20

# View or edit triage rules
clawmail rules
clawmail rules --edit
```

## Configuration

Config lives at `~/.config/clawmail/config.yaml`. See [config.example.yaml](config.example.yaml) for the full format.
Processed message UIDs are tracked in `~/.config/clawmail/processed.txt` to avoid re-processing the same emails.

Rules define categories, each with an action:

| Action    | Effect                                      |
|-----------|---------------------------------------------|
| `none`    | Leave in place                              |
| `flag`    | Star the email                              |
| `move`    | Move to a target folder/label               |
| `trash`   | Move to Trash                               |
| `archive` | Remove from Inbox (stays in All Mail)       |

In `--dry-run` mode, Claude also suggests new categories you might want to add based on the emails it saw. Customize the `suggestions_prompt` in your config to steer these suggestions.

## Provider notes

### Gmail
- Target folders for `move` actions must already exist as Gmail labels — create them in Gmail's UI first.
- Gmail requires an App Password, not your regular password. Generate one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).

### iCloud
- Generate an app-specific password at [appleid.apple.com](https://appleid.apple.com/account/manage) → App-Specific Passwords.
- iCloud uses `Deleted Messages` as the trash folder and `Archive` for archiving.
- Target folders for `move` actions must already exist — create them in Mail.app or iCloud Mail on the web.
