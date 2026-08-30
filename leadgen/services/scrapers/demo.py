"""Deterministic demo-data scraper.

Exists so the whole product can be exercised end to end (scrape -> review ->
send -> reply -> CRM) with no network access and no API key.  Every record it
produces is clearly synthetic: the ``source`` is ``demo``, addresses use
``example.com``-style domains that cannot receive mail, and the UI labels the
batch as simulated.  It never pretends to be real scraped data.
"""

from __future__ import annotations

import hashlib
import random
import re

from .base import BaseScraper, ScrapedLead

SURNAMES = [
    "Alvarez", "Bennett", "Chowdhury", "Duarte", "Ellison", "Fitzgerald", "Garcia",
    "Haddad", "Iversen", "Jensen", "Kowalski", "Lindqvist", "Moreau", "Nakamura",
    "Okonkwo", "Patel", "Quintero", "Rossi", "Sandoval", "Tanaka", "Ueda",
    "Vasquez", "Whitfield", "Yilmaz", "Zielinski",
]
FIRST_NAMES = [
    "Amara", "Ben", "Carla", "Diego", "Elena", "Farid", "Grace", "Hugo", "Ingrid",
    "Jonas", "Kavya", "Liam", "Mira", "Noah", "Olga", "Priya", "Rafael", "Sofia",
    "Tomas", "Umar", "Vera", "Will", "Yara", "Zane",
]
BRAND_PARTS_A = [
    "Summit", "Northside", "Bluegrass", "Copperline", "Delta", "Eastgate", "Fairway",
    "Granite", "Harborview", "Ironwood", "Juniper", "Keystone", "Lakeshore", "Meridian",
    "Oakfield", "Pinnacle", "Quarry", "Redrock", "Stonebridge", "Timberline",
]
BRAND_PARTS_B = [
    "Mechanical", "Services", "Solutions", "Group", "Partners", "Systems", "Works",
    "Collective", "Contracting", "Specialists", "Co", "Holdings", "Trading",
]

CATEGORY_TEMPLATES = {
    "hvac": ("Air Conditioning", ["Heating & Cooling", "Air Systems", "Climate Control"]),
    "roofing": ("Roofing", ["Roof Works", "Exterior Restoration", "Roofing Co"]),
    "solar": ("Solar", ["Renewables", "Energy Systems", "Power Group"]),
    "cleaning": ("Clean", ["Facility Care", "Janitorial", "Commercial Clean"]),
    "landscaping": ("Landscapes", ["Grounds Care", "Outdoor Living", "Turf Co"]),
    "pest": ("Pest Control", ["Pest Solutions", "Exterminators", "Vector Control"]),
    "dental": ("Dental", ["Family Dentistry", "Dental Studio", "Smile Care"]),
    "restaurant": ("Kitchen", ["Eatery", "Grill", "Provisions"]),
    "it": ("IT Solutions", ["Managed IT", "Tech Partners", "Networks"]),
    "logistics": ("Freight", ["Logistics", "Cargo Systems", "Distribution"]),
    "default": ("Services", ["Group", "Solutions", "Partners", "Co"]),
}

CATEGORY_LOOKUP = [
    (r"hvac|air conditioning|heating|cooling", "hvac"),
    (r"roof", "roofing"),
    (r"solar|renewable", "solar"),
    (r"clean|janitorial", "cleaning"),
    (r"landscap|lawn|garden", "landscaping"),
    (r"pest|termite|mosquito", "pest"),
    (r"dental|dentist", "dental"),
    (r"restaurant|cafe|catering|food", "restaurant"),
    (r"\bit\b|msp|cyber|network|software", "it"),
    (r"logistic|freight|shipping|warehouse", "logistics"),
]

