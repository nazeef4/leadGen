"""Geo dataset loading, '*' macro expansion and validation."""

from __future__ import annotations

from leadgen.services.geo import GeoService


def test_dataset_loads_countries_states_cities():
    geo = GeoService()
    countries = geo.countries()
    assert len(countries) >= 40
    us = geo.country("US")
    assert us["name"] == "United States"
    assert len(geo.states("US")) == 51  # 50 states + DC
    cities = geo.cities("US", "AZ")
    assert any(c["name"] == "Phoenix" for c in cities)


def test_city_attributes_drive_targeting():
    geo = GeoService()
    phoenix = geo.place("US", "AZ", "Phoenix")
    assert phoenix is not None
    assert phoenix.avg_summer_c >= 40
    assert "hot" in phoenix.climate and "arid" in phoenix.climate
    assert phoenix.pop_tier >= 3

    reykjavikless = geo.place("GB", "SCT", "Glasgow")
    assert reykjavikless.avg_summer_c < 25


def test_expand_all_macro_for_a_state():
    geo = GeoService()
    places = geo.expand({"selections": [{"country": "US", "state": "AZ", "city": "*"}]})
    assert {p.city for p in places} >= {"Phoenix", "Tucson", "Mesa"}
    assert all(p.country_code == "US" and p.state_code == "AZ" for p in places)


def test_expand_whole_country_and_dedupe():
    geo = GeoService()
    places = geo.expand(
        {"selections": [{"country": "AE", "state": "*", "city": "*"}, {"country": "AE", "state": "DU", "city": "Dubai"}]}
    )
    labels = [p.label for p in places]
    assert len(labels) == len(set(labels))
    assert any(p.city == "Dubai" for p in places)


def test_expand_global_is_capped():
    geo = GeoService()
    places = geo.expand({"selections": [{"country": "*", "state": "*", "city": "*"}]}, max_places=50)
    assert len(places) == 50


def test_extra_cities_escape_hatch():
    geo = GeoService()
    places = geo.expand({"selections": [], "extraCities": ["Reykjavik, Iceland"]})
    assert len(places) == 1
    assert places[0].city == "Reykjavik"
    assert places[0].country == "Iceland"


def test_search_finds_each_level():
    geo = GeoService()
    kinds = {r["type"] for r in geo.search("pho", limit=25)}
    assert "city" in kinds
    assert any(r["type"] == "country" for r in geo.search("united", limit=10))
    assert any(r["type"] == "state" for r in geo.search("bavaria", limit=10))


def test_validate_reports_unknown_selections():
    geo = GeoService()
    problems = GeoService.validate({"selections": [{"country": "XX", "state": "*", "city": "*"}]})
    assert any("Unknown country" in p for p in problems)
    assert GeoService.validate({"selections": [{"country": "US", "state": "AZ", "city": "Phoenix"}]}) == []
    assert GeoService.validate({"selections": []}) != []


def test_climate_profile_summarises_selection():
    geo = GeoService()
    profile = geo.climate_profile({"selections": [{"country": "US", "state": "AZ", "city": "*"}]})
    assert profile["count"] >= 5
    assert profile["avgSummerC"] > 30
    assert profile["hotShare"] > 0.5
