"""Scraping: email extraction, scoring, CSV import, demo data, query planning."""

from __future__ import annotations

from leadgen.services.scrapers.base import ScrapedLead
from leadgen.services.scrapers.csv_import import CsvImportScraper, normalise_header
from leadgen.services.scrapers.demo import DemoScraper
from leadgen.services.scrapers.duckduckgo import unwrap_ddg
from leadgen.services.scrapers.enrich import (
    classify_email,
    extract_emails,
    extract_phone,
    is_plausible_email,
    score_lead,
)

PAGE = """
<html><head><title>Desert Air</title>
<script>var x = "tracker@sentry.io";</script></head>
<body>
<p>24/7 emergency AC repair. Call (602) 555-0134.</p>
<a href="mailto:maria@desertair.com">Email Maria</a>
<a href="mailto:info@desertair.com">General enquiries</a>
<img src="/logo.png"> contact: sales.desk@desertair.com
</body></html>
"""


def test_email_extraction_prefers_people_over_roles():
    found = extract_emails(PAGE)
    addresses = [c.address for c in found]
    assert "maria@desertair.com" in addresses
    assert "info@desertair.com" in addresses
    assert addresses[0] == "maria@desertair.com"
    assert "tracker@sentry.io" not in addresses, "script contents must be ignored"


def test_plausibility_filters_junk():
    assert is_plausible_email("owner@acme.com")
    assert not is_plausible_email("logo@site.png")
    assert not is_plausible_email("not-an-email")
    assert not is_plausible_email("a@b")
    assert not is_plausible_email("x@wixpress.com")
    assert not is_plausible_email("a..b@acme.com")


def test_email_classification():
    assert classify_email("maria.lopez@acme.com").kind == "personal"
    assert classify_email("info@acme.com").kind == "role"
    assert classify_email("owner@acme.com").kind == "role"
    assert classify_email("x1@acme.com").kind == "unknown"
    assert classify_email("maria.lopez@acme.com").score > classify_email("info@acme.com").score


def test_phone_extraction():
    assert "(602) 555-0134" in extract_phone(PAGE)
    assert extract_phone("<html>nothing here</html>") == ""


def test_scoring_rewards_personal_email_and_social_proof():
    weak = ScrapedLead(business_name="Acme")
    strong = ScrapedLead(
        business_name="Acme Roofing",
        email="owner@acmeroofing.com",
        website="https://acmeroofing.com",
        phone="+1 555 0100",
        city="Phoenix",
        rating=4.8,
        review_count=210,
        snippet="GAF certified roofing, insurance claims handled",
    )
    weak_score, _ = score_lead(weak, mx_ok=True)
    strong_score, reasons = score_lead(
        strong, mx_ok=True, buyer_signals=["insurance claims"], target_cities={"Phoenix"}
    )
    assert strong_score > weak_score + 40
    assert reasons["emailKind"] == "role" or reasons["emailKind"] == "personal"
    assert reasons.get("cityMatch") is True
    assert reasons.get("buyerSignals") == ["insurance claims"]


def test_failed_mx_check_lowers_score():
    lead = ScrapedLead(business_name="Acme", email="x@acme.com")
    good, _ = score_lead(lead, mx_ok=True)
    bad, _ = score_lead(lead, mx_ok=False)
    assert good > bad


def test_csv_import_maps_flexible_columns():
    csv_text = (
        "Company Name,E-Mail,Location,Industry,Notes\n"
        "Acme Roofing,OWNER@acme.com,Phoenix,Roofing,insurance claims\n"
        "Bad Row,not-an-email,Phoenix,Roofing,\n"
        "No Email,,Phoenix,Roofing,\n"
    )
    scraper = CsvImportScraper()
    leads = scraper.from_text(csv_text)
    assert len(leads) == 1
    assert leads[0].business_name == "Acme Roofing"
    assert leads[0].email == "owner@acme.com"
    assert leads[0].city == "Phoenix"
    assert leads[0].category == "Roofing"
    assert len(scraper.skipped) == 2


def test_normalise_header_aliases():
    assert normalise_header("Company Name") == "business_name"
    assert normalise_header("E-Mail") == "email"
    assert normalise_header("Phone Number") == "phone"
    assert normalise_header("Something Else") == "something_else"


