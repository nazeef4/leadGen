"""AI-driven niche & demographic recommendation layer.

Two cooperating stages:

1. **Deterministic knowledge base** (always available, no network) — a library of
   service archetypes that know which climates, densities and buyer personas suit
   them.  "HVAC / AC repair" maps onto hot, high-summer-temperature metros; "snow
   removal" maps onto cold continental regions; "MSP / managed IT" maps onto
   dense business districts, and so on.

2. **Optional LLM enrichment** — when an API key is configured the model refines
   hooks, categories and adjacent niches.  Its output is *merged* into the rule
   based result, never used as the sole source, so a model outage cannot break
   targeting.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field

from .geo import GeoService, Place, get_geo_service
from .llm import LLMError, get_llm

log = logging.getLogger("leadgen.niche")


@dataclass
class Archetype:
    key: str
    label: str
    keywords: list[str]
    search_terms: list[str]
    categories: list[str]
    hooks: list[str]
    personas: list[str]
    seasonality: str = "year round"
    offer_angles: list[str] = field(default_factory=list)
    climate_wanted: list[str] = field(default_factory=list)
    climate_avoid: list[str] = field(default_factory=list)
    min_summer_c: int | None = None
    max_summer_c: int | None = None
    density_min: int = 1
    buyer_signals: list[str] = field(default_factory=list)
    buyer_tags: list[str] = field(default_factory=list)
    region_bias: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


ARCHETYPES: list[Archetype] = [
    Archetype(
        key="hvac_cooling",
        buyer_tags=['contractor', 'property_manager', 'facilities', 'restaurant', 'retail', 'warehouse'],
        label="HVAC / air-conditioning & cooling",
        keywords=["hvac", "ac repair", "air conditioning", "aircon", "cooling", "refrigeration",
                  "duct", "chiller", "heat pump", "mini split", "furnace", "heating"],
        search_terms=["hvac company", "air conditioning repair", "heating and cooling contractor",
                      "commercial hvac service", "ac installation"],
        categories=["HVAC contractor", "Air conditioning repair service", "Heating & cooling company",
                    "Commercial refrigeration service", "Mechanical contractor"],
        hooks=["summer demand spikes blow past crew capacity",
               "commercial clients lose revenue for every hour the unit is down",
               "maintenance contracts smooth out the winter revenue dip",
               "booking forms that leak after-hours emergencies to competitors"],
        personas=["owner-operator HVAC contractors", "commercial mechanical contractors",
                  "property management firms with rooftop units", "refrigeration & cold-chain operators"],
        seasonality="peak Mar-Sep (northern hemisphere) / Oct-Mar (southern hemisphere)",
        offer_angles=["emergency dispatch lead guarantee", "seasonal maintenance contract upsell",
                      "commercial SLA response times"],
        climate_wanted=["hot", "arid", "desert", "tropical", "humid"],
        min_summer_c=30,
        density_min=1,
        buyer_signals=["24/7 emergency", "epa certified", "carrier dealer", "servicing since"],
    ),
    Archetype(
        key="solar",
        buyer_tags=['contractor', 'facilities', 'warehouse', 'manufacturing', 'retail'],
        label="Solar & energy storage",
        keywords=["solar", "photovoltaic", "pv", "battery storage", "renewable", "energy storage",
                  "panel installation"],
        search_terms=["solar installation company", "solar panel installer", "commercial solar contractor",
                      "solar maintenance service"],
        categories=["Solar energy company", "Solar panel installer", "Renewable energy contractor",
                    "Electrical contractor"],
        hooks=["rising tariffs are pushing commercial tenants to ask about solar",
               "roof-space assessment bottleneck", "installer backlog after incentive deadlines"],
        personas=["commercial solar installers", "electrical contractors adding PV",
                  "facility managers of warehouses & retail parks"],
        seasonality="peak Apr-Oct, incentive-deadline driven",
        offer_angles=["free roof suitability report", "PPA / lease modelling"],
        climate_wanted=["hot", "arid", "mediterranean", "desert"],
        min_summer_c=28,
        buyer_signals=["nabcep", "tier 1 panels", "kwh", "net metering"],
    ),
    Archetype(
        key="heating_cold",
        buyer_tags=['contractor', 'property_manager', 'facilities'],
        region_bias=['North America', 'Northern Europe', 'Eastern Europe'],
        label="Heating, snow & cold-climate services",
        keywords=["snow removal", "snow plow", "de-icing", "boiler", "furnace repair", "heating repair",
                  "ice melt", "winter maintenance", "insulation", "heat loss"],
        search_terms=["snow removal service", "commercial snow plowing", "boiler repair",
                      "furnace installation", "insulation contractor"],
        categories=["Snow removal service", "Heating contractor", "Insulation contractor",
                    "Property maintenance company"],
        hooks=["first storm of the season decides who keeps the contract",
               "liability exposure from untreated walkways", "energy bills squeezing landlords"],
        personas=["commercial landscaping firms", "property managers", "facilities directors",
                  "heating contractors"],
        seasonality="Oct-Mar (northern hemisphere)",
        offer_angles=["per-event vs seasonal pricing", "24h storm response SLA"],
        climate_wanted=["cold", "continental"],
        climate_avoid=["tropical", "desert"],
        max_summer_c=27,
        buyer_signals=["per storm", "seasonal contract", "salting", "load calculation"],
    ),
    Archetype(
        key="roofing_storm",
        buyer_tags=['contractor', 'property_manager', 'facilities', 'retail'],
        label="Roofing, storm & water damage",
        keywords=["roofing", "roof repair", "gutter", "water damage", "flood restoration", "storm damage",
                  "waterproofing", "siding"],
        search_terms=["roofing company", "roof repair contractor", "water damage restoration",
                      "gutter installation", "storm damage repair"],
        categories=["Roofing contractor", "Water damage restoration service", "Gutter service",
                    "Building restoration company"],
        hooks=["storm seasons create 3-week booking windows", "insurance claim paperwork buries crews",
               "inspection photos that speed up adjuster approval"],
        personas=["roofing contractors", "restoration franchises", "insurance adjusters",
                  "property management companies"],
        seasonality="storm & hurricane season driven",
        offer_angles=["free drone roof inspection", "insurance-ready documentation pack"],
        climate_wanted=["coastal", "monsoon", "humid", "tropical"],
        density_min=1,
        buyer_signals=["gaf certified", "insurance claims", "storm response", "licensed & bonded"],
    ),
    Archetype(
        key="pool_outdoor",
        buyer_tags=['property_manager', 'hospitality', 'retail'],
        region_bias=['North America', 'Australasia', 'Southern Europe'],
        label="Pools, landscaping & outdoor living",
        keywords=["pool", "landscaping", "lawn", "irrigation", "sprinkler", "garden", "lawn care",
                  "hardscape", "patio", "outdoor"],
        search_terms=["pool service company", "pool cleaning", "landscaping company", "lawn care service",
                      "irrigation contractor"],
        categories=["Pool cleaning service", "Landscaping company", "Lawn care service",
                    "Irrigation contractor"],
        hooks=["route density is the whole margin game", "seasonal churn loses weekly routes",
               "chemical compliance record-keeping"],
        personas=["pool route operators", "landscape maintenance firms", "HOA managers"],
        seasonality="Mar-Oct (northern hemisphere)",
        offer_angles=["weekly route optimisation", "HOA & multi-site contracts"],
        climate_wanted=["hot", "coastal", "mediterranean", "tropical", "humid"],
        min_summer_c=26,
        buyer_signals=["weekly service", "route", "hoa", "chlorine"],
    ),
    Archetype(
        key="pest",
        buyer_tags=['restaurant', 'hotel', 'warehouse', 'property_manager', 'facilities'],
        label="Pest & mosquito control",
        keywords=["pest control", "exterminator", "termite", "mosquito", "rodent", "fumigation",
                  "wildlife removal"],
        search_terms=["pest control company", "termite treatment", "mosquito control service",
                      "commercial pest control"],
        categories=["Pest control service", "Termite control company", "Mosquito control service"],
        hooks=["food-service audits fail on a single sighting", "humidity seasons double call volume",
               "quarterly commercial contracts are the profit engine"],
        personas=["pest control operators", "restaurant & hotel groups", "food manufacturers",
                  "property managers"],
        seasonality="spring-summer surge in warm humid climates",
        offer_angles=["audit-ready reporting", "quarterly commercial service plans"],
        climate_wanted=["tropical", "humid", "hot", "monsoon"],
        min_summer_c=27,
        buyer_signals=["npma", "state licensed", "quarterly service", "ipm"],
    ),
    Archetype(
        key="msp_it",
        buyer_tags=['legal', 'accounting', 'clinic', 'dental', 'manufacturing', 'logistics', 'office'],
        region_bias=['North America', 'Western Europe', 'Northern Europe', 'Southern Europe', 'Eastern Europe', 'Australasia'],
        label="Managed IT / MSP & cybersecurity",
        keywords=["managed it", "msp", "it support", "cybersecurity", "network", "helpdesk",
                  "cloud migration", "it services", "voip", "cctv", "it consulting"],
        search_terms=["managed it services", "it support company", "cybersecurity consultant",
                      "network support provider", "cloud migration consultant"],
        categories=["IT support service", "Managed service provider", "Cybersecurity consultant",
                    "Computer repair service"],
        hooks=["compliance deadlines (HIPAA, SOC2, DORA) landing on small firms",
               "ransomware insurance now demands documented controls",
               "break-fix shops losing margins to subscription models"],
        personas=["law firms", "medical & dental practices", "accounting firms", "manufacturers",
                  "logistics companies"],
        seasonality="budget cycles: Jan-Feb and Jul-Aug",
        offer_angles=["free security posture assessment", "compliance gap report"],
        climate_avoid=[],
        density_min=2,
        buyer_signals=["microsoft partner", "soc 2", "hipaa", "response time"],
    ),
    Archetype(
        key="marketing",
        buyer_tags=['legal', 'clinic', 'restaurant', 'ecommerce', 'retail', 'contractor', 'startup'],
        region_bias=['North America', 'Western Europe', 'Northern Europe', 'Southern Europe', 'Australasia', 'Eastern Europe'],
        keywords=["marketing", "seo", "ppc", "ads", "branding", "web design", "web development",
                  "website", "social media", "lead generation", "content"],
        label="Marketing, web & creative agencies",
        search_terms=["digital marketing agency", "web design company", "seo agency",
                      "social media agency", "web development firm"],
        categories=["Marketing agency", "Web design company", "SEO agency", "Advertising agency"],
        hooks=["client acquisition costs rising while retainers flatten",
               "churn after the first 90 days kills LTV", "reporting that proves ROI"],
        personas=["professional services firms", "franchises expanding into new territories",
                  "e-commerce brands", "clinics and med spas"],
        seasonality="Q1 planning and Q4 budget spend",
        offer_angles=["free teardown of the current site/ads", "90-day ROI pilot"],
        density_min=2,
        buyer_signals=["google partner", "case studies", "retainer", "hubspot"],
    ),
    Archetype(
        key="commercial_cleaning",
        buyer_tags=['office', 'property_manager', 'facilities', 'retail', 'clinic'],
        keywords=["cleaning", "janitorial", "commercial cleaning", "carpet", "window cleaning",
                  "pressure washing", "post construction"],
        label="Commercial cleaning & facilities",
        search_terms=["commercial cleaning company", "janitorial service", "office cleaning",
                      "pressure washing service", "window cleaning"],
        categories=["Commercial cleaning service", "Janitorial service", "Pressure washing service",
                    "Window cleaning service"],
        hooks=["staff turnover makes consistent delivery hard", "walk-through failures cost contracts",
               "bidding on multi-site tenders without the paperwork muscle"],
        personas=["office managers", "property managers", "retail chains", "medical offices",
                  "post-construction GCs"],
        seasonality="year round; spikes after construction booms",
        offer_angles=["free walk-through & quote", "green/eco certification"],
        density_min=2,
        buyer_signals=["bonded and insured", "green certified", "nightly service", "osha"],
    ),
    Archetype(
        key="accounting",
        buyer_tags=['ecommerce', 'contractor', 'startup', 'restaurant', 'retail'],
        region_bias=['North America', 'Western Europe', 'Northern Europe', 'Australasia', 'Southern Europe'],
        keywords=["accounting", "bookkeeping", "tax", "payroll", "cfo", "audit", "finance"],
        label="Accounting, bookkeeping & finance",
        search_terms=["accounting firm", "bookkeeping service", "tax preparation", "payroll service",
                      "fractional cfo"],
        categories=["Accounting firm", "Bookkeeping service", "Tax preparation service", "Payroll service"],
        hooks=["tax deadlines create a 6-week crunch every year",
               "advisory revenue requires cleaner books than clients keep",
               "software migration work nobody wants to bill hourly"],
        personas=["growing SMBs", "e-commerce sellers", "contractor businesses", "startups"],
        seasonality="Jan-Apr tax season, Q4 planning",
        offer_angles=["free books health-check", "monthly close SLA"],
        density_min=1,
        buyer_signals=["cpa", "quickbooks proadvisor", "xero partner", "acca"],
    ),
    Archetype(
        key="construction",
        buyer_tags=['contractor', 'construction', 'property_manager', 'facilities'],
        keywords=["construction", "general contractor", "plumbing", "electrical", "remodel",
                  "renovation", "concrete", "painting", "handyman", "flooring"],
        label="Construction, trades & home services",
        search_terms=["general contractor", "plumbing company", "electrical contractor",
                      "remodeling contractor", "commercial painting"],
        categories=["General contractor", "Plumbing service", "Electrical contractor",
                    "Remodeling contractor"],
        hooks=["bid win-rate collapses without fast follow-up", "change-order disputes eat margin",
               "labour shortage makes schedule reliability the differentiator"],
        personas=["commercial GCs", "property developers", "facility managers", "franchise operators"],
        seasonality="building season; spring-summer peaks",
        offer_angles=["free bid-timeline review", "subcontractor network"],
        climate_wanted=[],
        density_min=1,
        buyer_signals=["licensed", "bonded", "design build", "osha 30"],
    ),
    Archetype(
        key="hospitality",
        buyer_tags=['restaurant', 'hotel', 'hospitality'],
        keywords=["restaurant", "cafe", "hospitality", "hotel", "catering", "food", "kitchen",
                  "coffee", "bar", "food truck"],
        label="Hospitality & food service",
        search_terms=["restaurant", "catering company", "hotel", "cafe", "commercial kitchen supplier"],
        categories=["Restaurant", "Catering service", "Hotel", "Cafe", "Food supplier"],
        hooks=["thin margins punish every wasted seat and every no-show",
               "review velocity drives discovery more than ads",
               "staffing gaps on weekends"],
        personas=["independent restaurants", "hotel groups", "caterers", "coffee chains"],
        seasonality="tourist season and holidays",
        offer_angles=["free mystery-shopper report", "reservation no-show reduction"],
        climate_wanted=["coastal", "tropical", "mediterranean"],
        density_min=2,
        buyer_signals=["tripadvisor", "opentable", "chef", "seasonal menu"],
    ),
    Archetype(
        key="real_estate",
        buyer_tags=['property_manager', 'contractor', 'construction'],
        keywords=["real estate", "realtor", "property management", "realty", "letting", "property",
                  "staging", "mortgage"],
        label="Real estate & property services",
        search_terms=["real estate agency", "property management company", "real estate photography",
                      "home staging", "letting agent"],
        categories=["Real estate agency", "Property management company", "Real estate photographer",
                    "Home staging service"],
        hooks=["listing inventory swings monthly", "buyer leads go cold in 5 minutes",
               "portfolio landlords demand better reporting"],
        personas=["brokerages", "property managers", "developers", "investor clients"],
        seasonality="spring-summer listing season",
        offer_angles=["free listing conversion audit", "lead-speed benchmark"],
        density_min=2,
        buyer_signals=["mls", "zillow", "lettings", "portfolio"],
    ),
    Archetype(
        key="health_wellness",
        buyer_tags=['clinic', 'dental', 'vet', 'gym'],
        keywords=["medical", "clinic", "dental", "health", "wellness", "physiotherapy", "chiropractic",
                  "veterinary", "med spa", "therapy", "pharmacy"],
        label="Health, wellness & veterinary",
        search_terms=["medical clinic", "dental practice", "physiotherapy clinic", "veterinary clinic",
                      "med spa"],
        categories=["Medical clinic", "Dental practice", "Physiotherapy clinic", "Veterinary clinic"],
        hooks=["no-shows quietly destroy clinic utilisation",
               "patient acquisition cost per new booking keeps climbing",
               "front-desk phone volume caps growth"],
        personas=["private practices", "multi-location clinics", "veterinary groups", "med spas"],
        seasonality="year round; new-year and September intake surges",
        offer_angles=["no-show reduction pilot", "free front-desk call audit"],
        density_min=1,
        buyer_signals=["hipaa", "booking online", "insurance accepted", "board certified"],
    ),
    Archetype(
        key="logistics",
        buyer_tags=['logistics', 'warehouse', 'ecommerce', 'manufacturing'],
        keywords=["logistics", "freight", "shipping", "warehouse", "distribution", "trucking",
                  "courier", "supply chain", "fulfillment", "import", "export"],
        label="Logistics, freight & supply chain",
        search_terms=["logistics company", "freight forwarder", "warehousing service", "trucking company",
                      "fulfillment center"],
        categories=["Logistics company", "Freight forwarder", "Warehousing service", "Courier service"],
        hooks=["lane imbalances and empty miles", "customs delays erode shipper trust",
               "visibility requests from big shippers they cannot meet"],
        personas=["regional carriers", "3PLs", "importers", "e-commerce brands"],
        seasonality="Q4 peak, post-holiday trough",
        offer_angles=["free lane profitability analysis", "visibility dashboard trial"],
        climate_wanted=["coastal"],
        density_min=2,
        buyer_signals=["customs broker", "ftl", "ltl", "bonded warehouse"],
    ),
    Archetype(
        key="automotive",
        buyer_tags=['auto', 'contractor'],
        keywords=["auto", "automotive", "car", "vehicle", "detailing", "mechanic", "tyre", "tire",
                  "fleet", "motor"],
        label="Automotive & fleet services",
        search_terms=["auto repair shop", "car detailing", "fleet maintenance", "tyre service",
                      "auto body shop"],
        categories=["Auto repair shop", "Car detailing service", "Fleet maintenance company",
                    "Auto body shop"],
        hooks=["bay utilisation and parts wait time", "fleet contracts need uptime reporting",
               "review-driven local discovery"],
        personas=["independent garages", "detailing operators", "fleet managers", "dealerships"],
        seasonality="seasonal tyre & service peaks",
        offer_angles=["free bay utilisation review", "fleet uptime reporting"],
        density_min=1,
        buyer_signals=["ase certified", "oem parts", "fleet", "loaner car"],
    ),
    Archetype(
        key="staffing",
        buyer_tags=['manufacturing', 'clinic', 'hospitality', 'restaurant', 'logistics'],
        region_bias=['North America', 'Western Europe', 'Northern Europe', 'Australasia'],
        keywords=["recruiting", "recruitment", "staffing", "hiring", "hr", "headhunting", "talent"],
        label="Recruiting, staffing & HR",
        search_terms=["recruitment agency", "staffing agency", "hr consulting", "executive search"],
        categories=["Recruitment agency", "Staffing agency", "HR consulting firm"],
        hooks=["time-to-fill is the metric clients actually watch",
               "candidate ghosting after offer", "compliance-heavy placements"],
        personas=["manufacturers", "healthcare systems", "tech scale-ups", "hospitality groups"],
        seasonality="Jan and Sep hiring waves",
        offer_angles=["free time-to-fill benchmark", "guarantee period extension"],
        density_min=2,
        buyer_signals=["contingency", "retained search", "temp to perm"],
    ),
    Archetype(
        key="education",
        buyer_tags=['school', 'office'],
        keywords=["school", "education", "tutoring", "training", "daycare", "childcare", "university",
                  "coaching", "academy"],
        label="Education, training & childcare",
        search_terms=["training company", "tutoring center", "daycare", "corporate training provider",
                      "private school"],
        categories=["Training company", "Tutoring center", "Childcare center", "Corporate trainer"],
        hooks=["enrolment cycles decide the year", "parent/student retention is cheaper than acquisition",
               "accreditation paperwork"],
        personas=["private schools", "training providers", "childcare groups", "corporate L&D"],
        seasonality="Aug-Sep and Jan intakes",
        offer_angles=["free enrolment funnel review", "retention workshop"],
        density_min=2,
        buyer_signals=["accredited", "curriculum", "enrolment", "certification"],
    ),
]

GENERIC = Archetype(
    key="generic",
    label="General B2B services",
    keywords=[],
    search_terms=["business services", "company"],
    categories=["local business", "service company"],
    hooks=["slow follow-up loses deals", "no predictable pipeline", "relying on referrals alone"],
    personas=["owner-operators", "operations managers", "founders"],
    seasonality="year round",
    offer_angles=["free consultation", "no-obligation audit"],
    density_min=1,
)

_WORD = re.compile(r"[a-z0-9+/#]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


class NicheAdvisor:
    def __init__(self, geo: GeoService | None = None):
        self.geo = geo or get_geo_service()

    # ------------------------------------------------------------- matching
    def match_archetypes(self, offering: str, limit: int = 3) -> list[tuple[Archetype, float]]:
        tokens = _tokens(offering)
        if not tokens:
            return [(GENERIC, 0.0)]
        text = offering.lower()
        scored: list[tuple[Archetype, float]] = []
        for arch in ARCHETYPES:
            score = 0.0
            for kw in arch.keywords:
                if " " in kw:
                    if kw in text:
                        score += 3.0
                elif kw in tokens:
                    score += 2.0
                elif any(t.startswith(kw) or kw.startswith(t) for t in tokens if len(t) > 3):
                    score += 0.8
            if score:
                scored.append((arch, score))
        scored.sort(key=lambda kv: (-kv[1], kv[0].label))
        if not scored:
            return [(GENERIC, 0.0)]
        return scored[:limit]

    # --------------------------------------------------------------- scoring
    @staticmethod
    def score_place(place: Place, arch: Archetype) -> tuple[float, list[str]]:
        score = 30.0
        reasons: list[str] = []
        if arch.climate_wanted:
            overlap = [t for t in arch.climate_wanted if t in place.climate]
            if overlap:
                score += min(len(overlap), 3) * 12
                reasons.append(f"climate profile ({', '.join(sorted(set(place.climate))[:3])})")
            else:
                score -= 12
        if arch.climate_avoid:
            clash = [t for t in arch.climate_avoid if t in place.climate]
            if clash:
                score -= 20
                reasons.append(f"climate mismatch ({', '.join(clash)})")
        if arch.min_summer_c is not None:
            delta = place.avg_summer_c - arch.min_summer_c
            if delta >= 0:
                score += min(delta * 1.8, 34)
                reasons.append(f"avg {place.avg_summer_c}°C summer (+{delta}°C vs the {arch.min_summer_c}°C threshold)")
            else:
                score += max(delta * 2.0, -24)
                reasons.append(f"only {place.avg_summer_c}°C avg summer")
        if arch.max_summer_c is not None:
            delta = arch.max_summer_c - place.avg_summer_c
            if delta >= 0:
                score += min(delta * 1.8, 34)
                reasons.append(f"avg {place.avg_summer_c}°C summer (cold-climate demand)")
            else:
                score -= 18
                reasons.append("summers too warm for cold-climate demand")
        if place.pop_tier >= arch.density_min:
            score += 8 + (place.pop_tier - arch.density_min) * 5
            reasons.append(f"market density tier {place.pop_tier}")
        else:
            score -= 10
            reasons.append("thin local market density")
        if arch.region_bias:
            if place.subregion in arch.region_bias or place.region in arch.region_bias:
                score += 10
                reasons.append(f"{place.subregion or place.region} market (mature B2B service demand)")
            else:
                score -= 8
        return max(0.0, min(100.0, score)), reasons

    @staticmethod
    def fit_band(score: float) -> str:
        if score >= 75:
            return "strong"
        if score >= 55:
            return "moderate"
        return "weak"

    # -------------------------------------------------------------- queries
    @staticmethod
    def build_queries(place: Place, arch: Archetype, per_term: int = 1) -> list[str]:
        location = place.city or place.state or place.country
        extra = f" {place.state}" if place.city and place.state else ""
        queries: list[str] = []
        for term in arch.search_terms[:4]:
            for suffix in ("", " contact email")[:per_term + 1]:
                queries.append(f"{term} {location}{extra}{suffix}".strip())
        return queries

    # --------------------------------------------------------------- suggest
    def suggest(
        self,
        offering: str,
        geo_filter: dict | None = None,
        top_n: int = 12,
        use_llm: bool = True,
        candidate_places: list[Place] | None = None,
    ) -> dict:
        matches = self.match_archetypes(offering)
        primary, primary_score = matches[0]
        archs = [a for a, _ in matches]

        # candidate universe: the user's selection, else the whole dataset
        constrained = bool(geo_filter and (geo_filter.get("selections") or geo_filter.get("countries")))
        if candidate_places is not None:
            places = candidate_places
        elif constrained:
            places = self.geo.expand(geo_filter, max_places=400)
        else:
            places = self.geo.expand({"selections": [{"country": "*", "state": "*", "city": "*"}]},
                                     max_places=700)

        scored: list[dict] = []
        for place in places:
            best = 0.0
            best_reasons: list[str] = []
            best_arch = primary
            for arch in archs:
                s, reasons = self.score_place(place, arch)
                if s > best:
                    best, best_reasons, best_arch = s, reasons, arch
            scored.append(
                {
                    "place": place,
                    "score": round(best, 1),
                    "fit": self.fit_band(best),
                    "reasons": best_reasons,
                    "archetype": best_arch.key,
                }
            )
        scored.sort(
            key=lambda item: (-item["score"], -item["place"].pop_tier, item["place"].label)
        )
        top = scored[:top_n]

        # aggregate keywords / hooks across matched archetypes
        search_terms, categories, hooks, personas, angles = [], [], [], [], []
        for arch in archs:
            search_terms += arch.search_terms
            categories += arch.categories
            hooks += arch.hooks
            personas += arch.personas
            angles += arch.offer_angles
        search_terms = _dedupe(search_terms)[:10]
        categories = _dedupe(categories)[:12]
        hooks = _dedupe(hooks)[:8]
        personas = _dedupe(personas)[:8]
        angles = _dedupe(angles)[:6]

        suggestions = []
        for item in top:
            place: Place = item["place"]
            suggestions.append(
                {
                    "label": place.label,
                    "countryCode": place.country_code,
                    "country": place.country,
                    "state": place.state,
                    "city": place.city,
                    "climate": place.climate,
                    "avgSummerC": place.avg_summer_c,
                    "popTier": place.pop_tier,
                    "score": item["score"],
                    "fit": item["fit"],
                    "reasons": item["reasons"],
                    "archetype": item["archetype"],
                    "sampleQueries": self.build_queries(place, primary)[:4],
                }
            )

        result = {
            "offering": offering,
            "matchedArchetypes": [
                {"key": a.key, "label": a.label, "score": round(s, 2)} for a, s in matches
            ],
            "primaryArchetype": primary.key,
            "archetypeLabel": primary.label,
            "constrainedToSelection": constrained,
            "candidateCount": len(places),
            "suggestions": suggestions,
            "searchTerms": search_terms,
            "targetCategories": categories,
            "hooks": hooks,
            "personas": personas,
            "offerAngles": angles,
            "seasonality": "; ".join(_dedupe([a.seasonality for a in archs]))[:400],
            "buyerSignals": _dedupe([sig for a in archs for sig in a.buyer_signals])[:12],
            "strategy": self._strategy(offering, primary, suggestions, constrained),
            "source": "knowledge-base",
            "llm": {"used": False},
        }

        if use_llm:
            try:
                enriched = self._llm_enrich(offering, primary, suggestions, personas)
                result["llm"] = {"used": True, "provider": get_llm().info()["provider"]}
                result["source"] = "knowledge-base+llm"
                result.update(enriched)
            except LLMError as exc:
                log.info("LLM enrichment skipped: %s", exc)
                result["llm"] = {"used": False, "reason": str(exc)}
        return result

    def _strategy(
        self, offering: str, arch: Archetype, suggestions: list[dict], constrained: bool
    ) -> str:
        where = "your selected geography" if constrained else "the full dataset"
        hot = [s for s in suggestions if s["score"] >= 70]
        lead = hot[0]["label"] if hot else (suggestions[0]["label"] if suggestions else "your target list")
        return (
            f"For '{offering or 'your service'}' the strongest match is the {arch.label} archetype. "
            f"Ranking {where} puts {lead} at the top: "
            + (
                "; ".join(hot[0]["reasons"][:2])
                if hot and hot[0]["reasons"]
                else "best overall balance of climate fit and market density"
            )
            + f". Seasonality: {arch.seasonality}. Lead with "
            + (arch.hooks[0] if arch.hooks else "a concrete operational pain point")
            + " and offer "
            + (arch.offer_angles[0] if arch.offer_angles else "a low-risk first step")
            + "."
        )

    def _llm_enrich(
        self, offering: str, arch: Archetype, suggestions: list[dict], personas: list[str]
    ) -> dict:
        prompt = {
            "task": "Refine B2B targeting for a cold outreach campaign.",
            "offering": offering,
            "archetype": arch.label,
            "topPlaces": [
                {
                    "label": s["label"],
                    "avgSummerC": s["avgSummerC"],
                    "popTier": s["popTier"],
                    "climate": s["climate"],
                    "score": s["score"],
                }
                for s in suggestions[:10]
            ],
            "knownPersonas": personas,
        }
        data = get_llm().chat_json(
            json.dumps(prompt, ensure_ascii=False),
            system=(
                "You are a B2B demand-generation strategist. Respond with JSON only, using the keys: "
                'extraHooks (array of 5 short pain points), extraCategories (array of 5 business types), '
                'rerank (array of place labels in best-to-worst order), subjectAngles (array of 4 short '
                'email subject angles), objections (array of 4 likely objections with rebuttals as '
                '"objection -> rebuttal" strings).'
            ),
            temperature=0.4,
            max_tokens=900,
        )
        out: dict = {}
        if data.get("extraHooks"):
            out["hooks"] = _dedupe(list(data["extraHooks"])[:5] + [])
        if data.get("extraCategories"):
            out["targetCategories"] = _dedupe(list(data["extraCategories"])[:6])
        if data.get("subjectAngles"):
            out["subjectAngles"] = _dedupe(list(data["subjectAngles"])[:6])
        if data.get("objections"):
            out["objections"] = list(data["objections"])[:6]
        rerank = data.get("rerank")
        if isinstance(rerank, list) and suggestions:
            order = {label: idx for idx, label in enumerate(str(x) for x in rerank)}
            suggestions.sort(key=lambda s: order.get(s["label"], 999))
            out["suggestions"] = suggestions
        return out

    # ------------------------------------------------------- adjacent ideas
    def adjacent_niches(self, offering: str, limit: int = 5) -> list[dict]:
        """Cross-sell ideas: archetypes that share buyer segments with the matched one."""
        matches = self.match_archetypes(offering)
        matched_keys = {a.key for a, _ in matches}
        primary = matches[0][0]
        primary_tags = set(primary.buyer_tags)
        out = []
        for arch in ARCHETYPES:
            if arch.key in matched_keys:
                continue
            shared = primary_tags & set(arch.buyer_tags)
            overlap = len(shared)
            if not overlap:
                continue
            out.append(
                {
                    "key": arch.key,
                    "label": arch.label,
                    "sharedBuyers": sorted(shared),
                    "score": overlap,
                }
            )
        out.sort(key=lambda d: (-d["score"], d["label"]))
        return out[:limit]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


_advisor: NicheAdvisor | None = None


def get_niche_advisor() -> NicheAdvisor:
    global _advisor
    if _advisor is None:
        _advisor = NicheAdvisor()
    return _advisor
