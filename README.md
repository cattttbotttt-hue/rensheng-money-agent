# Rensheng Money Agent

Approval-gated scout for low-effort public money opportunities. Python 3.10+, standard library only.

A judge can run the full scout → score → act pipeline with no accounts, no tokens, and no payments. The digest and the approval pack include an AI risk and effort summary. Without an API key that summary is an offline heuristic, and the log says the AI provider was skipped. The summary only advises. When Telegram is unset, the digest prints in the terminal. Approving an item writes a manual checklist. The bot never submits a form and never sends money. If PayPal sandbox credentials are present, that same approval also creates one sandbox invoice and records its id, status, and link. The bot never pays the invoice. Without those credentials, approval still writes the checklist and says PayPal was skipped.

## Pipeline

| Stage | Command piece | What it does |
|---|---|---|
| Scout | `money_bot/scout.py` | Reads public, no-login feeds in `sources.json` (RSS and Devpost) into `data/raw_items.json`. |
| Score | `money_bot/score.py` | Drops scams, spend-to-save deals, and offers closed to Macau. Labels how much personal data an item asks for. Writes `data/scored_items.json`. |
| Act | `money_bot/executor.py` | Sends one Telegram digest with Approve buttons, or prints that digest locally. Highlight rows include an AI assist line, or an offline heuristic when no model key is set. A human approval writes `notes/packs/<id>.md` with that advice. With PayPal sandbox credentials, it then creates one invoice and does not pay it. |

Daily notes land in `notes/YYYY-MM-DD.md`. Nothing is written outside this repository.

```
score = 100 × (0.55 × effort + 0.30 × value + 0.15 × urgency) − 60 × risk
```

- **Effort (55%)** favors sign-up, claim, giveaway, and airdrop wording, and penalizes deposits, minimum spend, and hackathon deliverables.
- **Value (30%)** reads the largest USD-like amount that is not a spend or deposit. A large prize can offset complexity.
- **Urgency (15%)** boosts deadlines within two days and drops items that are already past deadline.
- **Risk** drops anything that asks for a seed phrase or private key. It also penalizes “send fee first”, wallet-connect bait, guaranteed profit, gambling, DMs, and short links. `crypto-airdrop` starts with extra base risk. `signup-bonus` does not. Items at risk ≥ 0.75 or score < 25 are discarded.

## Macau geo hard-drop

The scorer assumes the operator lives in Macau and is not a US, UK, EU, Canada, or Australia resident.

An item is discarded with `地區不符 not open to Macau` when the title or summary limits residency or citizenship and does not mention Macau, Macao, or a worldwide / international / global audience. `promo` items (for example Doctor of Credit) are treated as US-default unless they say they are worldwide. `void where prohibited` alone is not a geo limit. `no purchase necessary` does not override a residency limit. Needing an existing Coinbase or Binance account does not, by itself, drop a referral bonus.

## KYC and data labels

Each kept item gets one data-effort label, shown in the digest:

| Label | Meaning | Score effect |
|---|---|---|
| `kyc` 〔要KYC〕 | KYC, passport, government ID, SSN, proof of address | 0 (label only; `DATA_EFFORT_PENALTY_KYC` defaults to 0) |
| `address` 〔要地址〕 | Shipping address or a required phone number | −10 |
| `basic_info` 〔要基本資料〕 | Name, date of birth, or “open an account” | 0 |
| `email_only` 〔只需Email〕 | Default | 0 |

The bot does not perform KYC. The label is there so a person can decide.

## Safety rules

- **No auto-pay.** Approval writes a checklist and, only when PayPal sandbox credentials are set, creates one invoice. It does not pay, capture, deposit, or check out. Card, CVV, and payout calls are never made.
- **No auto-approve from AI.** The model, or the offline heuristic, may recommend approve or skip. That text is shown and stored. The human gate stays. The recommendation does not approve, skip, submit, or pay.
- **No auto-submit.** The optional browser step can pre-fill a form and stop. The submit click waits for a separate Telegram confirm. Card, CVV, IBAN, and billing fields are skipped.
- **No passwords.** Password inputs are never typed and never stored. “Sign in with Google” is only clicked on the opportunity’s own domain, and only after the human confirms.
- **No seed phrases.** The bot never asks for a seed phrase, recovery phrase, or private key. Items that ask for one are dropped as scams. `PROFILE_WALLET_EVM` is a public address only.
- **No captcha bypass.** A captcha becomes a screenshot and a request for a human.
- **No live browser profile.** `MONEY_BOT_PROFILE_DIR` must be a copy. A system Chrome directory or a profile with `SingletonLock` is refused.
- **Secrets stay local.** Tokens, the PayPal secret, the OpenAI API key, the Gmail app password, and `PROFILE_*` values belong in `.env`, which is gitignored. Participation packs, screenshots, the ledger, and approval state are gitignored too.

