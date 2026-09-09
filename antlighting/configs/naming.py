"""Turn noisy public remarks into clean, human-friendly server names.

Public share links carry remarks like ``"US 🇺🇸 | @Raydikalx | B5554D"``.
Users should see ``"United States #4"``.  Nothing here is authoritative — the
best effort is made and everything falls back gracefully.
"""

from __future__ import annotations

import re

# ISO 3166-1 alpha-2 -> (display name, ISO alpha-3-free short label)
COUNTRIES: dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "UK": "United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "NL": "Netherlands",
    "SE": "Sweden",
    "FI": "Finland",
    "NO": "Norway",
    "DK": "Denmark",
    "CH": "Switzerland",
    "AT": "Austria",
    "BE": "Belgium",
    "PL": "Poland",
    "ES": "Spain",
    "PT": "Portugal",
    "IT": "Italy",
    "IE": "Ireland",
    "CZ": "Czechia",
    "RO": "Romania",
    "HU": "Hungary",
    "BG": "Bulgaria",
    "GR": "Greece",
    "TR": "Turkey",
    "RU": "Russia",
    "UA": "Ukraine",
    "LT": "Lithuania",
    "LV": "Latvia",
    "EE": "Estonia",
    "IS": "Iceland",
    "CA": "Canada",
    "MX": "Mexico",
    "BR": "Brazil",
    "AR": "Argentina",
    "CL": "Chile",
    "IN": "India",
    "PK": "Pakistan",
    "BD": "Bangladesh",
    "JP": "Japan",
    "KR": "South Korea",
    "CN": "China",
    "HK": "Hong Kong",
    "TW": "Taiwan",
    "SG": "Singapore",
    "MY": "Malaysia",
    "ID": "Indonesia",
    "TH": "Thailand",
    "VN": "Vietnam",
    "PH": "Philippines",
    "AU": "Australia",
    "NZ": "New Zealand",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "IL": "Israel",
    "ZA": "South Africa",
    "EG": "Egypt",
    "MA": "Morocco",
    "KR2": "South Korea",
}

_FLAG_BASE = 0x1F1E6
_ASCII_A = ord("A")

# Common aliases used inside remarks.
_NAME_ALIASES: dict[str, str] = {
    "usa": "US",
    "united states": "US",
    "us": "US",
    "america": "US",
    "uk": "GB",
    "britain": "GB",
    "england": "GB",
    "london": "GB",
    "germany": "DE",
    "deutschland": "DE",
    "frankfurt": "DE",
    "holland": "NL",
    "netherlands": "NL",
    "amsterdam": "NL",
    "france": "FR",
    "paris": "FR",
    "sweden": "SE",
    "finland": "FI",
    "norway": "NO",
    "denmark": "DK",
    "switzerland": "CH",
    "austria": "AT",
    "belgium": "BE",
    "poland": "PL",
    "spain": "ES",
    "portugal": "PT",
    "italy": "IT",
    "ireland": "IE",
    "romania": "RO",
    "turkey": "TR",
    "turkiye": "TR",
    "russia": "RU",
    "ukraine": "UA",
    "lithuania": "LT",
    "latvia": "LV",
    "estonia": "EE",
    "iceland": "IS",
    "canada": "CA",
    "mexico": "MX",
    "brazil": "BR",
    "brasil": "BR",
    "india": "IN",
    "pakistan": "PK",
    "japan": "JP",
    "tokyo": "JP",
    "korea": "KR",
    "singapore": "SG",
    "malaysia": "MY",
    "indonesia": "ID",
    "thailand": "TH",
    "vietnam": "VN",
    "philippines": "PH",
    "australia": "AU",
    "sydney": "AU",
    "zealand": "NZ",
    "dubai": "AE",
    "emirates": "AE",
    "saudi": "SA",
    "israel": "IL",
    "africa": "ZA",
    "egypt": "EG",
    "morocco": "MA",
    "china": "CN",
    "hongkong": "HK",
    "taiwan": "TW",
}

_FLAG_RE = re.compile(
    "[" + chr(_FLAG_BASE) + "-" + chr(_FLAG_BASE + 25) + "]{2}"
)
_CHANNEL_RE = re.compile(r"@([A-Za-z0-9_]{3,32})")
_HEX_TAG_RE = re.compile(r"\b[0-9A-Fa-f]{6}\b")
_CODE_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(COUNTRIES)) + r")(?![A-Za-z])")
_NOISE_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")


def country_from_flag(flag: str) -> str | None:
    """Convert a two-character regional-indicator flag emoji to an ISO code."""
    if len(flag) != 2:
        return None
    letters = ""
    for ch in flag:
        code = ord(ch) - _FLAG_BASE
        if not 0 <= code < 26:
            return None
        letters += chr(_ASCII_A + code)
    return letters.upper()


def _detect_country(text: str) -> str | None:
    flag = _FLAG_RE.search(text)
    if flag:
        code = country_from_flag(flag.group(0))
        if code and code in COUNTRIES:
            return code
        if code:
            return code
    low = text.lower()
    for alias, code in _NAME_ALIASES.items():
        if re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", low):
            return code
    match = _CODE_RE.search(text)
    if match:
        return match.group(1)
    return None


def _detect_provider(text: str) -> str | None:
    match = _CHANNEL_RE.search(text)
    return match.group(1) if match else None


def normalise_remark(remark: str, address: str) -> tuple[str, str | None, str | None, str | None]:
    """Return ``(display_name, country, country_code, provider)``."""
    remark = (remark or "").strip()
    if not remark:
        return "", None, None, None

    code = _detect_country(remark)
    provider = _detect_provider(remark)
    country = COUNTRIES.get(code) if code else None

    cleaned = _NOISE_RE.sub(" ", remark)
    cleaned = _FLAG_RE.sub(" ", cleaned)
    # Drop trailing hash tags and channel handles.
    cleaned = _CHANNEL_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[#|/\\]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|")
    cleaned = _HEX_TAG_RE.sub("", cleaned).strip(" -_|")

    if not cleaned and code:
        cleaned = COUNTRIES.get(code, code)
    elif code and cleaned:
        # Strip the leading country token so "US | Raydikalx | B5554D" -> "".
        without_code = re.sub(r"(?<![A-Za-z])" + code + r"(?![A-Za-z])", " ", cleaned)
        without_code = re.sub(r"\s+", " ", without_code).strip(" -_|")
        if not without_code:
            cleaned = ""
        else:
            cleaned = without_code

    name = cleaned if cleaned else (COUNTRIES.get(code, code) if code else "")
    return name.strip(), country, code, provider


def friendly_name(country: str | None, country_code: str | None, index: int, scheme: str) -> str:
    """Build the fallback ``Germany #3`` style label."""
    base = country or (country_code.title() if country_code else "")
    if base:
        return f"{base} #{index}"
    return f"{scheme.upper()} #{index}"


def flag_for(country_code: str | None) -> str:
    """Regional-indicator flag emoji for an ISO code, or a globe fallback."""
    if not country_code or len(country_code) != 2:
        return "\U0001F310"
    code = country_code.upper()
    if not code.isalpha():
        return "\U0001F310"
    return "".join(chr(_FLAG_BASE + ord(c) - _ASCII_A) for c in code)