def test_demo_scraper_is_deterministic_and_labelled():
    a = DemoScraper(city="Phoenix", state="AZ", country="United States")
    b = DemoScraper(city="Phoenix", state="AZ", country="United States")
    first = a.search("hvac company", limit=5)
    second = b.search("hvac company", limit=5)
    assert [x.business_name for x in first] == [x.business_name for x in second]
    assert all(x.source == "demo" for x in first)
    assert all(x.signals.get("synthetic") for x in first)
    assert all("example.com" in x.email for x in first)
    assert all(x.city == "Phoenix" for x in first)


def test_demo_scraper_matches_the_query_category():
    leads = DemoScraper(city="Tampa").search("commercial cleaning service", limit=4)
    assert leads
    assert all("clean" in (x.category + x.business_name).lower() for x in leads)


def test_dedupe_key_prefers_email():
    lead = ScrapedLead(business_name="Acme", email="A@B.com", website="https://acme.com")
    assert lead.dedupe_key() == "email:a@b.com"
    no_email = ScrapedLead(business_name="Acme", website="https://www.ACMEdirect.com")
    assert no_email.dedupe_key() == "site:acmedirect.com"


def test_ddg_redirect_unwrapping():
    wrapped = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Facme.com%2F&rut=abc"
    assert unwrap_ddg(wrapped) == "https://acme.com/"
    assert unwrap_ddg("//acme.com/x") == "https://acme.com/x"
    assert unwrap_ddg("") == ""


# Shaped like a real html.duckduckgo.com/html/ results page: redirect-wrapped
# links, title|separator suffixes, snippet anchors and directory noise mixed in.
DDG_HTML = """
<html><body>
<div class="results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title"><a rel="nofollow" class="result__a"
      href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdesertaircooling.com%2F&amp;rut=1">
      Desert Air Cooling - 24/7 AC Repair Phoenix</a></h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdesertaircooling.com%2F">
      NATE certified <b>HVAC</b> technicians serving Phoenix since 2009.</a>
  </div>
</div>
<div class="result">
  <h2 class="result__title"><a class="result__a"
    href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.yelp.com%2Fbiz%2Fsomething">Yelp listing</a></h2>
  <a class="result__snippet">directory noise that must be skipped</a>
</div>
<div class="result">
  <h2 class="result__title"><a class="result__a"
    href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fsummitroofaz.com%2F">Summit Roofing AZ</a></h2>
  <a class="result__snippet">Storm damage and <b>roofing</b> repairs across Arizona.</a>
</div>
</body></html>
"""


def test_ddg_result_parsing_offline():
    from leadgen.services.scrapers.duckduckgo import DuckDuckGoScraper

    leads = DuckDuckGoScraper().parse_results(DDG_HTML, limit=10)
    assert [x.business_name for x in leads] == ["Desert Air Cooling", "Summit Roofing AZ"]
    assert leads[0].website == "https://desertaircooling.com/"
    assert leads[0].category == "HVAC contractor"  # snippet mentions HVAC, which wins over "AC repair"
    assert leads[1].category == "Roofing contractor"
    assert "24/7 AC Repair Phoenix" not in leads[0].business_name, "title suffix must be stripped"
    assert all(x.source == "duckduckgo" for x in leads)
    assert all("yelp.com" not in x.website for x in leads), "directories must be filtered out"


def test_ddg_search_reports_failure_without_inventing_data():
    from leadgen.services.scrapers.duckduckgo import DuckDuckGoScraper

    scraper = DuckDuckGoScraper()
    scraper.fetch = lambda url, check_robots=True: None  # simulate no network
    assert scraper.search("anything", limit=5) == []
    assert "No response from DuckDuckGo" in scraper.last_error


def test_query_builder_uses_offering_and_geography():
    from leadgen.services.scrapers.pipeline import QueryBuilder

    plan = QueryBuilder().build(
        "HVAC repair",
        {"selections": [{"country": "US", "state": "AZ", "city": "*"}]},
        max_places=4,
        queries_per_place=2,
    )
    assert plan
    assert len(plan) <= 8
    for item in plan:
        assert "hvac" in item.query.lower() or "air" in item.query.lower()
        assert item.place_label
    assert any("Phoenix" in item.query for item in plan)
