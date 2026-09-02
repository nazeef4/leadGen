"""Targeting: geo browser, niche advisor, offer configuration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..schemas import GeoFilter, NicheRequest
from ..services.geo import get_geo_service
from ..services.niche_advisor import get_niche_advisor

router = APIRouter(prefix="/api/targeting", tags=["targeting"])


@router.get("/countries")
def countries() -> dict:
    geo = get_geo_service()
    return {"countries": geo.countries()}


@router.get("/countries/{code}/states")
def states(code: str) -> dict:
    geo = get_geo_service()
    record = geo.country(code)
    if not record:
        raise HTTPException(status_code=404, detail=f"Unknown country '{code}'")
    return {"country": record["name"], "code": record["code"], "states": geo.states(code)}


@router.get("/countries/{code}/states/{state_code}/cities")
def cities(code: str, state_code: str) -> dict:
    geo = get_geo_service()
    record = geo.country(code)
    if not record:
        raise HTTPException(status_code=404, detail=f"Unknown country '{code}'")
    return {"cities": geo.cities(code, state_code)}


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(25, ge=1, le=50)) -> dict:
    return {"results": get_geo_service().search(q, limit=limit)}


@router.post("/expand")
def expand(payload: GeoFilter) -> dict:
    """Resolve a selection (with '*' macros) into concrete target places."""
    geo = get_geo_service()
    places = geo.expand(payload.to_plain(), max_places=200)
    return {
        "count": len(places),
        "places": [p.to_dict() for p in places],
        "profile": geo.climate_profile(payload.to_plain()),
        "problems": geo.validate(payload.to_plain()),
        "summary": geo.describe(payload.to_plain()),
    }


@router.post("/niche-suggestions")
def niche_suggestions(payload: NicheRequest) -> dict:
    advisor = get_niche_advisor()
    result = advisor.suggest(
        payload.offering,
        payload.geoFilter.to_plain() if payload.geoFilter else None,
        top_n=payload.topN,
        use_llm=payload.useLlm,
    )
    result["adjacentNiches"] = advisor.adjacent_niches(payload.offering)
    return result


@router.get("/niche-suggestions")
def niche_suggestions_get(
    offering: str, top_n: int = Query(12, ge=1, le=40), use_llm: bool = False
) -> dict:
    advisor = get_niche_advisor()
    result = advisor.suggest(offering, None, top_n=top_n, use_llm=use_llm)
    result["adjacentNiches"] = advisor.adjacent_niches(offering)
    return result


@router.get("/niche-archetypes")
def archetypes() -> dict:
    from ..services.niche_advisor import ARCHETYPES

    return {
        "archetypes": [
            {
                "key": a.key,
                "label": a.label,
                "keywords": a.keywords[:8],
                "categories": a.categories[:6],
                "climateWanted": a.climate_wanted,
                "minSummerC": a.min_summer_c,
                "maxSummerC": a.max_summer_c,
                "densityMin": a.density_min,
                "seasonality": a.seasonality,
            }
            for a in ARCHETYPES
        ]
    }
