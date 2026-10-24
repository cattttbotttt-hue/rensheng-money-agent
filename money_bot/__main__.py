"""money-bot CLI.

  python -m money_bot run                 春→夏→秋 full pipeline (daily digest)
  python -m money_bot scout               春 fetch raw items only
  python -m money_bot score               夏 re-score existing raw items
  python -m money_bot digest [--all]      秋 send digest (--all resends seen items)
  python -m money_bot poll                秋 poll Telegram approvals
  python -m money_bot approve <id>        秋 approve an item locally (no Telegram)
  python -m money_bot advise <id>         秋 print AI advice only (no approve, no invoice)
  python -m money_bot paypal-dry-run [id] 秋 print the PayPal invoice body; no network
  python -m money_bot trio-test           春/夏/秋 send one check-in line each
  python -m money_bot enter <id>          秋 semi-auto pre-fill for an APPROVED item
  python -m money_bot verify <id>         秋 check Gmail for a verification link
"""

from __future__ import annotations

import argparse

from . import config


def main() -> None:
    config.load_dotenv()
    parser = argparse.ArgumentParser(prog="money_bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("scout")
    sub.add_parser("score")
    p_digest = sub.add_parser("digest")
    p_digest.add_argument("--all", action="store_true", help="resend already-seen items")
    p_digest.add_argument("--top", type=int, default=config.DIGEST_TOP_N)
    sub.add_parser("poll")
    sub.add_parser("trio-test")
    p_approve = sub.add_parser("approve")
    p_approve.add_argument("item_id")
    p_advise = sub.add_parser("advise")
    p_advise.add_argument("item_id")
    p_paypal = sub.add_parser("paypal-dry-run")
    p_paypal.add_argument("item_id", nargs="?", default="")
    p_enter = sub.add_parser("enter")
    p_enter.add_argument("item_id")
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("item_id")
    args = parser.parse_args()

    if args.cmd == "run":
        from . import executor

        executor.run_all()
    elif args.cmd == "scout":
        from . import scout

        scout.run()
    elif args.cmd == "score":
        from . import score

        score.run()
    elif args.cmd == "digest":
        from . import executor

        executor.digest(top_n=args.top, include_seen=args.all)
    elif args.cmd == "poll":
        from . import executor

        executor.poll()
    elif args.cmd == "trio-test":
        from . import executor

        executor.trio_test()
    elif args.cmd == "approve":
        from . import executor

        executor.approve(args.item_id)
    elif args.cmd == "advise":
        from . import executor

        if executor.advise_item(args.item_id) is None:
            raise SystemExit(1)
    elif args.cmd == "paypal-dry-run":
        import json

        from . import ledger, paypal

        item = None
        if args.item_id:
            state = ledger.load_state()
            item = state["items_cache"].get(args.item_id)
            if not item and config.SCORED_PATH.exists():
                scored = json.loads(config.SCORED_PATH.read_text(encoding="utf-8")).get("items", [])
                item = next((row for row in scored if row.get("id") == args.item_id), None)
            if not item:
                raise SystemExit(f"item not found: {args.item_id}")
        print(json.dumps(paypal.dry_run(item), ensure_ascii=False, indent=2))
    elif args.cmd == "enter":
        from . import enter

        enter.cli_enter(args.item_id)
    elif args.cmd == "verify":
        from . import gmail

        gmail.cli_verify(args.item_id)


if __name__ == "__main__":
    main()
