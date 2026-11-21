"""Offline geography helpers: timezone for a place (country + coordinates), using the zone table that ships
with the `tzdata` package. No network, no large dependency."""
from __future__ import annotations

import math
import re
from functools import lru_cache


def _parse_coord(s: str) -> tuple[float, float] | None:
    m = re.match(r"^([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?$", s)
    if not m:
        return None
    lat = int(m.group(2)) + int(m.group(3)) / 60 + int(m.group(4) or 0) / 3600
    lon = int(m.group(6)) + int(m.group(7)) / 60 + int(m.group(8) or 0) / 3600
    return (lat if m.group(1) == "+" else -lat, lon if m.group(5) == "+" else -lon)


@lru_cache(maxsize=1)
def zone_table() -> dict[str, list[tuple[str, float, float]]]:
    """country code -> [(timezone, lat, lon)], most populous zone first."""
    out: dict[str, list[tuple[str, float, float]]] = {}
    try:
        import importlib.resources as res
        text = res.files("tzdata").joinpath("zoneinfo").joinpath("zone.tab").read_text(encoding="utf-8")
    except Exception:
        return out
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        coord = _parse_coord(parts[1])
        if coord:
            out.setdefault(parts[0].upper(), []).append((parts[2], coord[0], coord[1]))
    return out


def timezone_for(country: str | None, lat: float | None = None, lon: float | None = None,
                 fallback: str = "UTC") -> str:
    zones = zone_table().get((country or "").upper())
    if not zones:
        return fallback
    if lat is None or lon is None or len(zones) == 1:
        return zones[0][0]

    def dist(z):
        dlat = math.radians(z[1] - lat)
        dlon = math.radians(z[2] - lon)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat)) * math.cos(math.radians(z[1])) * math.sin(dlon / 2) ** 2
        return a
    return min(zones, key=dist)[0]


# ---------------------------------------------------------------------------
# World rollout: major business cities per country (most important first).
# Used when reach.world_rollout is on: your start area, then your country, then these countries in order.
# ---------------------------------------------------------------------------
WORLD_CITIES: dict[str, list[str]] = {
    "IN": ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Ahmedabad", "Chennai", "Kolkata", "Pune", "Surat", "Jaipur",
           "Lucknow", "Kanpur", "Nagpur", "Indore", "Thane", "Bhopal", "Visakhapatnam", "Vadodara", "Ludhiana", "Rajkot",
           "Nashik", "Coimbatore", "Kochi", "Chandigarh", "Gurugram", "Noida", "Mysuru", "Guwahati", "Bhubaneswar",
           "Dehradun", "Amritsar", "Madurai", "Vijayawada", "Raipur", "Ranchi", "Jodhpur", "Udaipur", "Goa", "Thiruvananthapuram",
           "Mangaluru", "Navi Mumbai", "Gandhinagar", "Jamnagar", "Bhavnagar", "Anand"],
    "AE": ["Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah", "Al Ain"],
    "GB": ["London", "Manchester", "Birmingham", "Leeds", "Glasgow", "Liverpool", "Bristol", "Edinburgh", "Sheffield",
           "Leicester", "Nottingham", "Newcastle upon Tyne", "Cardiff", "Belfast", "Southampton", "Brighton", "Reading",
           "Coventry", "Oxford", "Cambridge"],
    "US": ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio", "San Diego", "Dallas",
           "Austin", "Jacksonville", "San Jose", "Fort Worth", "Columbus", "Charlotte", "Indianapolis", "San Francisco",
           "Seattle", "Denver", "Nashville", "Oklahoma City", "Boston", "Las Vegas", "Portland", "Detroit", "Atlanta",
           "Miami", "Tampa", "Orlando", "Minneapolis", "Raleigh", "Sacramento", "Kansas City", "Salt Lake City",
           "Pittsburgh", "Cincinnati", "St. Louis", "Baltimore", "Milwaukee", "Albuquerque", "Tucson", "New Orleans",
           "Cleveland", "Honolulu", "Omaha", "Louisville", "Memphis", "Richmond", "Buffalo", "Boise"],
    "CA": ["Toronto", "Vancouver", "Montreal", "Calgary", "Edmonton", "Ottawa", "Winnipeg", "Mississauga", "Hamilton", "Halifax"],
    "AU": ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Gold Coast", "Canberra", "Newcastle", "Hobart", "Darwin"],
    "NZ": ["Auckland", "Wellington", "Christchurch", "Hamilton", "Tauranga"],
    "SG": ["Singapore"],
    "IE": ["Dublin", "Cork", "Galway", "Limerick"],
    "ZA": ["Johannesburg", "Cape Town", "Durban", "Pretoria", "Port Elizabeth"],
    "SA": ["Riyadh", "Jeddah", "Dammam", "Mecca", "Medina", "Khobar"],
    "QA": ["Doha"],
    "KW": ["Kuwait City"],
    "OM": ["Muscat"],
    "BH": ["Manama"],
    "MY": ["Kuala Lumpur", "Penang", "Johor Bahru", "Petaling Jaya"],
    "PH": ["Manila", "Quezon City", "Cebu City", "Davao City", "Makati"],
    "NG": ["Lagos", "Abuja", "Port Harcourt", "Ibadan"],
    "KE": ["Nairobi", "Mombasa"],
    "NL": ["Amsterdam", "Rotterdam", "The Hague", "Utrecht", "Eindhoven"],
    "FR": ["Paris", "Lyon", "Marseille", "Toulouse", "Nice", "Bordeaux"],
    "ES": ["Madrid", "Barcelona", "Valencia", "Seville", "Malaga"],
    "IT": ["Rome", "Milan", "Naples", "Turin", "Florence", "Bologna"],
    "DE": ["Berlin", "Hamburg", "Munich", "Cologne", "Frankfurt", "Stuttgart", "Düsseldorf"],
    "SE": ["Stockholm", "Gothenburg", "Malmö"],
    "PL": ["Warsaw", "Kraków", "Wrocław", "Gdańsk"],
    "PT": ["Lisbon", "Porto"],
    "MX": ["Mexico City", "Guadalajara", "Monterrey", "Cancún"],
    "BR": ["São Paulo", "Rio de Janeiro", "Belo Horizonte", "Brasília", "Curitiba"],
    "LK": ["Colombo"],
    "NP": ["Kathmandu"],
    "BD": ["Dhaka", "Chittagong"],
    "PK": ["Karachi", "Lahore", "Islamabad"],
    "ID": ["Jakarta", "Surabaya", "Bali"],
    "TH": ["Bangkok", "Phuket", "Chiang Mai"],
    "VN": ["Ho Chi Minh City", "Hanoi", "Da Nang"],
    "HK": ["Hong Kong"],
    "JP": ["Tokyo", "Osaka"],
}

