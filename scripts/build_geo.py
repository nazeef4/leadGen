#!/usr/bin/env python3
"""Builds the bundled geo dataset from compact curated source tables.

Run with:  python scripts/build_geo.py

Each country record carries the attributes the niche advisor reasons over:
  * climate tags   -> hot / humid / arid / tropical / cold / temperate / coastal /
                      mountain / monsoon / continental / mediterranean
  * avg summer C   -> mean warmest-month temperature (drives HVAC / solar / roofing logic)
  * population tier -> 1: 100k-500k, 2: 500k-1M, 3: 1M-3M, 4: 3M+  (market density signal)

City records are stored pipe-delimited to keep the source table readable:
    "Phoenix|hot arid|41|3,Tucson|hot arid|38|2"
"""

from __future__ import annotations

import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "leadgen" / "data" / "geo"

CLIMATE_TAGS = {
    "hot", "humid", "arid", "tropical", "cold", "temperate",
    "coastal", "mountain", "monsoon", "continental", "mediterranean", "desert",
    "subtropical", "highland",
}

# code: (name, region, subregion, {state_code: (state_name, "city|climate|summerC|popTier,...")})
GEO: dict[str, tuple[str, str, str, dict[str, tuple[str, str]]]] = {
    "US": ("United States", "Americas", "North America", {
        "AL": ("Alabama", "Birmingham|humid subtropical|33|2,Huntsville|humid subtropical|33|1,Mobile|humid coastal|33|1,Montgomery|humid subtropical|34|1"),
        "AK": ("Alaska", "Anchorage|cold continental|19|1,Fairbanks|cold continental|22|1,Juneau|cold coastal|18|1"),
        "AZ": ("Arizona", "Phoenix|hot arid desert|41|3,Tucson|hot arid desert|38|2,Mesa|hot arid desert|40|2,Scottsdale|hot arid desert|41|1,Flagstaff|arid mountain|28|1,Yuma|hot arid desert|42|1"),
        "AR": ("Arkansas", "Little Rock|humid subtropical|34|1,Fayetteville|humid subtropical|32|1,Fort Smith|humid subtropical|34|1"),
        "CA": ("California", "Los Angeles|mediterranean coastal|29|4,San Diego|mediterranean coastal|26|2,San Jose|mediterranean|29|1,San Francisco|temperate coastal|22|1,Fresno|hot arid|38|1,Sacramento|hot mediterranean|37|1,Riverside|hot arid|40|2,Bakersfield|hot arid|39|1,Anaheim|mediterranean|30|1,Stockton|hot mediterranean|36|1,Long Beach|mediterranean coastal|28|1,Palm Springs|hot arid desert|43|1"),
        "CO": ("Colorado", "Denver|continental mountain|31|2,Colorado Springs|continental mountain|29|1,Aurora|continental mountain|31|1,Fort Collins|continental mountain|31|1,Boulder|continental mountain|31|1"),
        "CT": ("Connecticut", "Bridgeport|humid continental|28|1,New Haven|humid continental coastal|28|1,Hartford|humid continental|29|1,Stamford|humid continental coastal|28|1"),
        "DE": ("Delaware", "Wilmington|humid continental|30|1,Dover|humid subtropical|31|1"),
        "FL": ("Florida", "Miami|tropical humid coastal|33|1,Jacksonville|humid subtropical coastal|33|1,Tampa|humid subtropical coastal|33|1,Orlando|humid subtropical|34|1,Tallahassee|humid subtropical|34|1,Fort Lauderdale|tropical humid coastal|33|1,Naples|tropical humid coastal|33|1,Sarasota|humid subtropical coastal|33|1,Key West|tropical coastal|33|1,Gainesville|humid subtropical|34|1"),
        "GA": ("Georgia", "Atlanta|humid subtropical|32|2,Savannah|humid subtropical coastal|33|1,Augusta|humid subtropical|34|1,Columbus|humid subtropical|34|1,Macon|humid subtropical|34|1"),
        "HI": ("Hawaii", "Honolulu|tropical coastal|32|1,Hilo|tropical humid|29|1,Kailua|tropical coastal|31|1"),
        "ID": ("Idaho", "Boise|continental arid|34|1,Meridian|continental arid|34|1,Idaho Falls|continental mountain|28|1"),
        "IL": ("Illinois", "Chicago|humid continental|29|3,Aurora|humid continental|30|1,Rockford|humid continental|30|1,Naperville|humid continental|30|1,Peoria|humid continental|31|1"),
        "IN": ("Indiana", "Indianapolis|humid continental|30|1,Fort Wayne|humid continental|29|1,Evansville|humid subtropical|32|1,South Bend|humid continental|29|1"),
        "IA": ("Iowa", "Des Moines|humid continental|30|1,Cedar Rapids|humid continental|30|1,Davenport|humid continental|30|1"),
        "KS": ("Kansas", "Wichita|humid continental|34|1,Overland Park|humid continental|33|1,Kansas City|humid continental|33|1,Topeka|humid continental|33|1"),
        "KY": ("Kentucky", "Louisville|humid subtropical|32|1,Lexington|humid subtropical|31|1,Bowling Green|humid subtropical|33|1"),
        "LA": ("Louisiana", "New Orleans|humid subtropical coastal|33|1,Baton Rouge|humid subtropical|34|1,Shreveport|humid subtropical|36|1,Lafayette|humid subtropical|34|1"),
        "ME": ("Maine", "Portland|humid continental coastal|26|1,Bangor|cold continental|26|1"),
        "MD": ("Maryland", "Baltimore|humid subtropical|31|2,Columbia|humid subtropical|31|1,Annapolis|humid subtropical coastal|31|1"),
        "MA": ("Massachusetts", "Boston|humid continental coastal|28|2,Worcester|humid continental|28|1,Springfield|humid continental|29|1,Cambridge|humid continental coastal|28|1"),
        "MI": ("Michigan", "Detroit|humid continental|28|2,Grand Rapids|humid continental|28|1,Ann Arbor|humid continental|28|1,Lansing|humid continental|28|1"),
        "MN": ("Minnesota", "Minneapolis|cold continental|28|2,Saint Paul|cold continental|28|1,Rochester|cold continental|28|1,Duluth|cold continental|24|1"),
        "MS": ("Mississippi", "Jackson|humid subtropical|34|1,Gulfport|humid subtropical coastal|33|1,Hattiesburg|humid subtropical|35|1"),
        "MO": ("Missouri", "Kansas City|humid continental|33|1,St. Louis|humid subtropical|33|2,Springfield|humid subtropical|33|1,Columbia|humid subtropical|32|1"),
        "MT": ("Montana", "Billings|continental mountain|31|1,Missoula|continental mountain|30|1,Bozeman|continental mountain|28|1,Great Falls|continental mountain|29|1"),
        "NE": ("Nebraska", "Omaha|humid continental|32|1,Lincoln|humid continental|32|1,Bellevue|humid continental|32|1"),
        "NV": ("Nevada", "Las Vegas|hot arid desert|41|2,Henderson|hot arid desert|41|1,Reno|arid mountain|33|1,North Las Vegas|hot arid desert|41|1"),
        "NH": ("New Hampshire", "Manchester|humid continental|27|1,Nashua|humid continental|27|1,Concord|humid continental|27|1"),
        "NJ": ("New Jersey", "Newark|humid subtropical|30|1,Jersey City|humid subtropical coastal|29|1,Paterson|humid subtropical|30|1,Trenton|humid subtropical|30|1"),
        "NM": ("New Mexico", "Albuquerque|arid mountain|34|1,Las Cruces|hot arid|38|1,Santa Fe|arid mountain|30|1"),
        "NY": ("New York", "New York City|humid subtropical coastal|29|4,Buffalo|cold continental|26|1,Rochester|cold continental|27|1,Yonkers|humid continental|29|1,Syracuse|cold continental|27|1,Albany|humid continental|28|1"),
        "NC": ("North Carolina", "Charlotte|humid subtropical|32|2,Raleigh|humid subtropical|32|1,Greensboro|humid subtropical|32|1,Durham|humid subtropical|32|1,Wilmington|humid subtropical coastal|32|1,Asheville|humid subtropical mountain|28|1"),
        "ND": ("North Dakota", "Fargo|cold continental|28|1,Bismarck|cold continental|29|1,Grand Forks|cold continental|27|1"),
        "OH": ("Ohio", "Columbus|humid continental|29|2,Cleveland|humid continental|28|1,Cincinnati|humid subtropical|30|1,Toledo|humid continental|29|1,Dayton|humid continental|29|1"),
        "OK": ("Oklahoma", "Oklahoma City|humid subtropical|35|2,Tulsa|humid subtropical|35|1,Norman|humid subtropical|35|1,Lawton|humid subtropical|36|1"),
        "OR": ("Oregon", "Portland|temperate coastal|28|2,Salem|temperate|29|1,Eugene|temperate|29|1,Bend|continental arid|29|1,Beaverton|temperate|28|1"),
        "PA": ("Pennsylvania", "Philadelphia|humid subtropical|30|2,Pittsburgh|humid continental|28|1,Allentown|humid continental|29|1,Erie|humid continental|26|1,Harrisburg|humid continental|30|1"),
        "RI": ("Rhode Island", "Providence|humid continental|28|1,Warwick|humid continental coastal|27|1,Cranston|humid continental coastal|27|1"),
        "SC": ("South Carolina", "Charleston|humid subtropical coastal|33|1,Columbia|humid subtropical|34|1,Greenville|humid subtropical|32|1,Myrtle Beach|humid subtropical coastal|32|1"),
        "SD": ("South Dakota", "Sioux Falls|cold continental|29|1,Rapid City|continental arid|31|1,Aberdeen|cold continental|28|1"),
        "TN": ("Tennessee", "Nashville|humid subtropical|32|2,Memphis|humid subtropical|34|2,Knoxville|humid subtropical|31|1,Chattanooga|humid subtropical|32|1"),
        "TX": ("Texas", "Houston|humid subtropical coastal|35|3,San Antonio|humid subtropical|36|2,Dallas|humid subtropical|37|2,Austin|humid subtropical|36|2,Fort Worth|humid subtropical|37|2,El Paso|hot arid desert|38|1,Arlington|humid subtropical|37|1,Corpus Christi|tropical coastal|34|1,Lubbock|hot arid|35|1,Amarillo|arid continental|34|1,McAllen|tropical humid|38|1,Midland|hot arid|36|1"),
        "UT": ("Utah", "Salt Lake City|continental arid mountain|34|1,West Valley City|continental arid|34|1,Provo|continental arid mountain|33|1,St. George|hot arid desert|40|1"),
        "VT": ("Vermont", "Burlington|cold continental|26|1,South Burlington|cold continental|26|1,Rutland|cold continental|26|1"),
        "VA": ("Virginia", "Virginia Beach|humid subtropical coastal|31|1,Norfolk|humid subtropical coastal|31|1,Richmond|humid subtropical|32|1,Arlington|humid subtropical|31|1,Chesapeake|humid subtropical coastal|31|1"),
        "WA": ("Washington", "Seattle|temperate coastal|25|2,Spokane|continental arid|31|1,Tacoma|temperate coastal|26|1,Vancouver|temperate coastal|26|1,Bellevue|temperate coastal|25|1,Tri-Cities|arid continental|33|1"),
        "WV": ("West Virginia", "Charleston|humid subtropical|30|1,Huntington|humid subtropical|30|1,Morgantown|humid continental|29|1"),
        "WI": ("Wisconsin", "Milwaukee|humid continental|27|2,Madison|humid continental|28|1,Green Bay|cold continental|26|1,Kenosha|humid continental|27|1"),
        "WY": ("Wyoming", "Cheyenne|continental arid mountain|29|1,Casper|continental arid|31|1,Laramie|continental mountain|26|1"),
        "DC": ("District of Columbia", "Washington|humid subtropical|31|2"),
    }),
    "CA": ("Canada", "Americas", "North America", {
        "AB": ("Alberta", "Calgary|cold continental|23|2,Edmonton|cold continental|23|1,Red Deer|cold continental|23|1,Lethbridge|continental arid|27|1"),
        "BC": ("British Columbia", "Vancouver|temperate coastal|23|2,Victoria|temperate coastal|22|1,Surrey|temperate coastal|23|1,Kelowna|continental|29|1,Abbotsford|temperate coastal|24|1"),
        "MB": ("Manitoba", "Winnipeg|cold continental|26|1,Brandon|cold continental|26|1"),
        "NB": ("New Brunswick", "Moncton|humid continental|25|1,Saint John|humid continental coastal|23|1,Fredericton|humid continental|26|1"),
        "NL": ("Newfoundland and Labrador", "St. John's|cold coastal|20|1,Corner Brook|cold coastal|20|1"),
        "NS": ("Nova Scotia", "Halifax|humid continental coastal|23|1,Dartmouth|humid continental coastal|23|1,Sydney|cold coastal|22|1"),
        "ON": ("Ontario", "Toronto|humid continental|27|3,Ottawa|cold continental|27|1,Mississauga|humid continental|27|1,Hamilton|humid continental|27|1,London|humid continental|27|1,Windsor|humid continental|28|1,Kitchener|humid continental|27|1"),
        "PE": ("Prince Edward Island", "Charlottetown|humid continental coastal|23|1,Summerside|humid continental coastal|23|1"),
        "QC": ("Quebec", "Montreal|cold continental|26|2,Quebec City|cold continental|25|1,Laval|cold continental|26|1,Gatineau|cold continental|27|1,Longueuil|cold continental|26|1"),
        "SK": ("Saskatchewan", "Saskatoon|cold continental|25|1,Regina|cold continental|26|1"),
    }),
    "GB": ("United Kingdom", "Europe", "Northern Europe", {
        "ENG": ("England", "London|temperate|23|4,Manchester|temperate|21|2,Birmingham|temperate|22|1,Liverpool|temperate coastal|20|1,Leeds|temperate|21|1,Bristol|temperate|22|1,Newcastle|temperate coastal|20|1,Sheffield|temperate|21|1,Nottingham|temperate|22|1,Southampton|temperate coastal|22|1,Brighton|temperate coastal|22|1,Coventry|temperate|22|1"),
        "SCT": ("Scotland", "Glasgow|temperate coastal|19|1,Edinburgh|temperate coastal|19|1,Aberdeen|cold coastal|17|1,Dundee|temperate coastal|18|1"),
        "WLS": ("Wales", "Cardiff|temperate coastal|20|1,Swansea|temperate coastal|19|1,Newport|temperate coastal|20|1"),
        "NIR": ("Northern Ireland", "Belfast|temperate coastal|19|1,Derry|temperate coastal|18|1,Lisburn|temperate|19|1"),
    }),
    "IE": ("Ireland", "Europe", "Northern Europe", {
        "D": ("Dublin", "Dublin|temperate coastal|19|2,Tallaght|temperate|19|1"),
        "C": ("Cork", "Cork|temperate coastal|19|1"),
        "G": ("Galway", "Galway|temperate coastal|18|1"),
        "L": ("Limerick", "Limerick|temperate|19|1"),
    }),
    "AU": ("Australia", "Oceania", "Australasia", {
        "NSW": ("New South Wales", "Sydney|temperate coastal|26|3,Newcastle|temperate coastal|26|1,Wollongong|temperate coastal|24|1,Central Coast|temperate coastal|25|1,Albury|temperate|28|1"),
        "VIC": ("Victoria", "Melbourne|temperate coastal|26|3,Geelong|temperate coastal|24|1,Ballarat|temperate|22|1,Bendigo|temperate|27|1"),
        "QLD": ("Queensland", "Brisbane|tropical humid coastal|30|2,Gold Coast|tropical humid coastal|29|2,Cairns|tropical monsoon coastal|31|1,Townsville|tropical coastal|31|1,Sunshine Coast|tropical coastal|29|1,Toowoomba|humid subtropical|26|1"),
        "WA": ("Western Australia", "Perth|mediterranean coastal|32|2,Bunbury|mediterranean coastal|29|1,Kalgoorlie|hot arid|33|1"),
        "SA": ("South Australia", "Adelaide|mediterranean coastal|29|1,Mount Gambier|mediterranean|23|1,Whyalla|arid coastal|28|1"),
        "TAS": ("Tasmania", "Hobart|temperate coastal|21|1,Launceston|temperate|22|1,Devonport|temperate coastal|20|1"),
        "NT": ("Northern Territory", "Darwin|tropical monsoon|32|1,Alice Springs|hot arid desert|36|1,Palmerston|tropical monsoon|32|1"),
        "ACT": ("Australian Capital Territory", "Canberra|temperate continental|28|1"),
    }),
    "NZ": ("New Zealand", "Oceania", "Australasia", {
        "AUK": ("Auckland", "Auckland|temperate coastal|24|2,Manukau|temperate coastal|24|1"),
        "WGN": ("Wellington", "Wellington|temperate coastal|20|1,Lower Hutt|temperate coastal|20|1"),
        "CAN": ("Canterbury", "Christchurch|temperate coastal|22|1,Timaru|temperate|21|1"),
        "HKB": ("Hawke's Bay", "Napier|temperate coastal|24|1,Hastings|temperate|25|1"),
    }),
    "IN": ("India", "Asia", "Southern Asia", {
        "MH": ("Maharashtra", "Mumbai|tropical humid coastal|32|4,Pune|tropical|31|2,Nagpur|tropical|36|2,Nashik|tropical|33|1,Thane|tropical humid coastal|33|2"),
        "DL": ("Delhi", "New Delhi|hot arid|40|4,Dwarka|hot arid|40|1"),
        "KA": ("Karnataka", "Bengaluru|tropical|30|3,Mysuru|tropical|32|1,Mangaluru|tropical coastal|31|1,Hubballi|tropical|32|1"),
        "TN": ("Tamil Nadu", "Chennai|tropical humid coastal|38|3,Coimbatore|tropical|35|2,Madurai|hot tropical|37|1,Salem|hot tropical|37|1"),
        "TG": ("Telangana", "Hyderabad|hot tropical|37|3,Warangal|hot tropical|39|1,Nizamabad|hot tropical|38|1"),
        "GJ": ("Gujarat", "Ahmedabad|hot arid|38|2,Surat|hot humid coastal|34|2,Vadodara|hot|37|1,Rajkot|hot arid|38|1"),
        "WB": ("West Bengal", "Kolkata|tropical humid|35|3,Howrah|tropical humid|35|1,Durgapur|tropical|36|1"),
        "RJ": ("Rajasthan", "Jaipur|hot arid|38|2,Jodhpur|hot arid desert|40|1,Udaipur|hot arid|37|1,Kota|hot arid|40|1"),
        "UP": ("Uttar Pradesh", "Lucknow|hot subtropical|38|2,Kanpur|hot subtropical|39|2,Agra|hot arid|40|1,Noida|hot arid|40|1,Ghaziabad|hot arid|40|1"),
        "KL": ("Kerala", "Kochi|tropical monsoon coastal|31|2,Thiruvananthapuram|tropical monsoon coastal|31|1,Kozhikode|tropical monsoon coastal|31|1"),
        "PB": ("Punjab", "Ludhiana|hot subtropical|39|1,Amritsar|hot subtropical|39|1,Jalandhar|hot subtropical|39|1"),
        "HR": ("Haryana", "Gurugram|hot arid|40|2,Faridabad|hot arid|41|2,Panipat|hot arid|40|1"),
    }),
    "AE": ("United Arab Emirates", "Asia", "Western Asia", {
        "DU": ("Dubai", "Dubai|hot arid desert coastal|42|3,Al Ain|hot arid desert|44|1"),
        "AZ": ("Abu Dhabi", "Abu Dhabi|hot arid desert coastal|42|1"),
        "SH": ("Sharjah", "Sharjah|hot arid desert coastal|42|1"),
        "RK": ("Ras Al Khaimah", "Ras Al Khaimah|hot arid desert coastal|42|1"),
    }),
    "SA": ("Saudi Arabia", "Asia", "Western Asia", {
        "01": ("Riyadh", "Riyadh|hot arid desert|43|3"),
        "02": ("Makkah", "Jeddah|hot arid desert coastal|40|2,Makkah|hot arid desert|43|1,Taif|arid mountain|33|1"),
        "04": ("Eastern Province", "Dammam|hot arid desert coastal|42|1,Khobar|hot arid desert coastal|42|1,Dhahran|hot arid desert coastal|42|1,Al Jubail|hot arid desert coastal|43|1"),
        "03": ("Madinah", "Madinah|hot arid desert|44|1"),
    }),
    "QA": ("Qatar", "Asia", "Western Asia", {
        "DA": ("Doha", "Doha|hot arid desert coastal|42|2,Al Rayyan|hot arid desert coastal|42|1,Al Wakrah|hot arid desert coastal|42|1"),
    }),
    "KW": ("Kuwait", "Asia", "Western Asia", {
        "KU": ("Al Asimah", "Kuwait City|hot arid desert coastal|45|2,Hawalli|hot arid desert coastal|45|1"),
    }),
    "SG": ("Singapore", "Asia", "South-Eastern Asia", {
        "SG": ("Singapore", "Singapore|tropical humid coastal|32|3"),
    }),
    "MY": ("Malaysia", "Asia", "South-Eastern Asia", {
        "14": ("Kuala Lumpur", "Kuala Lumpur|tropical humid|33|2"),
        "10": ("Selangor", "Petaling Jaya|tropical humid|33|1,Shah Alam|tropical humid|33|1,Klang|tropical humid coastal|33|1"),
        "07": ("Penang", "George Town|tropical humid coastal|32|1,Butterworth|tropical humid coastal|32|1"),
        "01": ("Johor", "Johor Bahru|tropical humid coastal|32|1"),
    }),
    "ID": ("Indonesia", "Asia", "South-Eastern Asia", {
        "JK": ("Jakarta", "Jakarta|tropical humid coastal|33|4"),
        "JI": ("East Java", "Surabaya|tropical humid coastal|34|2,Malang|tropical mountain|29|1"),
        "BA": ("Bali", "Denpasar|tropical humid coastal|32|1,Badung|tropical humid coastal|32|1"),
        "SU": ("North Sumatra", "Medan|tropical humid|33|2"),
    }),
    "PH": ("Philippines", "Asia", "South-Eastern Asia", {
        "NCR": ("Metro Manila", "Manila|tropical humid coastal|34|3,Quezon City|tropical humid|34|3,Makati|tropical humid coastal|34|1,Taguig|tropical humid coastal|34|1"),
        "CEB": ("Cebu", "Cebu City|tropical humid coastal|33|1"),
        "DAV": ("Davao", "Davao City|tropical humid coastal|33|1"),
    }),
    "TH": ("Thailand", "Asia", "South-Eastern Asia", {
        "10": ("Bangkok", "Bangkok|tropical monsoon|35|4,Nonthaburi|tropical monsoon|35|1"),
        "83": ("Phuket", "Phuket|tropical monsoon coastal|33|1"),
        "50": ("Chiang Mai", "Chiang Mai|tropical|36|1"),
    }),
    "VN": ("Vietnam", "Asia", "South-Eastern Asia", {
        "HN": ("Hanoi", "Hanoi|tropical monsoon|34|3"),
        "SG": ("Ho Chi Minh City", "Ho Chi Minh City|tropical humid|34|3"),
        "DN": ("Da Nang", "Da Nang|tropical monsoon coastal|34|1"),
    }),
    "CN": ("China", "Asia", "Eastern Asia", {
        "BJ": ("Beijing", "Beijing|hot continental|31|4"),
        "SH": ("Shanghai", "Shanghai|humid subtropical coastal|32|4,Pudong|humid subtropical coastal|32|2"),
        "GD": ("Guangdong", "Guangzhou|tropical humid|34|3,Shenzhen|tropical humid coastal|33|3,Dongguan|tropical humid|34|2,Foshan|tropical humid|34|2"),
        "ZJ": ("Zhejiang", "Hangzhou|humid subtropical|33|2,Ningbo|humid subtropical coastal|32|2,Wenzhou|humid subtropical coastal|32|1"),
        "JS": ("Jiangsu", "Suzhou|humid subtropical|32|2,Nanjing|humid subtropical|33|2,Wuxi|humid subtropical|32|2"),
    }),
    "JP": ("Japan", "Asia", "Eastern Asia", {
        "13": ("Tokyo", "Tokyo|humid subtropical|31|4,Shinjuku|humid subtropical|31|1"),
        "27": ("Osaka", "Osaka|humid subtropical coastal|33|3,Sakai|humid subtropical coastal|33|1"),
        "23": ("Aichi", "Nagoya|humid subtropical|33|2,Toyota|humid subtropical|33|1"),
        "01": ("Hokkaido", "Sapporo|cold continental|26|2"),
        "40": ("Fukuoka", "Fukuoka|humid subtropical coastal|32|1,Kitakyushu|humid subtropical coastal|32|1"),
    }),
    "KR": ("South Korea", "Asia", "Eastern Asia", {
        "11": ("Seoul", "Seoul|humid continental|30|4,Gangnam|humid continental|30|1"),
        "26": ("Busan", "Busan|humid subtropical coastal|29|2"),
        "28": ("Incheon", "Incheon|humid continental coastal|29|2"),
    }),
    "PK": ("Pakistan", "Asia", "Southern Asia", {
        "SD": ("Sindh", "Karachi|hot arid coastal|36|4,Hyderabad|hot arid|41|1,Sukkur|hot arid desert|43|1"),
        "PB": ("Punjab", "Lahore|hot subtropical|39|3,Faisalabad|hot arid|40|2,Rawalpindi|hot subtropical|38|1,Multan|hot arid|42|1"),
        "IS": ("Islamabad", "Islamabad|hot subtropical|38|1"),
    }),
    "BD": ("Bangladesh", "Asia", "Southern Asia", {
        "C": ("Dhaka", "Dhaka|tropical monsoon|34|4,Gazipur|tropical monsoon|34|1"),
        "B": ("Chattogram", "Chattogram|tropical monsoon coastal|33|2"),
    }),
    "LK": ("Sri Lanka", "Asia", "Southern Asia", {
        "1": ("Western Province", "Colombo|tropical humid coastal|31|1,Negombo|tropical humid coastal|31|1"),
        "2": ("Central Province", "Kandy|tropical mountain|29|1"),
    }),
    "DE": ("Germany", "Europe", "Western Europe", {
        "BE": ("Berlin", "Berlin|temperate continental|26|2"),
        "BY": ("Bavaria", "Munich|temperate continental|25|2,Nuremberg|temperate continental|26|1,Augsburg|temperate continental|26|1,Wurzburg|temperate continental|26|1"),
        "BW": ("Baden-Wurttemberg", "Stuttgart|temperate continental|26|1,Karlsruhe|temperate continental|27|1,Mannheim|temperate continental|27|1,Freiburg|temperate continental|26|1"),
        "NW": ("North Rhine-Westphalia", "Cologne|temperate|26|2,Dusseldorf|temperate|26|1,Dortmund|temperate|26|1,Essen|temperate|25|1,Bonn|temperate|26|1,Munster|temperate|25|1"),
        "HE": ("Hesse", "Frankfurt|temperate|27|1,Wiesbaden|temperate|26|1,Darmstadt|temperate|26|1"),
        "HH": ("Hamburg", "Hamburg|temperate coastal|24|2"),
        "SN": ("Saxony", "Leipzig|temperate continental|26|1,Dresden|temperate continental|26|1"),
        "NI": ("Lower Saxony", "Hanover|temperate|25|1,Braunschweig|temperate|25|1,Osnabruck|temperate|25|1"),
    }),
    "FR": ("France", "Europe", "Western Europe", {
        "IDF": ("Ile-de-France", "Paris|temperate|26|3,Boulogne-Billancourt|temperate|26|1,Versailles|temperate|26|1"),
        "PAC": ("Provence-Alpes-Cote d'Azur", "Marseille|mediterranean coastal|29|1,Nice|mediterranean coastal|28|1,Toulon|mediterranean coastal|29|1,Aix-en-Provence|mediterranean|31|1"),
        "ARA": ("Auvergne-Rhone-Alpes", "Lyon|temperate|28|2,Grenoble|temperate mountain|28|1,Saint-Etienne|temperate mountain|27|1"),
        "OCC": ("Occitanie", "Toulouse|temperate|28|1,Montpellier|mediterranean coastal|29|1,Perpignan|mediterranean|31|1"),
        "NAQ": ("Nouvelle-Aquitaine", "Bordeaux|temperate|28|1,Pau|temperate|27|1,La Rochelle|temperate coastal|25|1"),
        "HDF": ("Hauts-de-France", "Lille|temperate|25|1,Amiens|temperate|25|1"),
        "GES": ("Grand Est", "Strasbourg|temperate continental|27|1,Reims|temperate|26|1,Nancy|temperate continental|26|1"),
    }),
    "ES": ("Spain", "Europe", "Southern Europe", {
        "MD": ("Madrid", "Madrid|hot continental|33|3,Alcala de Henares|hot continental|34|1,Mostoles|hot continental|34|1"),
        "CT": ("Catalonia", "Barcelona|mediterranean coastal|29|2,L'Hospitalet|mediterranean coastal|29|1,Tarragona|mediterranean coastal|29|1,Girona|mediterranean|30|1"),
        "AN": ("Andalusia", "Seville|hot mediterranean|37|2,Malaga|hot mediterranean coastal|32|1,Cordoba|hot mediterranean|39|1,Granada|hot mediterranean|36|1,Marbella|mediterranean coastal|30|1,Almeria|hot arid coastal|32|1"),
        "VC": ("Valencia", "Valencia|mediterranean coastal|31|2,Alicante|mediterranean coastal|31|1,Castellon|mediterranean coastal|30|1"),
        "PV": ("Basque Country", "Bilbao|temperate coastal|26|1,San Sebastian|temperate coastal|25|1,Vitoria-Gasteiz|temperate|27|1"),
        "GA": ("Galicia", "Vigo|temperate coastal|25|1,A Coruna|temperate coastal|23|1"),
        "CL": ("Castile and Leon", "Valladolid|hot continental|32|1,Burgos|continental|30|1,Salamanca|hot continental|33|1"),
        "MU": ("Murcia", "Murcia|hot mediterranean|35|1,Cartagena|hot mediterranean coastal|33|1"),
    }),
    "IT": ("Italy", "Europe", "Southern Europe", {
        "25": ("Lombardy", "Milan|humid subtropical|30|2,Bergamo|humid subtropical|30|1,Brescia|humid subtropical|30|1"),
        "62": ("Lazio", "Rome|mediterranean|32|3,Latina|mediterranean|32|1"),
        "72": ("Campania", "Naples|mediterranean coastal|31|2,Salerno|mediterranean coastal|31|1"),
        "52": ("Tuscany", "Florence|mediterranean|33|1,Pisa|mediterranean coastal|30|1,Prato|mediterranean|33|1"),
        "34": ("Veneto", "Venice|humid subtropical coastal|29|1,Verona|humid subtropical|31|1,Padua|humid subtropical|31|1"),
        "21": ("Piedmont", "Turin|humid subtropical|29|1,Novara|humid subtropical|30|1"),
        "82": ("Sicily", "Palermo|mediterranean coastal|30|1,Catania|mediterranean coastal|32|1,Messina|mediterranean coastal|31|1"),
        "75": ("Apulia", "Bari|mediterranean coastal|30|1,Lecce|hot mediterranean|32|1,Taranto|hot mediterranean coastal|32|1"),
    }),
    "PT": ("Portugal", "Europe", "Southern Europe", {
        "11": ("Lisbon", "Lisbon|mediterranean coastal|28|2,Cascais|mediterranean coastal|27|1,Amadora|mediterranean|28|1"),
        "13": ("Porto", "Porto|mediterranean coastal|25|2,Braga|mediterranean|28|1"),
        "08": ("Faro", "Faro|mediterranean coastal|29|1,Albufeira|mediterranean coastal|28|1,Portimao|mediterranean coastal|28|1"),
    }),
    "NL": ("Netherlands", "Europe", "Western Europe", {
        "NH": ("North Holland", "Amsterdam|temperate coastal|22|1,Haarlem|temperate coastal|22|1"),
        "ZH": ("South Holland", "Rotterdam|temperate coastal|22|1,The Hague|temperate coastal|22|1,Utrecht|temperate|23|1,Leiden|temperate coastal|22|1"),
        "NB": ("North Brabant", "Eindhoven|temperate|23|1,Tilburg|temperate|23|1,Breda|temperate|23|1"),
    }),
    "BE": ("Belgium", "Europe", "Western Europe", {
        "BRU": ("Brussels", "Brussels|temperate|24|1"),
        "VAN": ("Antwerp", "Antwerp|temperate|23|1,Mechelen|temperate|23|1"),
        "VLI": ("Limburg", "Hasselt|temperate|23|1,Genk|temperate|23|1"),
        "WBR": ("Walloon Brabant", "Louvain-la-Neuve|temperate|23|1"),
    }),
    "CH": ("Switzerland", "Europe", "Western Europe", {
        "ZH": ("Zurich", "Zurich|temperate mountain|24|1,Winterthur|temperate mountain|24|1"),
        "GE": ("Geneva", "Geneva|temperate mountain|26|1"),
        "BS": ("Basel", "Basel|temperate|26|1"),
        "BE": ("Bern", "Bern|temperate mountain|24|1"),
    }),
    "AT": ("Austria", "Europe", "Western Europe", {
        "9": ("Vienna", "Vienna|temperate continental|26|2"),
        "8": ("Styria", "Graz|temperate continental|26|1"),
        "7": ("Tyrol", "Innsbruck|temperate mountain|25|1"),
        "4": ("Upper Austria", "Linz|temperate continental|25|1"),
    }),
    "SE": ("Sweden", "Europe", "Northern Europe", {
        "AB": ("Stockholm", "Stockholm|cold continental coastal|22|1,Solna|cold continental|22|1"),
        "O": ("Vastra Gotaland", "Gothenburg|temperate coastal|21|1,Boras|temperate|21|1"),
        "M": ("Skane", "Malmo|temperate coastal|22|1,Helsingborg|temperate coastal|21|1"),
    }),
    "NO": ("Norway", "Europe", "Northern Europe", {
        "03": ("Oslo", "Oslo|cold continental coastal|21|1"),
        "46": ("Vestland", "Bergen|temperate coastal|18|1"),
        "50": ("Trondelag", "Trondheim|cold coastal|19|1"),
    }),
    "DK": ("Denmark", "Europe", "Northern Europe", {
        "84": ("Capital Region", "Copenhagen|temperate coastal|21|2,Frederiksberg|temperate coastal|21|1"),
        "82": ("Central Jutland", "Aarhus|temperate coastal|20|1"),
    }),
    "FI": ("Finland", "Europe", "Northern Europe", {
        "18": ("Uusimaa", "Helsinki|cold continental coastal|21|1,Espoo|cold continental coastal|21|1,Vantaa|cold continental|21|1"),
        "06": ("Pirkanmaa", "Tampere|cold continental|21|1"),
    }),
    "PL": ("Poland", "Europe", "Eastern Europe", {
        "14": ("Masovian", "Warsaw|humid continental|25|2,Radom|humid continental|25|1"),
        "12": ("Lesser Poland", "Krakow|humid continental|25|2,Tarnow|humid continental|26|1"),
        "02": ("Lower Silesian", "Wroclaw|humid continental|26|2"),
        "24": ("Silesian", "Katowice|humid continental|25|1,Czestochowa|humid continental|25|1"),
        "22": ("Pomeranian", "Gdansk|temperate coastal|22|1,Gdynia|temperate coastal|22|1"),
    }),
    "CZ": ("Czechia", "Europe", "Eastern Europe", {
        "10": ("Prague", "Prague|humid continental|25|2"),
        "64": ("South Moravian", "Brno|humid continental|26|1"),
        "80": ("Moravian-Silesian", "Ostrava|humid continental|25|1"),
    }),
    "RO": ("Romania", "Europe", "Eastern Europe", {
        "B": ("Bucharest", "Bucharest|humid continental|29|2"),
        "CJ": ("Cluj", "Cluj-Napoca|humid continental|25|1"),
        "TM": ("Timis", "Timisoara|humid continental|28|1"),
    }),
    "GR": ("Greece", "Europe", "Southern Europe", {
        "I": ("Attica", "Athens|hot mediterranean|34|2,Piraeus|hot mediterranean coastal|33|1"),
        "B": ("Central Macedonia", "Thessaloniki|mediterranean|32|1"),
        "M": ("Crete", "Heraklion|hot mediterranean coastal|30|1,Chania|mediterranean coastal|29|1"),
    }),
    "TR": ("Turkey", "Asia", "Western Asia", {
        "34": ("Istanbul", "Istanbul|humid subtropical coastal|29|4"),
        "06": ("Ankara", "Ankara|hot continental|31|3"),
        "35": ("Izmir", "Izmir|hot mediterranean coastal|34|2"),
        "07": ("Antalya", "Antalya|hot mediterranean coastal|35|1,Alanya|hot mediterranean coastal|34|1"),
        "16": ("Bursa", "Bursa|mediterranean|31|2"),
    }),
    "IL": ("Israel", "Asia", "Western Asia", {
        "TA": ("Tel Aviv", "Tel Aviv|hot mediterranean coastal|31|1,Ramat Gan|hot mediterranean|32|1"),
        "JM": ("Jerusalem", "Jerusalem|mediterranean mountain|30|1"),
        "HA": ("Haifa", "Haifa|hot mediterranean coastal|31|1"),
    }),
    "ZA": ("South Africa", "Africa", "Southern Africa", {
        "GT": ("Gauteng", "Johannesburg|temperate subtropical highland|26|3,Pretoria|temperate subtropical highland|29|2,Sandton|temperate subtropical highland|26|1"),
        "WC": ("Western Cape", "Cape Town|mediterranean coastal|26|2,Stellenbosch|mediterranean|28|1"),
        "KZN": ("KwaZulu-Natal", "Durban|humid subtropical coastal|28|2,Umhlanga|humid subtropical coastal|27|1"),
    }),
    "NG": ("Nigeria", "Africa", "Western Africa", {
        "LA": ("Lagos", "Lagos|tropical humid coastal|31|4,Ikeja|tropical humid|32|1"),
        "FC": ("Abuja", "Abuja|tropical|33|2"),
        "RI": ("Rivers", "Port Harcourt|tropical humid coastal|30|1"),
    }),
    "KE": ("Kenya", "Africa", "Eastern Africa", {
        "110": ("Nairobi", "Nairobi|tropical highland|26|3"),
        "280": ("Mombasa", "Mombasa|tropical humid coastal|31|1"),
    }),
    "EG": ("Egypt", "Africa", "Northern Africa", {
        "C": ("Cairo", "Cairo|hot arid|36|4,Giza|hot arid|37|2,New Cairo|hot arid|36|1"),
        "ALX": ("Alexandria", "Alexandria|hot arid coastal|31|2"),
    }),
    "MA": ("Morocco", "Africa", "Northern Africa", {
        "07": ("Marrakech-Safi", "Marrakech|hot arid|38|1"),
        "06": ("Casablanca-Settat", "Casablanca|mediterranean coastal|28|3"),
        "04": ("Rabat-Sale-Kenitra", "Rabat|mediterranean coastal|28|1"),
        "05": ("Fes-Meknes", "Fes|hot mediterranean|37|1"),
    }),
    "GH": ("Ghana", "Africa", "Western Africa", {
        "AA": ("Greater Accra", "Accra|tropical humid coastal|31|3,Tema|tropical humid coastal|31|1"),
        "AH": ("Ashanti", "Kumasi|tropical humid|31|1"),
    }),
    "BR": ("Brazil", "Americas", "South America", {
        "SP": ("Sao Paulo", "Sao Paulo|humid subtropical|28|4,Campinas|humid subtropical|29|2,Santos|humid subtropical coastal|28|1,Sao Jose dos Campos|humid subtropical|29|1"),
        "RJ": ("Rio de Janeiro", "Rio de Janeiro|tropical humid coastal|32|3,Niteroi|tropical humid coastal|31|1"),
        "MG": ("Minas Gerais", "Belo Horizonte|tropical highland|28|2,Uberlandia|tropical|29|1"),
        "RS": ("Rio Grande do Sul", "Porto Alegre|humid subtropical|28|2,Caxias do Sul|humid subtropical|24|1"),
        "PR": ("Parana", "Curitiba|humid subtropical highland|25|2,Londrina|humid subtropical|29|1"),
        "BA": ("Bahia", "Salvador|tropical humid coastal|30|2,Feira de Santana|hot tropical|31|1"),
        "CE": ("Ceara", "Fortaleza|tropical humid coastal|31|2"),
        "PE": ("Pernambuco", "Recife|tropical humid coastal|30|2"),
    }),
    "MX": ("Mexico", "Americas", "Central America", {
        "CMX": ("Mexico City", "Mexico City|tropical highland|25|4"),
        "JAL": ("Jalisco", "Guadalajara|tropical highland|30|3,Zapopan|tropical highland|30|2"),
        "NLE": ("Nuevo Leon", "Monterrey|hot arid|36|3,San Nicolas de los Garza|hot arid|36|1"),
        "BC": ("Baja California", "Tijuana|mediterranean coastal|26|2,Mexicali|hot arid desert|42|1,Ensenada|mediterranean coastal|25|1"),
        "PUE": ("Puebla", "Puebla|tropical highland|27|2"),
        "QR": ("Quintana Roo", "Cancun|tropical humid coastal|33|1,Playa del Carmen|tropical humid coastal|33|1"),
        "CHH": ("Chihuahua", "Chihuahua|hot arid|34|1,Ciudad Juarez|hot arid desert|37|2"),
    }),
    "AR": ("Argentina", "Americas", "South America", {
        "C": ("Buenos Aires City", "Buenos Aires|humid subtropical|28|4"),
        "B": ("Buenos Aires Province", "La Plata|humid subtropical|28|1,Mar del Plata|temperate coastal|24|1"),
        "X": ("Cordoba", "Cordoba|humid subtropical|30|2"),
        "S": ("Santa Fe", "Rosario|humid subtropical|30|1,Santa Fe|humid subtropical|32|1"),
        "M": ("Mendoza", "Mendoza|arid mountain|32|1"),
    }),
    "CL": ("Chile", "Americas", "South America", {
        "RM": ("Santiago Metropolitan", "Santiago|mediterranean|30|4"),
        "VS": ("Valparaiso", "Valparaiso|mediterranean coastal|22|1,Vina del Mar|mediterranean coastal|22|1"),
        "BI": ("Biobio", "Concepcion|mediterranean|23|1"),
    }),
    "CO": ("Colombia", "Americas", "South America", {
        "DC": ("Bogota", "Bogota|tropical highland|20|4"),
        "ANT": ("Antioquia", "Medellin|tropical highland|28|2"),
        "VAC": ("Valle del Cauca", "Cali|tropical|30|2"),
        "ATL": ("Atlantico", "Barranquilla|tropical humid coastal|33|1"),
    }),
    "PE": ("Peru", "Americas", "South America", {
        "LIM": ("Lima", "Lima|arid coastal|26|4"),
        "ARE": ("Arequipa", "Arequipa|arid highland|23|1"),
        "CUS": ("Cusco", "Cusco|tropical highland|20|1"),
    }),
}


