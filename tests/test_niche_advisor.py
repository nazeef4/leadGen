"""The niche advisor must map services onto the right geographies offline."""

from __future__ import annotations

import pytest

from leadgen.services.niche_advisor import NicheAdvisor


@pytest.fixture(scope="module")
def advisor():
    return NicheAdvisor()


def test_hvac_suggests_hot_regions(advisor):
    result = advisor.suggest("HVAC and AC repair services", use_llm=False, top_n=10)
    assert result["primaryArchetype"] == "hvac_cooling"
    cities = {s["city"] for s in result["suggestions"]}
    assert "Phoenix" in cities
    for item in result["suggestions"]:
        assert item["avgSummerC"] >= 30, f"{item['label']} is too cool for AC demand"
        assert item["fit"] in {"strong", "moderate"}


def test_snow_removal_suggests_cold_regions(advisor):
    result = advisor.suggest("snow removal and de-icing contracts", use_llm=False, top_n=8)
    assert result["primaryArchetype"] == "heating_cold"
    for item in result["suggestions"]:
        assert item["avgSummerC"] <= 27
        assert "cold" in item["climate"]


def test_managed_it_prefers_dense_metros(advisor):
    result = advisor.suggest("managed IT support and cybersecurity for SMBs", use_llm=False, top_n=6)
    assert result["primaryArchetype"] == "msp_it"
    tiers = [s["popTier"] for s in result["suggestions"]]
    assert max(tiers) >= 3
    # deterministic and not alphabetical-by-accident
    assert result["suggestions"] == sorted(
        result["suggestions"], key=lambda s: (-s["score"], -s["popTier"], s["label"])
    )


def test_unknown_service_falls_back_to_generic(advisor):
    result = advisor.suggest("underwater basket weaving", use_llm=False, top_n=3)
    assert result["primaryArchetype"] == "generic"
    assert result["suggestions"]


def test_constrained_to_user_selection(advisor):
    result = advisor.suggest(
        "HVAC repair",
        {"selections": [{"country": "US", "state": "AZ", "city": "*"}]},
        use_llm=False,
        top_n=5,
    )
    assert result["constrainedToSelection"] is True
    assert {s["state"] for s in result["suggestions"]} == {"Arizona"}


def test_reasons_and_queries_are_populated(advisor):
    result = advisor.suggest("pool cleaning service", use_llm=False, top_n=3)
    for item in result["suggestions"]:
        assert item["reasons"], "every suggestion needs an explanation"
        assert item["sampleQueries"], "the scraper needs query seeds"
        assert item["sampleQueries"][0]
    assert result["searchTerms"]
    assert result["targetCategories"]
    assert result["hooks"]
    assert result["strategy"]


def test_adjacent_niches_share_buyers(advisor):
    adjacent = advisor.adjacent_niches("HVAC repair")
    assert adjacent
    labels = {a["label"] for a in adjacent}
    assert "Roofing, storm & water damage" in labels or "Pest & mosquito control" in labels
    for item in adjacent:
        assert item["sharedBuyers"]


def test_llm_disabled_without_key(advisor):
    result = advisor.suggest("HVAC repair", use_llm=True, top_n=3)
    assert result["llm"]["used"] is False
    assert "reason" in result["llm"]
