"""Geolocation targeting service.

Loads the bundled country -> state -> city dataset and expands user selections
(including the "*" / All macro at any level) into concrete target places that
the niche advisor and the scraper consume.

Selection format used across the API and the DB::

    {
      "selections": [
        {"country": "US", "state": "AZ", "city": "Phoenix"},  # one city
        {"country": "US", "state": "AZ", "city": "*"},        # every city in AZ
        {"country": "US", "state": "*",  "city": "*"},        # whole country
        {"country": "*",  "state": "*",  "city": "*"}         # everywhere
      ],
      "extraCities": ["Reykjavik, Iceland"]                    # free-text escape hatch
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ALL = "*"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "geo"


@dataclass(slots=True)
class Place:
    country_code: str
    country: str
    state_code: str = ""
    state: str = ""
    city: str = ""
    climate: list[str] = field(default_factory=list)
    avg_summer_c: int = 20
    pop_tier: int = 1
    region: str = ""
    subregion: str = ""

    @property
    def label(self) -> str:
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ", ".join(parts)

    @property
    def granularity(self) -> str:
        if self.city:
            return "city"
        if self.state:
            return "state"
        return "country"

    def to_dict(self) -> dict:
        return {
            "countryCode": self.country_code,
            "country": self.country,
            "stateCode": self.state_code,
            "state": self.state,
            "city": self.city,
            "climate": self.climate,
            "avgSummerC": self.avg_summer_c,
            "popTier": self.pop_tier,
            "region": self.region,
            "subregion": self.subregion,
            "label": self.label,
            "granularity": self.granularity,
        }


class GeoService:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or DATA_DIR
        self._countries: dict[str, dict] = {}
        self._index: list[dict] = []
        self._loaded = False

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        if self._loaded:
            return
        index_file = self.data_dir / "_index.json"
        if index_file.exists():
            self._index = json.loads(index_file.read_text(encoding="utf-8")).get("countries", [])
        else:  # pragma: no cover - dataset ships with the app
            for path in sorted(self.data_dir.glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                self._index.append(
                    {
                        "code": record["code"],
                        "name": record["name"],
                        "region": record.get("region", ""),
                        "subregion": record.get("subregion", ""),
                        "stateCount": len(record.get("states", [])),
                        "cityCount": sum(len(s.get("cities", [])) for s in record.get("states", [])),
                    }
                )
        self._loaded = True

    def _country(self, code: str) -> dict | None:
        if code not in self._countries:
            path = self.data_dir / f"{code}.json"
            if not path.exists():
                return None
            self._countries[code] = json.loads(path.read_text(encoding="utf-8"))
        return self._countries[code]

    # ---------------------------------------------------------------- browse
    def countries(self) -> list[dict]:
        self.load()
        return [
            {
                "code": c["code"],
                "name": c["name"],
                "region": c.get("region", ""),
                "subregion": c.get("subregion", ""),
                "stateCount": c.get("stateCount", 0),
                "cityCount": c.get("cityCount", 0),
            }
            for c in self._index
        ]

    def country(self, code: str) -> dict | None:
        self.load()
        return self._country(code.upper())

    def states(self, country_code: str) -> list[dict]:
        record = self.country(country_code)
        if not record:
            return []
        return [
            {"code": s["code"], "name": s["name"], "cityCount": len(s.get("cities", []))}
            for s in record["states"]
        ]

    def cities(self, country_code: str, state_code: str) -> list[dict]:
        record = self.country(country_code)
        if not record:
            return []
        for s in record["states"]:
            if s["code"].upper() == state_code.upper():
                return [
                    {
                        "name": c["name"],
                        "climate": c["climate"],
                        "avgSummerC": c["avgSummerC"],
                        "popTier": c["popTier"],
                    }
                    for c in s["cities"]
                ]
        return []

    def search(self, query: str, limit: int = 25) -> list[dict]:
        """Fuzzy lookup used by the UI autocomplete."""
        self.load()
        q = query.strip().lower()
        if not q:
            return []
        results: list[dict] = []
        for entry in self._index:
            if q in entry["name"].lower():
                results.append({"type": "country", "country": entry["code"], "name": entry["name"]})
            if len(results) >= limit:
                return results[:limit]
            record = self._country(entry["code"])
            if not record:
                continue
            for state in record["states"]:
                if q in state["name"].lower():
                    results.append(
                        {
                            "type": "state",
                            "country": entry["code"],
                            "state": state["code"],
                            "name": f'{state["name"]}, {entry["name"]}',
                        }
                    )
                    if len(results) >= limit:
                        return results[:limit]
                    continue
                for city in state["cities"]:
                    if q in city["name"].lower():
                        results.append(
                            {
                                "type": "city",
                                "country": entry["code"],
                                "state": state["code"],
                                "name": f'{city["name"]}, {state["name"]}, {entry["name"]}',
                                "avgSummerC": city["avgSummerC"],
                                "climate": city["climate"],
                            }
                        )
                        if len(results) >= limit:
                            return results[:limit]
                        break
        return results[:limit]

    # -------------------------------------------------------------- expand
    def place(self, country: str, state: str = "", city: str = "") -> Place | None:
        """Resolve a single (possibly partial) selection into a Place."""
        record = self.country(country)
        if not record:
            return None
        place = Place(
            country_code=record["code"],
            country=record["name"],
            region=record.get("region", ""),
            subregion=record.get("subregion", ""),
        )
        if state and state != ALL:
            for s in record["states"]:
                if s["code"].upper() == state.upper() or s["name"].lower() == state.lower():
                    place.state_code, place.state = s["code"], s["name"]
                    break
            else:
                return None
            if city and city != ALL:
                for s in record["states"]:
                    if s["code"] != place.state_code:
                        continue
                    for c in s["cities"]:
                        if c["name"].lower() == city.lower():
                            place.city = c["name"]
                            place.climate = c["climate"]
                            place.avg_summer_c = c["avgSummerC"]
                            place.pop_tier = c["popTier"]
                            return place
                return None
            # whole state -> aggregate climate profile across its cities
            cities = [c for s in record["states"] if s["code"] == place.state_code for c in s["cities"]]
            self._aggregate(place, cities)
            return place
        if state == ALL and city and city != ALL:
            # city given without a state
            for s in record["states"]:
                for c in s["cities"]:
                    if c["name"].lower() == city.lower():
                        place.state_code, place.state = s["code"], s["name"]
                        place.city = c["name"]
                        place.climate = c["climate"]
                        place.avg_summer_c = c["avgSummerC"]
                        place.pop_tier = c["popTier"]
                        return place
            return None
        # whole country -> aggregate
        cities = [c for s in record["states"] for c in s["cities"]]
        self._aggregate(place, cities)
        return place

    @staticmethod
    def _aggregate(place: Place, cities: list[dict]) -> None:
        if not cities:
            return
        place.avg_summer_c = int(sum(c["avgSummerC"] for c in cities) / len(cities))
        place.pop_tier = max(c["popTier"] for c in cities)
        counts: dict[str, int] = {}
        for c in cities:
            for tag in c["climate"]:
                counts[tag] = counts.get(tag, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        place.climate = [tag for tag, _ in top]

    def expand(self, geo_filter: dict | None, max_places: int = 400) -> list[Place]:
        """Expand a selection list into concrete Places, honouring '*' macros."""
        geo_filter = geo_filter or {}
        selections = geo_filter.get("selections") or []
        if not selections and geo_filter.get("countries"):
            # tolerate the simplified shape {"countries": ["US"], "states": {...}}
            selections = [
                {"country": c, "state": ALL, "city": ALL} for c in geo_filter["countries"]
            ]
        places: list[Place] = []
        seen: set[str] = set()
        for sel in selections:
            country = (sel.get("country") or ALL).strip()
            state = (sel.get("state") or ALL).strip()
            city = (sel.get("city") or ALL).strip()
            country_codes = (
                [c["code"] for c in self.countries()]
                if country == ALL
                else [country.upper()]
            )
            for cc in country_codes:
                record = self.country(cc)
                if not record:
                    continue
                if state == ALL and city == ALL:
                    # expand a whole country into its cities so the scraper gets
                    # precise queries; fall back to the country itself if empty.
                    expanded = False
                    for s in record["states"]:
                        for c in s["cities"]:
                            key = f"{cc}:{s['code']}:{c['name']}"
                            if key in seen:
                                continue
                            seen.add(key)
                            expanded = True
                            places.append(
                                Place(
                                    country_code=cc,
                                    country=record["name"],
                                    state_code=s["code"],
                                    state=s["name"],
                                    city=c["name"],
                                    climate=c["climate"],
                                    avg_summer_c=c["avgSummerC"],
                                    pop_tier=c["popTier"],
                                    region=record.get("region", ""),
                                    subregion=record.get("subregion", ""),
                                )
                            )
                            if len(places) >= max_places:
                                return self._with_extras(places, geo_filter)
                    if not expanded:
                        p = self.place(cc, ALL, ALL)
                        if p and _place_key(p) not in seen:
                            seen.add(_place_key(p))
                            places.append(p)
                    continue
                state_codes = (
                    [s["code"] for s in record["states"]]
                    if state == ALL
                    else [state.upper()]
                )
                for sc in state_codes:
                    if city == ALL:
                        for c in self.cities(cc, sc):
                            key = f"{cc}:{sc}:{c['name']}"
                            if key in seen:
                                continue
                            seen.add(key)
                            places.append(
                                Place(
                                    country_code=cc,
                                    country=record["name"],
                                    state_code=sc,
                                    state=next(
                                        (s["name"] for s in record["states"] if s["code"] == sc), ""
                                    ),
                                    city=c["name"],
                                    climate=c["climate"],
                                    avg_summer_c=c["avgSummerC"],
                                    pop_tier=c["popTier"],
                                    region=record.get("region", ""),
                                    subregion=record.get("subregion", ""),
                                )
                            )
                            if len(places) >= max_places:
                                return self._with_extras(places, geo_filter)
                    else:
                        p = self.place(cc, sc, city)
                        if p and _place_key(p) not in seen:
                            seen.add(_place_key(p))
                            places.append(p)
        return self._with_extras(places, geo_filter)

    def _with_extras(self, places: list[Place], geo_filter: dict) -> list[Place]:
        for raw in geo_filter.get("extraCities") or []:
            name = str(raw).strip()
            if not name:
                continue
            parts = [p.strip() for p in name.split(",")]
            city = parts[0]
            country = parts[-1] if len(parts) > 1 else ""
            place = Place(
                country_code="", country=country, city=city, pop_tier=1, avg_summer_c=20
            )
            if place.label not in {p.label for p in places}:
                places.append(place)
        return places

    # ------------------------------------------------------------- matching
    @staticmethod
    def filter_places(places: list[Place], predicate) -> list[Place]:
        return [p for p in places if predicate(p)]

    def climate_profile(self, geo_filter: dict | None) -> dict:
        """Aggregate climate summary for the targeting panel."""
        places = self.expand(geo_filter, max_places=400)
        if not places:
            return {"count": 0, "avgSummerC": 0, "hotShare": 0.0, "climate": {}, "countries": []}
        counts: dict[str, int] = {}
        for p in places:
            for tag in p.climate:
                counts[tag] = counts.get(tag, 0) + 1
        avg = sum(p.avg_summer_c for p in places) / len(places)
        hot = sum(1 for p in places if p.avg_summer_c >= 30)
        return {
            "count": len(places),
            "avgSummerC": round(avg, 1),
            "hotShare": round(hot / len(places), 3),
            "climate": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:6]),
            "countries": sorted({p.country for p in places}),
        }

    @staticmethod
    def validate(geo_filter: dict | None) -> list[str]:
        """Return human readable problems with a selection (empty list == ok)."""
        problems: list[str] = []
        geo_filter = geo_filter or {}
        selections = geo_filter.get("selections") or []
        if not selections and not geo_filter.get("countries"):
            problems.append("No target geography selected.")
            return problems
        svc = _service()
        for sel in selections:
            country = (sel.get("country") or "").strip()
            if not country:
                problems.append("A selection is missing a country.")
                continue
            if country == ALL:
                continue
            if not svc.country(country):
                problems.append(f"Unknown country code: {country}")
                continue
            state = (sel.get("state") or ALL).strip()
            city = (sel.get("city") or ALL).strip()
            if state not in ("", ALL) and not any(
                s["code"].upper() == state.upper() for s in svc.states(country)
            ):
                problems.append(f"Unknown state '{state}' for {country}.")
            if city not in ("", ALL) and not svc.place(country, state or ALL, city):
                problems.append(f"Unknown city '{city}' for {country}.")
        return problems

    def describe(self, geo_filter: dict | None, max_items: int = 6) -> str:
        """One line summary such as 'Phoenix, Mesa (Arizona, United States) +2 more'."""
        places = self.expand(geo_filter, max_places=64)
        if not places:
            return "No geography selected"
        labels = [p.label for p in places[:max_items]]
        suffix = f" +{len(places) - max_items} more" if len(places) > max_items else ""
        return "; ".join(labels) + suffix


def _place_key(place: Place) -> str:
    """Single dedupe namespace shared by every expansion path."""
    return f"{place.country_code}:{place.state_code}:{place.city or place.state or ''}"


def normalise_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


@lru_cache(maxsize=1)
def _service() -> GeoService:
    return GeoService()


def get_geo_service() -> GeoService:
    return _service()