## Setup

Requires Python 3.10 or newer. No pip install is required for scout, score, digest, poll, advise, or local approve.

```bash
python3 --version
cp .env.example .env
# Optional: replace the placeholder values. Leave them blank to use the terminal.
python3 -m unittest discover -s tests -t . -v
python3 -m money_bot run
```

Other commands:

```bash
python3 -m money_bot scout          # fetch only
python3 -m money_bot score          # re-score data/raw_items.json
python3 -m money_bot digest         # send or print the digest (--all resends seen items)
python3 -m money_bot poll           # read Telegram Approve presses
python3 -m money_bot approve <id>   # local approval, no Telegram
python3 -m money_bot advise <id>    # print AI advice; does not approve or invoice
python3 -m money_bot paypal-dry-run # print the invoice body; no network
python3 -m money_bot trio-test      # one check-in line per butler
```

`sources.json` is the feed list plus a `blocklist` of case-insensitive substrings. Add a source there; do not edit the fetcher unless you need a new `type`. Supported types are `rss` and `devpost`. Categories: `promo`, `signup-bonus`, `sweepstakes`, `giveaway`, `crypto-airdrop`, `hackathon`.

## Telegram approval gate

Recommended layout is three BotFather bots: spring (scout), summer (score), autumn (act). Put their tokens in `TELEGRAM_TOKEN_SPRING`, `TELEGRAM_TOKEN_SUMMER`, and `TELEGRAM_TOKEN_AUTUMN`. A butler with an empty token uses `TELEGRAM_BOT_TOKEN`. If every token is empty, the same report prints in the terminal.

`TELEGRAM_CHAT_ID` is the numeric `chat.id` from `https://api.telegram.org/bot<token>/getUpdates` after you message the bot. The daily message is one autumn note: date, how many items were kept, three one-line highlights, the path of the markdown note, and an Approve button per top item. The next `poll` turns that press into `notes/packs/<id>.md`. It does not submit the opportunity. If PayPal is configured, the same press also creates one sandbox invoice.

On your own machine, a cron can call `python3 -m money_bot run` once a day and `python3 -m money_bot poll` every 30 minutes. Set `MONEY_BOT_PUBLISH=1` only if that machine should `git pull --rebase` and push `notes/` (ignored files stay untracked). The bundled GitHub Action is manual (`workflow_dispatch`) and does not push.

## AI assist

The bot asks for a short risk and effort summary, plus an approve or skip recommendation, when it builds digest highlights and again when a human approves an item. The text is advice only. It does not approve, skip, submit a form, or pay. PayPal invoice creation still runs only after the human approval, and the bot still does not pay that invoice.