COUNTRY_NAMES: dict[str, str] = {
    "IN": "India", "AE": "United Arab Emirates", "GB": "United Kingdom", "US": "United States", "CA": "Canada",
    "AU": "Australia", "NZ": "New Zealand", "SG": "Singapore", "IE": "Ireland", "ZA": "South Africa",
    "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait", "OM": "Oman", "BH": "Bahrain", "MY": "Malaysia",
    "PH": "Philippines", "NG": "Nigeria", "KE": "Kenya", "NL": "Netherlands", "FR": "France", "ES": "Spain",
    "IT": "Italy", "DE": "Germany", "SE": "Sweden", "PL": "Poland", "PT": "Portugal", "MX": "Mexico", "BR": "Brazil",
    "LK": "Sri Lanka", "NP": "Nepal", "BD": "Bangladesh", "PK": "Pakistan", "ID": "Indonesia", "TH": "Thailand",
    "VN": "Vietnam", "HK": "Hong Kong", "JP": "Japan",
}

# Default rollout order after your own country: English-speaking, high-budget markets first.
DEFAULT_COUNTRY_ORDER = ["AE", "GB", "US", "AU", "CA", "SG", "NZ", "IE", "SA", "QA", "KW", "OM", "BH", "ZA", "MY",
                         "PH", "NG", "KE", "NL", "LK", "NP", "BD", "HK"]


def country_code_for(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip().lower().rstrip(".")
    if len(n) == 2 and n.isalpha():
        return "GB" if n == "uk" else n.upper()
    for code, full in COUNTRY_NAMES.items():
        if full.lower() == n:
            return code
    aliases = {"uk": "GB", "england": "GB", "britain": "GB", "great britain": "GB", "usa": "US", "us": "US",
               "america": "US", "united states of america": "US", "uae": "AE", "emirates": "AE", "dubai": "AE",
               "bharat": "IN", "ksa": "SA", "south korea": "KR", "korea": "KR", "russia": "RU", "china": "CN",
               "canada": "CA", "germany": "DE", "deutschland": "DE"}
    if n in aliases:
        return aliases[n]
    try:  # any country name the timezone database knows
        import importlib.resources as res
        text = res.files("tzdata").joinpath("zoneinfo").joinpath("iso3166.tab").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line and not line.startswith("#"):
                code, _, full = line.partition("\t")
                if full.strip().lower() == n:
                    return code.upper()
    except Exception:
        pass
    return None