SNIPPETS = {
    "hvac": [
        "24/7 emergency AC repair and commercial maintenance contracts across {city}.",
        "NATE-certified technicians serving {city} homes and light commercial buildings.",
        "Family owned {city} HVAC company, {reviews} five-star reviews since 2009.",
    ],
    "roofing": [
        "Storm damage specialists in {city}, insurance claim documentation included.",
        "Residential and commercial roofing in {city}. GAF Master Elite certified.",
    ],
    "solar": [
        "Design-build solar installer serving {city} businesses, 0% financing available.",
        "{city} solar and battery storage, {reviews} installations completed.",
    ],
    "cleaning": [
        "Nightly office cleaning in {city}, bonded and insured, green products.",
        "Commercial janitorial services for {city} medical and professional offices.",
    ],
    "landscaping": [
        "Weekly maintenance routes for {city} HOAs and commercial properties.",
        "Design, installation and irrigation service across {city}.",
    ],
    "pest": [
        "Quarterly commercial pest control plans for {city} restaurants and warehouses.",
        "Termite and rodent specialists serving {city} since 1998.",
    ],
    "dental": [
        "Modern family dentistry in {city}, same-day emergency appointments.",
        "Cosmetic and restorative dentistry, {reviews} patient reviews in {city}.",
    ],
    "restaurant": [
        "Locally sourced seasonal menu in the heart of {city}.",
        "Neighbourhood {city} restaurant, private dining and catering available.",
    ],
    "it": [
        "Managed IT and cybersecurity for {city} professional firms, 15 minute response.",
        "Microsoft partner providing helpdesk, cloud migration and compliance for {city} SMBs.",
    ],
    "logistics": [
        "Regional LTL and FTL carrier based in {city}, bonded warehouse on site.",
        "{city} 3PL with same-day fulfilment and customs brokerage.",
    ],
    "default": [
        "Serving {city} businesses and homeowners with {reviews} verified reviews.",
        "Local {city} company, free estimates, licensed and insured.",
    ],
}


def _category_key(query: str) -> str:
    text = (query or "").lower()
    for pattern, key in CATEGORY_LOOKUP:
        if re.search(pattern, text):
            return key
    return "default"


def _seed(text: str) -> int:
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:12], 16)


class DemoScraper(BaseScraper):
    """Produces deterministic, clearly-labelled synthetic leads for a query."""

    name = "demo"
    requires_key = False

    def __init__(self, *args, city: str = "", state: str = "", country: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.city = city
        self.state = state
        self.country = country
        self.last_error = ""

    def search(self, query: str, limit: int = 20) -> list[ScrapedLead]:
        key = _category_key(query)
        primary, alternates = CATEGORY_TEMPLATES[key]
        snippets = SNIPPETS.get(key, SNIPPETS["default"])
        rng = random.Random(_seed(f"{query}|{self.city}|{limit}"))
        results: list[ScrapedLead] = []
        used_names: set[str] = set()
        for index in range(limit):
            for _ in range(12):
                brand = f"{rng.choice(BRAND_PARTS_A)} {primary if index % 2 == 0 else rng.choice(alternates)}"
                if brand not in used_names:
                    used_names.add(brand)
                    break
            first, last = rng.choice(FIRST_NAMES), rng.choice(SURNAMES)
            slug = re.sub(r"[^a-z0-9]", "", brand.lower())[:18] or "business"
            domain = f"{slug}{rng.randint(10, 99)}.example.com"
            reviews = rng.choice([0, 8, 17, 34, 62, 118, 205, 340])
            rating = round(rng.uniform(3.6, 5.0), 1) if reviews else None
            snippet = rng.choice(snippets).format(
                city=self.city or "the metro area", reviews=reviews
            )
            phone = f"+1 ({rng.randint(201, 989)}) 555-{rng.randint(1000, 9999)}"
            results.append(
                ScrapedLead(
                    business_name=brand,
                    contact_name=f"{first} {last}",
                    email=f"{first[0].lower()}{last.lower()}@{domain}",
                    website=f"https://{domain}",
                    phone=phone,
                    address=f"{rng.randint(100, 9800)} {rng.choice(BRAND_PARTS_A)} St, "
                    f"{self.city or 'Springfield'}",
                    city=self.city,
                    state=self.state,
                    country=self.country,
                    category=primary.title() if key != "default" else query.split(" ")[0].title(),
                    snippet=snippet,
                    source=self.name,
                    source_url=f"https://{domain}",
                    rating=rating,
                    review_count=reviews or None,
                    signals={"synthetic": True},
                )
            )
        return results
