import asyncio

from app import valuation


class FakeSweep:
    usage = {"serpapi": 0, "ebay": 0}


def offer(title, amount, used=False, currency="USD"):
    return {"title": title, "amount": amount, "currency": currency, "source": "test", "url": f"https://x/{amount}",
            "used": used, "retrieved_at": "2026-10-08T00:00:00+00:00"}


def test_median_listing_is_a_real_listing():
    offers = [offer("a", 30), offer("b", 10), offer("c", 20), offer("d", 40)]
    assert valuation.median_listing(offers)["amount"] == 20   # lower median, not 25


def test_matching_drops_unrelated_listings():
    offers = [offer("The Hobbit - Paperback", 10), offer("SparkNotes study guide: Dune", 5)]
    assert [o["title"] for o in valuation.matching(offers, "The Hobbit")] == ["The Hobbit - Paperback"]


def test_no_listings_means_no_price(monkeypatch):
    async def nothing(*args, **kwargs):
        return []
    monkeypatch.setattr(valuation.prices, "shopping_offers", nothing)
    monkeypatch.setattr(valuation.prices, "ebay_offers", nothing)
    book = {"isbn": "", "title": "Obscure Book", "author": "Nobody"}
    repl, used = asyncio.run(valuation.price_book(FakeSweep(), book, "US", "USD"))
    assert repl["amount"] is None and used["amount"] is None


def test_falls_back_to_other_market_and_labels_conversion(monkeypatch):
    async def shop(sweep, query, country):
        return [offer("The Hobbit", 10.0, currency="GBP")] if country == "GB" else []

    async def ebay(sweep, query, country, condition):
        return []

    async def fx(a, b):
        return {"rate": 1.25, "date": "2026-10-07", "url": "https://fx"}

    monkeypatch.setattr(valuation.prices, "shopping_offers", shop)
    monkeypatch.setattr(valuation.prices, "ebay_offers", ebay)
    monkeypatch.setattr(valuation.prices, "fx_rate", fx)
    repl, _ = asyncio.run(valuation.price_book(FakeSweep(), {"isbn": "", "title": "The Hobbit", "author": "Tolkien"},
                                               "US", "USD"))
    assert repl["amount"] == 12.5
    assert repl["converted"] is True
    assert "converted from GBP" in repl["source"]


def test_user_statement_sends_book_to_appraisal():
    statements = [{"kind": "first_edition", "about": "the hobbit", "quote": "that's a first edition"}]
    assert valuation.appraisal_reasons({"title": "The Hobbit"}, statements)
    assert not valuation.appraisal_reasons({"title": "Dune"}, statements)
