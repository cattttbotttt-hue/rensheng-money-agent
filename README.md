# Rensheng Money Agent

Approval-gated scout for low-effort public money opportunities. Python 3.10+, standard library only.

A judge can run the full scout → score → act pipeline with no accounts, no tokens, and no payments. When Telegram is unset, the digest prints in the terminal. Approving an item writes a manual checklist. The bot never submits a form and never sends money.

## Pipeline

| Stage | Command piece | What it does |
|---|---|---|
| Scout | `money_bot/scout.py` | Reads public, no-login feeds in `sources.json` (RSS and Devpost) into `data/raw_items.json`. |
| Score | `money_bot/score.py` | Drops scams, spend-to-save deals, and offers closed to Macau. Labels how much personal data an item asks for. Writes `data/scored_items.json`. |
| Act | `money_bot/executor.py` | Sends one Telegram digest with Approve buttons, or prints that digest locally. A human approval writes `notes/packs/<id>.md`. |

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

- **No auto-pay.** Approval writes a checklist. It does not pay, deposit, or check out.
- **No auto-submit.** The optional browser step can pre-fill a form and stop. The submit click waits for a separate Telegram confirm. Card, CVV, IBAN, and billing fields are skipped.
- **No passwords.** Password inputs are never typed and never stored. “Sign in with Google” is only clicked on the opportunity’s own domain, and only after the human confirms.
- **No seed phrases.** The bot never asks for a seed phrase, recovery phrase, or private key. Items that ask for one are dropped as scams. `PROFILE_WALLET_EVM` is a public address only.
- **No captcha bypass.** A captcha becomes a screenshot and a request for a human.
- **No live browser profile.** `MONEY_BOT_PROFILE_DIR` must be a copy. A system Chrome directory or a profile with `SingletonLock` is refused.
- **Secrets stay local.** Tokens, the Gmail app password, and `PROFILE_*` values belong in `.env`, which is gitignored. Participation packs, screenshots, the ledger, and approval state are gitignored too.

## Setup

Requires Python 3.10 or newer. No pip install is required for scout, score, digest, poll, or local approve.

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
python3 -m money_bot trio-test      # one check-in line per butler
```

`sources.json` is the feed list plus a `blocklist` of case-insensitive substrings. Add a source there; do not edit the fetcher unless you need a new `type`. Supported types are `rss` and `devpost`. Categories: `promo`, `signup-bonus`, `sweepstakes`, `giveaway`, `crypto-airdrop`, `hackathon`.

## Telegram approval gate

Recommended layout is three BotFather bots: spring (scout), summer (score), autumn (act). Put their tokens in `TELEGRAM_TOKEN_SPRING`, `TELEGRAM_TOKEN_SUMMER`, and `TELEGRAM_TOKEN_AUTUMN`. A butler with an empty token uses `TELEGRAM_BOT_TOKEN`. If every token is empty, the same report prints in the terminal.

`TELEGRAM_CHAT_ID` is the numeric `chat.id` from `https://api.telegram.org/bot<token>/getUpdates` after you message the bot. The daily message is one autumn note: date, how many items were kept, three one-line highlights, the path of the markdown note, and an Approve button per top item. The next `poll` turns that press into `notes/packs/<id>.md` and does not submit anything.

On your own machine, a cron can call `python3 -m money_bot run` once a day and `python3 -m money_bot poll` every 30 minutes. Set `MONEY_BOT_PUBLISH=1` only if that machine should `git pull --rebase` and push `notes/` (ignored files stay untracked). The bundled GitHub Action is manual (`workflow_dispatch`) and does not push.

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
