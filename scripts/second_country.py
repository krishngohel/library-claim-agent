"""Price the same 10 books in a second country, to show locale is a setting.

    python -m scripts.second_country <sweep_id> GB

Takes the first 10 identified books from claim_packet.json, prices them with the same
code path but a different country, and writes locale_comparison.json + .html.
"""

import asyncio
import json
import sys
from html import escape

from app import config, valuation
from app.sweep import Sweep


async def compare(sweep_id: str, country: str) -> list[dict]:
    sweep = Sweep.load_state(sweep_id)
    packet = json.loads((sweep.dir / "claim_packet.json").read_text(encoding="utf-8"))
    currency = config.LOCALES[country][0]
    books = [b for b in packet["books"] if b["status"] == "identified"][:10]
    rows = []
    for b in books:
        replacement, used = await valuation.price_book(sweep, b, country, currency)
        rows.append({"id": b["id"], "title": b["title"], "isbn": b["isbn"],
                     packet["sweep"]["country"]: {"replacement_cost": b["replacement_cost"], "used_value": b["used_value"]},
                     country: {"replacement_cost": replacement, "used_value": used}})
    return rows


def main():
    sweep_id, country = sys.argv[1], sys.argv[2].upper()
    rows = asyncio.run(compare(sweep_id, country))
    out = Sweep.load_state(sweep_id).dir
    (out / "locale_comparison.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    home = [k for k in rows[0] if k not in ("id", "title", "isbn", country)][0] if rows else ""

    def cell(p):
        if p["amount"] is None:
            return "none"
        conv = " (converted)" if p.get("converted") else ""
        return f'<a href="{escape(p["url"])}">{p["amount"]:.2f}</a>{conv}<br><small>{escape(p["source"])}</small>'

    body = "".join(f"<tr><td>{escape(r['title'])}</td><td>{cell(r[home]['replacement_cost'])}</td>"
                   f"<td>{cell(r[home]['used_value'])}</td><td>{cell(r[country]['replacement_cost'])}</td>"
                   f"<td>{cell(r[country]['used_value'])}</td></tr>" for r in rows)
    html = (f"<!doctype html><meta charset=utf-8><title>Locale comparison</title><table border=1 cellpadding=4>"
            f"<tr><th>Title</th><th>{home} new</th><th>{home} used</th><th>{country} new</th><th>{country} used</th></tr>"
            f"{body}</table>")
    (out / "locale_comparison.html").write_text(html, encoding="utf-8")
    print(f"wrote {out / 'locale_comparison.html'}")


if __name__ == "__main__":
    main()