No extra packages. The call is an OpenAI-compatible [Chat Completions](https://platform.openai.com/docs/api-reference/chat) `POST` from `urllib` in `money_bot/assist.py`.

### When it runs

- `digest` attaches advice to the highlight rows: up to three items in the terminal or Telegram digest, and the top three in `notes/YYYY-MM-DD.md`. The terminal line says whether the provider was skipped.
- `approve` (Telegram ✅ or `python3 -m money_bot approve <id>`) asks again and stores the text in `notes/packs/<id>.md` under `AI assist`, in the approval state, and in `data/ledger.jsonl` as `ai_advice`. A recommendation of `skip` does not block that human approval.
- `python3 -m money_bot advise <id>` prints the advice and stops. It does not write a pack and does not create an invoice.

### Model

The default model is `gpt-4o-mini`. The default endpoint is `https://api.openai.com/v1/chat/completions`. Set `OPENAI_MODEL` and `OPENAI_BASE_URL` for another OpenAI-compatible host. The client only posts to a URL that ends in `/chat/completions`. The prompt contains the public scored fields (title, URL, score, effort, risk, data label, prize, deadline, reasons, and the item summary). It does not contain `.env` secrets, `PROFILE_*` values, or card data.

### No API key

Leave `OPENAI_API_KEY` blank or as `replace-me`. The bot skips the AI provider, says `AI provider skipped` in the terminal, the daily note, and the pack, and uses this offline heuristic:

- `skip` when the text mentions a seed phrase, private key, or upfront fee; when risk is at least 0.45; when the effort score is below 0.40 (higher effort means less work); or when the score is below 40
- `approve` otherwise

That heuristic is what the unit tests and the manual GitHub Action run. They do not call a live model.

### With an API key

Put the key in `.env` only:

```bash
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

If the call fails, or the reply is not usable JSON with `summary` and `recommendation` of `approve` or `skip`, the same offline heuristic is used and labeled as a fallback. Error text is redacted before it is stored. Do not commit the key.

## PayPal Sandbox invoice

Approving an item (Telegram ✅ on the autumn bot, or `python3 -m money_bot approve <id>`) can create one PayPal invoice so a demo has a real sandbox money trail. The invoice line is the opportunity title. The amount is the parsed prize (`prize_usd`, or a USD-like amount in the title or summary). When nothing was parsed, the amount is the sandbox placeholder **1.00 USD** (`PAYPAL_PLACEHOLDER_AMOUNT` overrides it). That number is not collected.

This uses the official [Invoicing REST API](https://developer.paypal.com/docs/api/invoicing/v2/) with the Python standard library: `POST /v2/invoicing/invoices`, then `POST /v2/invoicing/invoices/{id}/send` with `send_to_recipient` and `send_to_invoicer` both false. The send call does not email anyone. It moves the draft to a payable status so the response includes a link. The bot never calls PayPal Orders, Payments, Capture, or Payouts, and it never records a payment. The PayPal Agent Toolkit is an LLM tool wrapper and is not used here.

The invoice id, status, and link are appended to `data/ledger.jsonl` and to a `PayPal Sandbox invoice` section in `notes/packs/<id>.md`.

### Sandbox Client ID and Secret

1. Open the [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/applications/sandbox) and sign in. A personal PayPal account is enough. You do not need a live business account.
2. Under Apps & Credentials, choose **Sandbox** and create an app (or open the default one). Copy the **Sandbox** Client ID and Secret, not the live pair.
3. In this repository:

```bash
cp .env.example .env
```

Set these three keys:

```bash
PAYPAL_CLIENT_ID=your-sandbox-client-id
PAYPAL_CLIENT_SECRET=your-sandbox-secret
PAYPAL_MODE=sandbox
```

Leave the id and secret as `replace-me` to skip PayPal. `PAYPAL_MODE` defaults to `sandbox` when it is missing. The only value that leaves the sandbox is `live`, which is discouraged: it creates a real invoice and still does not pay it.

Optional: `PAYPAL_RECIPIENT_EMAIL` is the buyer address stored on the invoice. When it is blank, the invoice uses `sandbox-buyer@example.com`. Point it at a Sandbox Personal account if you want that account to be the recipient. Notifications stay off either way.

`.env` is gitignored. Do not commit the secret.

### Demo

Offline, with or without credentials (no PayPal call):

```bash
python3 -m unittest tests.test_paypal_offline -v
python3 -m money_bot paypal-dry-run
```

The dry run prints the invoice JSON. When the Client ID or Secret is missing, it says PayPal is skipped and that nothing was sent.

With sandbox credentials in `.env`, approve the bundled example item (parsed prize 50 USD):

```bash
mkdir -p data
python3 - << 'PY'
import json
from pathlib import Path
item = json.loads(Path("data/examples/scored_item.example.json").read_text())
Path("data/scored_items.json").write_text(
    json.dumps({"count": 1, "items": [item]}),
    encoding="utf-8",
)
PY
python3 -m money_bot approve example000001
```

The terminal line, `notes/packs/example000001.md`, and `data/ledger.jsonl` then include the invoice id, status, and link. Open the link while logged into the sandbox business account that owns the REST app. The line item title is `Example worldwide giveaway` and the amount is `50.00 USD`.

Without `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`, the same `approve` command still writes the participation pack and prints `PayPal skipped`. No request is sent to PayPal. Filling the credentials later and running `approve` again on that id creates the invoice once. A second successful approve does not create another one.

## Optional form fill and Gmail

```bash
pip install playwright && playwright install chromium
python3 -m money_bot enter <id>     # pre-fill an APPROVED item and screenshot it
python3 -m money_bot verify <id>    # read-only IMAP search for a confirmation mail
```

`verify` needs `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` (a Google app password, not the account password). It only reads the inbox. Mailbox headers and confirmation URLs are not written into the ledger. Without those two variables, `verify` does nothing.

## What is not in this repository

Runtime caches from a private operator (`data/raw_items.json`, `data/scored_items.json`, `data/state.json`, `data/ledger.jsonl`) are produced locally and gitignored. `data/examples/scored_item.example.json` is a synthetic fixture. Personal notes, journals, and filled `.env` files are not part of this package.

## License

MIT. See [LICENSE](LICENSE).