def parse_cities(raw: str) -> list[dict]:
    cities: list[dict] = []
    for chunk in (c.strip() for c in raw.split(",")):
        if not chunk:
            continue
        name, climate, summer, tier = chunk.split("|")
        cities.append(
            {
                "name": name.strip(),
                "climate": sorted(set(climate.split())),
                "avgSummerC": int(summer),
                "popTier": int(tier),
            }
        )
    return cities


def build() -> dict[str, dict]:
    countries: dict[str, dict] = {}
    for code, (name, region, subregion, states) in GEO.items():
        out_states = []
        for s_code, (s_name, raw) in states.items():
            cities = parse_cities(raw)
            for city in cities:
                for tag in city["climate"]:
                    if tag not in CLIMATE_TAGS:
                        raise ValueError(f"unknown climate tag {tag!r} in {code}/{s_code}")
            out_states.append({"code": s_code, "name": s_name, "cities": cities})
        out_states.sort(key=lambda s: s["name"])
        countries[code] = {
            "code": code,
            "name": name,
            "region": region,
            "subregion": subregion,
            "states": out_states,
        }
    return countries


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = build()
    for path in OUT_DIR.glob("*.json"):
        path.unlink()
    total_states = total_cities = 0
    for code, record in sorted(data.items()):
        total_states += len(record["states"])
        total_cities += sum(len(s["cities"]) for s in record["states"])
        (OUT_DIR / f"{code}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    index = {
        "countries": [
            {
                "code": c["code"],
                "name": c["name"],
                "region": c["region"],
                "subregion": c["subregion"],
                "stateCount": len(c["states"]),
                "cityCount": sum(len(s["cities"]) for s in c["states"]),
            }
            for c in sorted(data.values(), key=lambda c: c["name"])
        ]
    }
    (OUT_DIR / "_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"wrote {len(data)} countries, {total_states} states, {total_cities} cities -> {OUT_DIR}")


if __name__ == "__main__":
    main()
