#!/usr/bin/env python3
"""Read-only Discord status report for the scheduled Wonkies mint."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path


DB = Path("/home/hermes/dev/NFT/artbytes-engine/data/engine.db")


def eth(wei: str | None) -> str:
    if not wei:
        return "0"
    return f"{int(wei) / 10**18:.6f}".rstrip("0").rstrip(".")


def main() -> None:
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    if not DB.exists():
        print(f"⚠️ Wonkies mint — {now}\nEngine database missing.")
        return

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    mint = db.execute(
        "SELECT id, slug, status, price_wei, budget_wei FROM mints "
        "WHERE slug = 'wonkiescc0' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if mint is None:
        print(f"⚠️ Wonkies mint — {now}\nNo scheduled order found.")
        return

    tx = db.execute(
        "SELECT hash, status, value_wei, gas_wei FROM txs "
        "WHERE mint_id = ? AND kind = 'mint' ORDER BY id DESC LIMIT 1",
        (mint["id"],),
    ).fetchone()
    spent = db.execute(
        "SELECT COALESCE(SUM(CAST(amount_wei AS INTEGER)), 0) FROM budget_ledger "
        "WHERE mint_id = ? AND kind = 'spend'",
        (mint["id"],),
    ).fetchone()[0]

    lines = [
        f"🎯 **Wonkies mint — {now}**",
        f"Statut : **{mint['status']}**",
        f"Prix : {eth(mint['price_wei'])} ETH · budget max : {eth(mint['budget_wei'])} ETH",
        f"Dépensé : {eth(str(spent))} ETH",
    ]
    if tx and tx["hash"]:
        lines.append(f"Transaction : https://etherscan.io/tx/{tx['hash']}")
        lines.append(f"Confirmation : {tx['status']}")
    else:
        lines.append("Transaction : aucune pour l’instant")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
