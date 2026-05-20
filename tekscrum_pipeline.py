"""
╔══════════════════════════════════════════════════════════════════════╗
║        TEKSCRUM PIPELINE  —  n8n Edition  v4.0  (FINAL)             ║
║                                                                      ║
║  DESIGNED FOR n8n:                                                   ║
║  n8n handles scheduling, looping, Sheets reads/writes,               ║
║  Smartlead CSV upload, and Slack notifications.                      ║
║                                                                      ║
║  This script does ONE thing: receives a single lead as JSON,         ║
║  runs all enrichment (GMB via Places API, PageSpeed, website         ║
║  scrape, Gemini email generation), and returns the result            ║
║  as a single JSON object on stdout.                                  ║
║                                                                      ║
║  n8n calls it via Execute Command node:                              ║
║    python tekscrum_pipeline.py '{"first_name":"Robert",...}'         ║
║                                                                      ║
║  n8n reads the result with:                                          ║
║    JSON.parse($('Execute Command').item.json.stdout)                 ║
║                                                                      ║
║  Exit codes:                                                         ║
║    0 — success (including QC-flagged results — still usable)         ║
║    1 — hard error (Gemini failed, bad input, missing API key)        ║
║                                                                      ║
║  INSTALL (one-time):                                                 ║
║    pip install requests beautifulsoup4 google-generativeai           ║
║               python-dotenv                                          ║
║                                                                      ║
║  REQUIRED .env FILE (same folder as this script):                   ║
║    GEMINI_API_KEY=your_key_here                                      ║
║    PLACES_API_KEY=your_key_here                                      ║
║    PAGESPEED_API_KEY=your_key_here                                   ║
║    SENDER_NAME=Alex                                                  ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import sys
import json
import os
import re
import random
import time
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ══════════════════════════════════════════════════════════════════════
# CONFIG — loaded from .env (never hardcode keys in this file)
# ══════════════════════════════════════════════════════════════════════

load_dotenv(Path(__file__).parent / ".env")

GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_KEY_2  = os.getenv("GEMINI_API_KEY_2", "")
GEMINI_API_KEY_3  = os.getenv("GEMINI_API_KEY_3", "")
GEMINI_API_KEY_4  = os.getenv("GEMINI_API_KEY_4", "")
GEMINI_API_KEY_5  = os.getenv("GEMINI_API_KEY_5", "")
GEMINI_API_KEY_6  = os.getenv("GEMINI_API_KEY_6", "")
GEMINI_API_KEY_7  = os.getenv("GEMINI_API_KEY_7", "")
GEMINI_API_KEY_8  = os.getenv("GEMINI_API_KEY_8", "")
PLACES_API_KEY    = os.getenv("PLACES_API_KEY", "")
PAGESPEED_API_KEY = os.getenv("PAGESPEED_API_KEY", "")
SENDER_NAME       = os.getenv("SENDER_NAME", "Alex")

# ══════════════════════════════════════════════════════════════════════
# LOGGING — file only. stdout is RESERVED for the JSON result.
# Anything written to stdout other than the final JSON will break n8n.
# ══════════════════════════════════════════════════════════════════════

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(
            f"logs/pipeline_{datetime.now().strftime('%Y-%m')}.log",
            encoding="utf-8"
        )
    ]
)
log = logging.getLogger("tekscrum")

# ══════════════════════════════════════════════════════════════════════
# MOBILE USER AGENTS
# Rotate to reduce scrape blocking
# ══════════════════════════════════════════════════════════════════════

MOBILE_UAS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/116.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 Chrome/112.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; moto g(60)) AppleWebKit/537.36 Chrome/112.0.0.0 Mobile Safari/537.36",
]

# ══════════════════════════════════════════════════════════════════════
# GEMINI SYSTEM PROMPT — Funeral Home / Cremation Specialist
# Dignity-first, human-sounding cold emails. Zero dashes allowed.
# ══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are an elite cold email copywriter for TekScrum, a web design agency
that builds websites specifically for funeral homes and cremation services
in the US and UK.

Your job: read the prospect data, identify ONE specific problem, and write
a cold email that reads like a real person noticed something, not a tool
that ran an audit.

━━━ THE CORE PHILOSOPHY ━━━

Funeral directors are experienced business owners who have heard every
sales pitch. They do not respond to flattery, grief language, or urgency
tactics. They respond to someone who noticed something real and is being
straight with them about it.

The tone is: observant peer. Not vendor. Not consultant. Not bot.
Warm enough to feel human. Direct enough to feel honest.

The warmth comes from the opening and the bridge.
The directness comes from the pain line.
Never mix these up.

━━━ CRITICAL TONE RULES ━━━

NEVER sound like this:
"I noticed your website has several issues that could be impacting your
ability to serve families in their time of need."

ALWAYS sound like this:
"Your site takes around 4-5 seconds to come up on a phone. Long enough
that people tend to move on before they have even seen what you offer."

The difference: one is clinical and transactional.
The other is human, specific, and practical.

Do NOT frame the problem as lost traffic or lost leads in emotional
terms. Frame it as a practical, fixable observation. The owner should
feel informed, not criticised.

━━━ HUMANISING NUMBERS ━━━

If mobile_load_time is a precise decimal (e.g. 4.2, 7.8, 9.1):
Never use the raw decimal. Always round to natural speech.
Under 5 seconds: "around 4-5 seconds" or "just over 4 seconds"
5 to 7 seconds: "around 5-6 seconds" or "closer to 6 seconds"
7 to 14 seconds: "nearly 8 seconds" or "close to 10 seconds"
15 seconds or over: "well over 15 seconds" or "closer to [rounded] seconds"
The goal: it sounds like something a human clocked, not a tool.

If review_count is a precise number:
Use it naturally: "only 8 reviews" or "just 12 Google reviews"
Never: "your review count of 12 is below the threshold"

━━━ BUSINESS NAME RULE ━━━

Use the business name maximum twice. Opening line and CTA only.
Between those two points: never. Not once.
In the CTA, always use the FULL business name exactly as provided in
the input. Never shorten, truncate, or abbreviate it.
Never place the niche word directly next to the business name.
"Meadowview Funeral Home funeral home" sounds like a mail-merge. Avoid it.

━━━ COUNTRY-SPECIFIC PRICING ANGLE ━━━

UK prospects:
UK funeral directors are legally required under CMA rules to display
their price list on their website. Frame it as:
"UK funeral directors are required to display their pricing online. It
looks like the site does not have that yet, which puts it outside the
CMA guidelines."
Never say "you are breaking the law." Say "outside the guidelines" or
"not yet compliant." Factual, not accusatory.

US prospects:
Online pricing is not yet law in the US. Frame it as a competitive
advantage only:
"Most funeral homes in [City] still do not show prices online. The ones
that do tend to get the call first from families comparing options."
Never imply it is legally required for US prospects. It is not.

━━━ PAIN POINT PRIORITY ━━━

Pick the FIRST that applies. Skip any where data is "unknown".

P1: No website at all
P2: Website fails mobile (is_mobile_ready = false)
P3: Mobile load time over 4 seconds
P4: No pricing page on website
    UK: frame as CMA compliance issue
    US: frame as competitive advantage
P5: No online pre-planning or contact form
P6: Not in Google top 3 for city and niche
P7: Fewer than 10 reviews or rating below 4.0
P8: Website design outdated (pre-2019 era)
P9: No SSL or no clear CTA above the fold

━━━ SUBJECT LINE ━━━

One line. Under 8 words. All lowercase. No exclamation mark. No dashes.
No question marks. No colons. Never start with "your website."
Never generic. Never salesy. Never clever in an obvious way.

The subject must feel like it was written by a person who knows the city
and spotted something specific. It should look like a forwarded note from
a colleague, not a campaign. The owner should open it out of mild
curiosity, not because they were promised something.

Match the subject to the pain point used. Do not reference "funeral" or
"funeral home" in the subject line, ever. It signals a mass campaign.

NEVER use any of these patterns or openings:
"quick question", "your website", "just wanted", "following up",
"idea for", "website audit", "website feedback", "free", "improve",
"boost", """

# ══════════════════════════════════════════════════════════════════════
# GMB — Google Places API
# Uses the official API for reliable, structured data.
# Falls back gracefully if PLACES_API_KEY is not set.
# ══════════════════════════════════════════════════════════════════════

def find_gmb_data(business_name, city, state, country):
    """
    Fetches GMB rating, review count, and a top review theme
    using the Google Places Text Search + Place Details API.
    Returns 'unknown' for all fields if the API key is missing
    or the business is not found — Gemini handles unknowns gracefully.
    """
    result = {
        "gmb_rating":       "unknown",
        "gmb_review_count": "unknown",
        "gmb_review_theme": "",
        "gmb_found":        False,
        "gmb_link":         "",
        "gmb_reviews_text": "",
    }

    if not business_name or not PLACES_API_KEY:
        log.debug("GMB skipped: no business name or Places API key")
        return result

    query = f"{business_name} {city} {state} {country}".strip()
    log.info(f"  GMB Places search: {query}")

    try:
        # Step 1 — Text search to find the place
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": query, "key": PLACES_API_KEY},
            timeout=10,
        )
        data = r.json()

        if data.get("status") != "OK" or not data.get("results"):
            log.info(f"  GMB: not found (status={data.get('status')})")
            return result

        place = data["results"][0]
        result["gmb_rating"]       = str(place.get("rating", "unknown"))
        result["gmb_review_count"] = str(place.get("user_ratings_total", "unknown"))
        result["gmb_found"]        = True

        # Step 2 — Place Details to fetch top review text and maps link
        place_id = place.get("place_id")
        if place_id:
            dr = requests.get(
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={
                    "place_id": place_id,
                    "fields":   "reviews,url",
                    "key":      PLACES_API_KEY,
                },
                timeout=10,
            )
            details = dr.json().get("result", {})
            result["gmb_link"] = details.get("url", "")
            
            reviews = details.get("reviews", [])
            if reviews:
                # Use the highest-rated review as the "theme"
                top = max(reviews, key=lambda rv: rv.get("rating", 0))
                result["gmb_review_theme"] = top.get("text", "")[:120]
                
                # Collect up to 3 reviews for the prompt
                texts = [rv.get("text", "").replace("\n", " ") for rv in reviews[:3]]
                result["gmb_reviews_text"] = " | ".join(texts)[:500]

        log.info(f"  GMB: ⭐{result['gmb_rating']} ({result['gmb_review_count']} reviews)")

    except Exception as e:
        log.warning(f"  GMB Places API error: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════
# WEBSITE SCRAPER
# Checks for signals that map directly to pain points P1–P9.
# Uses a mobile user agent so mobile_ready check is realistic.
# ══════════════════════════════════════════════════════════════════════

def scrape_website(url):
    """
    Scrapes the prospect's website for the key signals Gemini needs.
    Extracts: page title, meta description, services, phone numbers,
    address clues, social media links, and pain point signals.
    Uses desktop UA fallback if mobile UA gets blocked.
    """
    result = {
        "has_website":      False,
        "has_ssl":          False,
        "has_form":         False,
        "has_cta":          False,
        "has_pricing_page": False,
        "is_mobile_ready":  False,
        "design_era":       "unknown",
        "other_issues":     "",
        "page_title":       "",
        "meta_description": "",
        "services_found":   "",
        "phone_on_site":    "",
        "address_on_site":  "",
        "social_links":     "",
        "page_text_snippet": "",
        "pages_scraped":    0,
    }

    url = str(url or "").strip()
    if not url or url.lower() in ("", "nan", "n/a", "none", "-"):
        log.info("  Website: none provided")
        return result

    if not url.startswith("http"):
        url = "https://" + url

    result["has_website"] = True
    result["has_ssl"]     = url.startswith("https://")

    # Try mobile UA first, fallback to desktop if blocked
    user_agents = [
        random.choice(MOBILE_UAS),
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    ]

    resp = None
    for ua in user_agents:
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"},
                timeout=12,
                allow_redirects=True,
            )
            if resp.status_code == 200 and len(resp.text) > 200:
                break
        except requests.exceptions.SSLError:
            result["has_ssl"] = False
            result["other_issues"] = "SSL certificate error"
            log.warning(f"  Site: SSL error on {url}")
            return result
        except requests.exceptions.ConnectionError:
            continue
        except requests.exceptions.Timeout:
            continue
        except Exception:
            continue

    if not resp or resp.status_code != 200 or len(resp.text) < 200:
        result["has_website"]  = False
        result["other_issues"] = f"Website unreachable or empty"
        log.warning(f"  Site: bad response — {url}")
        return result

    try:
        from urllib.parse import urljoin
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # ── Deep Scraper: Find internal pages (Contact, About, Pricing) ──
        to_crawl = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].lower()
            text = a_tag.get_text(strip=True).lower()
            if any(k in href or k in text for k in ["contact", "about", "price", "pricing", "cost"]):
                full_url = urljoin(resp.url, a_tag["href"])
                if full_url.startswith(resp.url) and full_url != resp.url:
                    to_crawl.add(full_url)
        
        to_crawl = list(to_crawl)[:3] # Max 3 extra pages
        extra_html_texts = []
        
        if to_crawl:
            def fetch_extra(u):
                try:
                    res = requests.get(u, headers={"User-Agent": user_agents[0]}, timeout=8)
                    if res.status_code == 200:
                        return res.text
                except:
                    pass
                return ""
                
            with ThreadPoolExecutor(max_workers=3) as ex:
                extra_html_texts = list(ex.map(fetch_extra, to_crawl))
        
        # Combine HTML for text searches
        combined_html_lower = resp.text.lower() + " " + " ".join(extra_html_texts).lower()
        combined_text_raw = resp.text + " " + " ".join(extra_html_texts)
        result["pages_scraped"] = 1 + len([t for t in extra_html_texts if t])

        result["has_ssl"] = resp.url.startswith("https://")

        # ── Page title ───────────────────────────────────────────────
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            result["page_title"] = title_tag.string.strip()[:150]

        # ── Meta description ─────────────────────────────────────────
        meta_desc = soup.find("meta", attrs={"name": re.compile("description", re.I)})
        if meta_desc and meta_desc.get("content"):
            result["meta_description"] = meta_desc["content"].strip()[:300]
        if not result["meta_description"]:
            og_desc = soup.find("meta", attrs={"property": "og:description"})
            if og_desc and og_desc.get("content"):
                result["meta_description"] = og_desc["content"].strip()[:300]

        # ── P2 — mobile viewport ─────────────────────────────────────
        result["is_mobile_ready"] = bool(
            soup.find("meta", {"name": re.compile("viewport", re.I)})
        )

        # ── P4 — form detection (use raw html for keywords) ──────────
        booking_kw = ["book", "schedule", "appointment", "enquir", "inquiry",
                      "contact", "quote", "request", "reserve", "get in touch",
                      "free estimate", "consultation"]
        
        # Also parse the extra pages to check for form tags
        extra_soups = [BeautifulSoup(ext, "html.parser") for ext in extra_html_texts if ext]
        has_form_tag = bool(soup.find_all("form")) or any(bool(s.find_all("form")) for s in extra_soups)
        
        result["has_form"] = (
            has_form_tag or
            any(kw in combined_html_lower for kw in booking_kw)
        )

        # ── P4 — Pricing page detection ────────────────────────────────
        pricing_kw = ["price", "pricing", "cost", "fee", "tariff",
                      "price list", "our prices", "funeral costs",
                      "cremation costs", "service charges", "payment"]
        pricing_in_links = False
        for a_tag in soup.find_all("a", href=True):
            link_text = a_tag.get_text(strip=True).lower()
            link_href = a_tag["href"].lower()
            if any(pk in link_text or pk in link_href for pk in pricing_kw):
                pricing_in_links = True
                break
        result["has_pricing_page"] = (
            pricing_in_links or
            any(pk in combined_html_lower for pk in ["price list", "our prices", "pricing",
                                      "funeral costs", "cremation costs"])
        )

        # ── P9 — CTA above fold ──────────────────────────────────────
        top_html = resp.text[:4000].lower()
        cta_kw = ["call us", "contact us", "book", "get a quote", "free quote",
                  "schedule", "enquire", "inquire", "get started", "call now",
                  "speak to us", "request a", "free estimate", "get in touch"]
        result["has_cta"] = any(kw in top_html for kw in cta_kw)

        # ── Services/headings ────────────────────────────────────────
        service_keywords = []
        for heading in soup.find_all(["h1", "h2", "h3"]):
            txt = heading.get_text(strip=True)
            if txt and 3 < len(txt) < 100:
                service_keywords.append(txt)
        result["services_found"] = " | ".join(service_keywords[:10]) if service_keywords else "none detected"

        # ── Social media links ───────────────────────────────────────
        social_domains = ["facebook.com", "instagram.com", "twitter.com",
                          "x.com", "linkedin.com", "youtube.com", "yelp.com",
                          "tiktok.com"]
        social_found = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].lower()
            for sd in social_domains:
                if sd in href:
                    social_found.add(sd.split(".")[0])
        result["social_links"] = ", ".join(sorted(social_found)) if social_found else "none found"

        # ── Phone numbers (from raw text, not soup) ──────────────────
        phone_matches = re.findall(
            r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}",
            combined_text_raw
        )
        if phone_matches:
            unique_phones = list(dict.fromkeys(phone_matches))
            result["phone_on_site"] = ", ".join(unique_phones[:2])

        # ── Address (from raw text) ──────────────────────────────────
        addr_pattern = re.findall(
            r"\d{1,5}\s+[A-Za-z0-9\s.,#-]{5,60}(?:St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Rd|Road|Ln|Lane|Way|Ct|Court|Pl|Place)[.,]?\s*[A-Za-z\s]+,?\s*[A-Z]{2}\s*\d{5}",
            combined_text_raw
        )
        if addr_pattern:
            result["address_on_site"] = addr_pattern[0].strip()[:200]

        # ── Visible page text (use a COPY — never decompose original soup)
        soup_copy = BeautifulSoup(resp.text, "html.parser")
        for tag in soup_copy(["script", "style", "nav", "noscript", "iframe"]):
            tag.decompose()
        visible_text = soup_copy.get_text(separator=" ", strip=True)
        visible_text = re.sub(r"\s+", " ", visible_text).strip()
        result["page_text_snippet"] = visible_text[:600]

        # ── P7 — design era ──────────────────────────────────────────
        years = [
            int(y) for y in re.findall(r"©\s*(\d{4})", combined_text_raw)
            if 1990 < int(y) < 2030
        ]
        if years:
            yr = max(years)
            if yr <= 2016:   result["design_era"] = "very outdated (pre-2016)"
            elif yr <= 2019: result["design_era"] = "outdated (2016–2019)"
            elif yr <= 2022: result["design_era"] = "dated (2020–2022)"
            else:            result["design_era"] = "modern (2023+)"
        else:
            modern = ["tailwind", "nextjs", "nuxt", "bootstrap/5", "bootstrap@5",
                      "react", "vue", "gatsby", "astro"]
            old    = ["bootstrap/2", "bootstrap/3", "jquery/1.", "jquery-1.",
                      "jquery-2.", "jquery-1.6", "jquery-1.7", "jquery-1.8"]
            if any(s in combined_html_lower for s in modern):
                result["design_era"] = "modern (2021+)"
            elif any(s in combined_html_lower for s in old):
                result["design_era"] = "outdated (pre-2018)"
            else:
                result["design_era"] = "unknown"

        log.info(
            f"  Site: SSL={result['has_ssl']} "
            f"Form={result['has_form']} "
            f"Mobile={result['is_mobile_ready']} "
            f"Era={result['design_era']} "
            f"Title={result['page_title'][:50]}"
        )

    except Exception as e:
        result["other_issues"] = str(e)[:80]
        log.warning(f"  Site: parse error — {e}")

    return result


# ══════════════════════════════════════════════════════════════════════
# PAGESPEED API
# Google's official mobile performance API. Free tier: 25,000/day.
# ══════════════════════════════════════════════════════════════════════

def get_pagespeed(url):
    """
    Returns mobile Time-to-Interactive (load time in seconds)
    and overall Lighthouse performance score (0–100).
    Returns 'unknown' for both if API key is missing or call fails.
    """
    result = {"mobile_load_time": "unknown", "mobile_score": "unknown"}

    if not url or not PAGESPEED_API_KEY:
        return result

    url = str(url).strip()
    if not url.startswith("http"):
        url = "https://" + url

    try:
        r = requests.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params={"url": url, "strategy": "mobile", "key": PAGESPEED_API_KEY},
            timeout=40,
        )
        data  = r.json()
        lr    = data.get("lighthouseResult", {})
        audits = lr.get("audits", {})

        # Time to Interactive — best proxy for "felt" mobile load time
        tti = audits.get("interactive", {}).get("numericValue")
        if tti is not None:
            result["mobile_load_time"] = round(tti / 1000, 1)

        # Overall Lighthouse performance score
        score = lr.get("categories", {}).get("performance", {}).get("score")
        if score is not None:
            result["mobile_score"] = int(score * 100)

        log.info(
            f"  PageSpeed: {result['mobile_load_time']}s "
            f"score={result['mobile_score']}/100"
        )

    except Exception as e:
        log.warning(f"  PageSpeed error: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════
# GEMINI — with exponential backoff retry
# Uses gemini-1.5-flash for speed and cost efficiency.
# 3 attempts with delays [5s, 15s, 45s] before giving up.
# ══════════════════════════════════════════════════════════════════════

def call_gemini(user_msg, attempts=3):
    """
    Dual API Key caller with automatic fallback.
    Tries Key 1 first, if credits exhausted switches to Key 2.
    Uses gemini-2.5-flash (fastest) with fallback models.
    """
    # Build list of API key clients to try
    clients_to_try = []
    if GEMINI_API_KEY:
        clients_to_try.append(("Key_1", genai.Client(api_key=GEMINI_API_KEY)))
    if GEMINI_API_KEY_2:
        clients_to_try.append(("Key_2", genai.Client(api_key=GEMINI_API_KEY_2)))
    if GEMINI_API_KEY_3:
        clients_to_try.append(("Key_3", genai.Client(api_key=GEMINI_API_KEY_3)))
    if GEMINI_API_KEY_4:
        clients_to_try.append(("Key_4", genai.Client(api_key=GEMINI_API_KEY_4)))
    if GEMINI_API_KEY_5:
        clients_to_try.append(("Key_5", genai.Client(api_key=GEMINI_API_KEY_5)))
    if GEMINI_API_KEY_6:
        clients_to_try.append(("Key_6", genai.Client(api_key=GEMINI_API_KEY_6)))
    if GEMINI_API_KEY_7:
        clients_to_try.append(("Key_7", genai.Client(api_key=GEMINI_API_KEY_7)))
    if GEMINI_API_KEY_8:
        clients_to_try.append(("Key_8", genai.Client(api_key=GEMINI_API_KEY_8)))

    if not clients_to_try:
        raise Exception("No GEMINI_API_KEY found in .env file!")

    models_to_try = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-flash-latest",
    ]

    last_err = "Unknown"

    for client_name, client in clients_to_try:
        log.info(f"  Trying {client_name}...")
        key_exhausted = False

        for model_name in models_to_try:
            if key_exhausted:
                break

            for i in range(attempts):
                try:
                    config = types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.7,
                    )
                    response = client.models.generate_content(
                        model=model_name,
                        contents=user_msg,
                        config=config,
                    )
                    log.info(f"  ✅ Success! Model: {model_name} ({client_name})")
                    return response.text.strip()

                except Exception as e:
                    last_err = str(e)
                    err_lower = last_err.lower()
                    log.warning(f"  ❌ {client_name} {model_name} attempt {i+1}: {err_lower[:120]}")

                    # Credits exhausted — skip to next API key immediately
                    if "prepayment" in err_lower or "quota" in err_lower or "resource_exhausted" in err_lower:
                        log.warning(f"  {client_name} credits exhausted, switching to next key...")
                        key_exhausted = True
                        break

                    # Model not available — try next model
                    if "404" in err_lower or "not found" in err_lower or "permission" in err_lower:
                        break

                    # Rate limit — wait and retry
                    if "429" in err_lower and "resource_exhausted" not in err_lower:
                        time.sleep(5 * (i + 1))
                        continue

                    if i < attempts - 1:
                        time.sleep(2)

        log.warning(f"  {client_name} exhausted. Trying next key...")

    raise Exception(f"All Gemini API keys failed. Last error: {last_err[:200]}")


def generate_email(lead, site, speed, gmb, learning_prompt=""):
    """
    Builds the user message with all collected data and calls Gemini.
    Includes self-learning insights from past leads.
    Parses the structured output into individual fields.
    """
    country = lead.get('country', 'US').upper()

    user_msg = f"""Write a cold email for this prospect.

PROSPECT:
Business name    : {lead.get('business_name', 'unknown')}
Owner first name : {lead.get('first_name', 'unknown')}
Niche            : {lead.get('niche', 'funeral home')}
City             : {lead.get('city', 'unknown')}
State / Region   : {lead.get('state', 'unknown')}
Country          : {country}
Sender name      : {SENDER_NAME}

GMB DATA (Google Places API):
Rating           : {gmb.get('gmb_rating', 'unknown')} stars
Review count     : {gmb.get('gmb_review_count', 'unknown')}
Top review theme : {gmb.get('gmb_review_theme', 'not available')}
Recent reviews   : {gmb.get('gmb_reviews_text', 'none')}
GMB data found   : {gmb.get('gmb_found', False)}

WEBSITE AUDIT:
Has website      : {site.get('has_website', False)}
Has SSL (https)  : {site.get('has_ssl', False)}
Mobile ready     : {site.get('is_mobile_ready', False)}
Has pricing page : {site.get('has_pricing_page', False)}
Has contact form : {site.get('has_form', False)}
Has CTA at top   : {site.get('has_cta', False)}
Design era       : {site.get('design_era', 'unknown')}
Issues noted     : {site.get('other_issues', 'none')}

WEBSITE CONTENT:
Page title       : {site.get('page_title', 'unknown')}
Meta description : {site.get('meta_description', 'unknown')}
Services/headings: {site.get('services_found', 'none detected')}
Page text snippet: {site.get('page_text_snippet', 'none available')}
Phone on site    : {site.get('phone_on_site', 'not found')}
Address on site  : {site.get('address_on_site', 'not found')}
Social media     : {site.get('social_links', 'none found')}

PAGESPEED (mobile):
Load time (s)    : {speed.get('mobile_load_time', 'unknown')}
Score (0-100)    : {speed.get('mobile_score', 'unknown')}
{learning_prompt}

Remember: follow ALL system prompt rules exactly. Zero dashes. Max 130 words.
Country is {country}. Apply the correct country-specific pricing rule if P4 is used."""

    raw = call_gemini(user_msg)

    # Parse structured output
    subject_m    = re.search(r"SUBJECT:\s*(.+?)(?:\n|$)", raw)
    body_m       = re.search(r"EMAIL:\s*\n(.*?)(?:\nWORD_COUNT:|$)", raw, re.DOTALL)
    word_count_m = re.search(r"WORD_COUNT:\s*(\d+)", raw)
    pain_m       = re.search(r"PAIN_USED:\s*(P\d+)", raw)
    country_m    = re.search(r"COUNTRY_RULE_APPLIED:\s*(US|UK)", raw)
    bio_m        = re.search(r"BUSINESS_BIO:\s*(.*?)(?:\n|$)", raw, re.DOTALL)

    body = body_m.group(1).strip() if body_m else raw

    # Remove any "Subject: ..." prefix that Gemini might have included in the body text itself
    body = re.sub(r"(?i)^subject:\s*.+?\n+", "", body).strip()

    return {
        "email_subject": (
            subject_m.group(1).strip().strip('"').strip("'")
            if subject_m
            else f"quick question about {lead.get('business_name', 'your business')}"
        ),
        "email_body":       body,
        "word_count":       int(word_count_m.group(1)) if word_count_m else len(body.split()),
        "pain_used":        pain_m.group(1) if pain_m else "unknown",
        "country_rule":     country_m.group(1) if country_m else country,
        "business_bio":     bio_m.group(1).strip() if bio_m else "Not generated.",
    }


# ══════════════════════════════════════════════════════════════════════
# SELF-LEARNING SYSTEM
# Stores insights from each processed lead in learning.json.
# Before generating each email, past insights are loaded and injected
# into the Gemini prompt so it improves over time.
# ══════════════════════════════════════════════════════════════════════

LEARNING_FILE = Path(__file__).parent / "learning.json"

def load_learnings():
    """Load accumulated learning data from past leads."""
    default = {
        "total_processed": 0,
        "total_passed_qc": 0,
        "avg_word_count": 0,
        "pain_performance": {},     # {"P3": {"used": 10, "passed": 8}}
        "best_email_by_pain": {},   # {"P3": "email text..."}
        "recent_qc_failures": [],   # [{"issue": "...", "count": 2}]
        "niche_performance": {},    # {"Funeral service": {"used": 10, "passed": 9}}
        "tips_learned": [],         # auto-generated strategy tips
    }
    try:
        if LEARNING_FILE.exists():
            with open(LEARNING_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Upgrade legacy pain_counts to advanced pain_performance
                if "pain_counts" in data and "pain_performance" not in data:
                    for k, v in data["pain_counts"].items():
                        default["pain_performance"][k] = {"used": v, "passed": v}
                
                # Merge with defaults for any missing or new keys
                for k, v in default.items():
                    if k not in data:
                        data[k] = v
                return data
    except Exception as e:
        log.warning(f"  Learning: could not load — {e}")
    return default


def save_learning(learnings, email_result, qc_issues, lead_data):
    """Update advanced learning data after processing a lead."""
    try:
        learnings["total_processed"] += 1
        pain = email_result.get("pain_used", "unknown")
        niche = lead_data.get("niche", "unknown")
        body = email_result.get("email_body", "")
        
        # Initialize nested structures if missing
        if pain not in learnings["pain_performance"]:
            learnings["pain_performance"][pain] = {"used": 0, "passed": 0}
        if niche not in learnings["niche_performance"]:
            learnings["niche_performance"][niche] = {"used": 0, "passed": 0}

        learnings["pain_performance"][pain]["used"] += 1
        learnings["niche_performance"][niche]["used"] += 1

        if not qc_issues:
            learnings["total_passed_qc"] += 1
            learnings["pain_performance"][pain]["passed"] += 1
            learnings["niche_performance"][niche]["passed"] += 1
            
            # Save the BEST email specifically for this pain point (always keep the shortest/punchiest one)
            current_best = learnings["best_email_by_pain"].get(pain, "")
            if not current_best or len(body) < len(current_best):
                learnings["best_email_by_pain"][pain] = body

        # Rolling average word count
        wc = email_result.get("word_count", 0)
        n = learnings["total_processed"]
        old_avg = learnings["avg_word_count"]
        learnings["avg_word_count"] = round(((old_avg * (n - 1)) + wc) / n, 1)

        # Track advanced QC issues
        if qc_issues:
            for issue in qc_issues:
                found = False
                for failure in learnings["recent_qc_failures"]:
                    if failure["issue"] == issue:
                        failure["count"] += 1
                        found = True
                        break
                if not found:
                    learnings["recent_qc_failures"].append({"issue": issue, "count": 1})
            
            # Sort by frequency and keep top 10
            learnings["recent_qc_failures"].sort(key=lambda x: x["count"], reverse=True)
            learnings["recent_qc_failures"] = learnings["recent_qc_failures"][:10]

        # Intelligent Strategy Tips Generation
        pass_rate = (learnings["total_passed_qc"] / n * 100) if n > 0 else 0
        tips = []
        
        # Tip 1: Pain Point Win Rates
        if learnings["pain_performance"]:
            best_pain = max(learnings["pain_performance"].items(), 
                            key=lambda x: (x[1]["passed"]/x[1]["used"] if x[1]["used"] > 0 else 0, x[1]["used"]))
            if best_pain[1]["used"] >= 3:
                rate = (best_pain[1]["passed"] / best_pain[1]["used"]) * 100
                tips.append(f"Highest converting angle is {best_pain[0]} with a {rate:.0f}% QC pass rate.")

        # Tip 2: Word Count Optimization
        if learnings["avg_word_count"] > 115:
            tips.append(f"Current avg word count is {learnings['avg_word_count']}. Emails perform better when kept closer to 90-100 words.")

        learnings["tips_learned"] = tips

        with open(LEARNING_FILE, "w", encoding="utf-8") as f:
            json.dump(learnings, f, indent=4, ensure_ascii=False)

        log.info(f"  Learning: advanced json saved (pass_rate={pass_rate:.0f}%, pain={pain})")

    except Exception as e:
        log.warning(f"  Learning: could not save — {e}")


def get_learning_prompt(learnings, current_pain_likely=None):
    """Generate advanced learning insights to strictly guide Gemini."""
    if learnings["total_processed"] < 1:
        return ""

    n = learnings["total_processed"]
    pass_rate = (learnings["total_passed_qc"] / n * 100) if n > 0 else 0

    lines = [f"\n━━━ ADVANCED SYSTEM LEARNINGS (Based on {n} previous emails) ━━━"]
    lines.append(f"Global QC Pass Rate: {pass_rate:.0f}%")
    
    if learnings["tips_learned"]:
        lines.append("Active Strategies:")
        for tip in learnings["tips_learned"]:
            lines.append(f"  • {tip}")

    if learnings["recent_qc_failures"]:
        lines.append("\nCRITICAL: Avoid these recent Quality Control failures:")
        for failure in learnings["recent_qc_failures"][:3]:
            lines.append(f"  • {failure['issue']} (Flagged {failure['count']} times)")

    # Provide a highly contextual example based on what is likely needed
    if learnings["best_email_by_pain"]:
        lines.append("\nPREVIOUS HIGH-PERFORMING EMAIL EXAMPLE:")
        lines.append("Analyze this structure, but write something EVEN MORE natural and conversational:")
        
        # Try to show an example of a random successful email, or specific if known
        example_email = list(learnings["best_email_by_pain"].values())[-1]
        lines.append(f"--- Top Example ---")
        lines.append(example_email)
        lines.append("-------------------")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# QUALITY CHECK
# Runs after email generation. Issues are reported in output JSON
# so n8n can route flagged emails to a review sheet automatically.
# ══════════════════════════════════════════════════════════════════════

BANNED_PHRASES = [
    "hope this email finds",
    "i am writing to",
    "my name is",
    "i work for",
    "synergy",
    "reach out",
    "touch base",
    "circle back",
    "leverage",
    "game-changer",
    "excited to share",
    "excited",
    "revolutionize",
    "kindly",
    "i hope you",
    "solutions",
    "i wanted to",
    "just following up",
    "families in need",
    "grieving families",
    "in their time of loss",
    "during difficult moments",
    "at a difficult time",
    "i hope this email finds you",
    "book a call",
    "schedule time",
    "lets connect",
    "would love to chat",
    "drop me a line",
]

def quality_check(body, word_count):
    """
    Returns a list of issues found.
    Empty list = PASSED. Any items = REVIEW NEEDED.
    Updated for funeral home prompt: 130 word max, zero dashes.
    """
    issues = []
    bl = body.lower()

    if word_count > 130:
        issues.append(f"over 130 words ({word_count}w)")
    if word_count < 50:
        issues.append(f"too short ({word_count}w, possible generation error)")

    for phrase in BANNED_PHRASES:
        if phrase in bl:
            issues.append(f"banned phrase: '{phrase}'")

    if body.count("!") > 1:
        issues.append("more than 1 exclamation mark")

    # Check for any dash characters (em dash, en dash)
    if "\u2014" in body or "\u2013" in body:
        issues.append("contains dash character (em/en dash not allowed)")

    # Check for unfilled placeholders left by Gemini
    if re.search(r"\[(?!BusinessName|FirstName)[A-Z][A-Z\s]+\]", body):
        issues.append("unfilled placeholder detected in body")

    if "GENERATION_ERROR" in body:
        issues.append("Gemini API error, regenerate this lead")

    # Business name should appear max twice
    # (We can't perfectly check this without the name, but flag obvious issues)

    return issues


# ══════════════════════════════════════════════════════════════════════
# AUTO COUNTRY DETECTION
# Detects US vs UK from phone, domain, or address patterns.
# ══════════════════════════════════════════════════════════════════════

def auto_detect_country(lead, site):
    """Detect country from available signals if not provided."""
    country = lead.get("country", "").strip().upper()
    if country in ("US", "UK", "GB"):
        return "UK" if country == "GB" else country

    # Check website domain
    website = lead.get("website", "").lower()
    if ".co.uk" in website or ".org.uk" in website:
        return "UK"

    # Check phone format
    phone = lead.get("phone", "") or site.get("phone_on_site", "")
    if phone.startswith("+44") or phone.startswith("044"):
        return "UK"

    # Check address for UK postcode pattern (e.g. SW1A 2AA)
    addr = site.get("address_on_site", "")
    if re.search(r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}", addr, re.I):
        return "UK"

    # Default to US
    return "US"


# ══════════════════════════════════════════════════════════════════════
# MAIN — entry point called by n8n Execute Command node
# Reads one lead from argv[1] as JSON string.
# Prints one result to stdout as JSON string.
# All logging goes to file — stdout is reserved for JSON only.
# ══════════════════════════════════════════════════════════════════════

def parse_cli_json():
    """Parse JSON from CLI args. Handles PowerShell quoting issues."""
    if len(sys.argv) <= 1:
        return {}

    # PowerShell sometimes splits JSON across multiple args
    raw = " ".join(sys.argv[1:])

    # Strip surrounding single quotes (PowerShell literal strings)
    raw = raw.strip()
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1]

    return json.loads(raw)


def main():

    # ── Validate API key ──────────────────────────────────────────────
    if not GEMINI_API_KEY:
        print(json.dumps({
            "status": "ERROR",
            "error":  "GEMINI_API_KEY not set in .env file"
        }))
        sys.exit(1)

    # ── Parse lead from CLI argument (PowerShell-safe) ───────────────
    try:
        lead = parse_cli_json()
    except (json.JSONDecodeError, IndexError) as e:
        print(json.dumps({"status": "ERROR", "error": f"Invalid JSON input: {e}"}))
        sys.exit(1)

    if not lead.get("email"):
        print(json.dumps({"status": "ERROR", "error": "Lead missing required 'email' field"}))
        sys.exit(1)

    biz = lead.get("business_name", "Unknown")
    log.info(f"━━━ Processing: {biz}, {lead.get('city', '')} ━━━")

    try:
        start_time = time.time()
        url = lead.get("website", "")

        # ── PARALLEL DATA GATHERING ──────────────────────────────────
        site  = {}
        speed = {"mobile_load_time": "unknown", "mobile_score": "unknown"}
        gmb   = {"gmb_rating": "unknown", "gmb_review_count": "unknown",
                 "gmb_review_theme": "", "gmb_found": False}

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_site = executor.submit(scrape_website, url)
            future_gmb  = executor.submit(
                find_gmb_data,
                lead.get("business_name", ""),
                lead.get("city", ""),
                lead.get("state", ""),
                lead.get("country", "US"),
            )
            future_speed = None
            if url and url.lower() not in ("", "nan", "n/a", "none", "-"):
                future_speed = executor.submit(get_pagespeed, url)

            site = future_site.result()
            gmb  = future_gmb.result()
            if future_speed:
                speed = future_speed.result()
            elif not site.get("has_website"):
                speed = {"mobile_load_time": "N/A, no website", "mobile_score": "N/A"}

        gather_time = round(time.time() - start_time, 1)
        log.info(f"  Data gathered in {gather_time}s (parallel)")

        # ── Auto-detect country if not provided ──────────────────────
        detected_country = auto_detect_country(lead, site)
        if not lead.get("country"):
            lead["country"] = detected_country
            log.info(f"  Country auto-detected: {detected_country}")

        # ── Generate email with QC auto-retry ────────────────────────
        learnings = load_learnings()
        learning_prompt = get_learning_prompt(learnings)
        log.info(f"  Calling Gemini... (learnings from {learnings['total_processed']} past leads)")

        max_retries = 3
        retry_count = 0
        email = None
        issues = []
        qc_status = "PASSED"

        for attempt in range(max_retries):
            retry_feedback = ""
            if attempt > 0:
                retry_feedback = (
                    f"\n\nPREVIOUS ATTEMPT FAILED QC. Issues: {'; '.join(issues)}\n"
                    f"Rewrite the email avoiding these issues. This is attempt {attempt + 1} of {max_retries}."
                )
                retry_count = attempt

            email = generate_email(lead, site, speed, gmb, learning_prompt + retry_feedback)
            issues = quality_check(email["email_body"], email["word_count"])
            qc_status = "PASSED" if not issues else "REVIEW: " + " | ".join(issues)

            log.info(
                f"  Attempt {attempt + 1}: pain={email['pain_used']} "
                f"words={email['word_count']} QC={qc_status}"
            )

            if not issues:
                break  # QC passed, no need to retry

        # ── Save learnings ───────────────────────────────────────────
        save_learning(learnings, email, issues, lead)

        total_time = round(time.time() - start_time, 1)
        log.info(f"  Total time: {total_time}s")

        # ── Build output JSON ─────────────────────────────────────────
        output = {
            # Pipeline result
            "status":           "DONE",
            "email_subject":    email["email_subject"],
            "email_body":       email["email_body"],
            "word_count":       email["word_count"],
            "pain_used":        email["pain_used"],
            "country_rule":     email.get("country_rule", detected_country),
            "qc_status":        qc_status,
            "retry_count":      retry_count,
            "processing_time":  f"{total_time}s",
            "leads_processed":  learnings["total_processed"],
            "business_bio":     email.get("business_bio", ""),
            "pages_scraped":    str(site.get("pages_scraped", 0)),
            # Enrichment data
            "gmb_rating":       gmb["gmb_rating"],
            "gmb_reviews":      gmb["gmb_review_count"],
            "gmb_review_theme": gmb.get("gmb_review_theme", ""),
            "gmb_link":         gmb.get("gmb_link", ""),
            "mobile_load_time": str(speed["mobile_load_time"]),
            "mobile_score":     str(speed["mobile_score"]),
            "has_form":         str(site["has_form"]),
            "has_pricing_page": str(site.get("has_pricing_page", False)),
            "has_ssl":          str(site["has_ssl"]),
            "design_era":       site["design_era"],
            "page_title":       site.get("page_title", ""),
            "meta_description": site.get("meta_description", ""),
            "services_found":   site.get("services_found", ""),
            "phone_on_site":    site.get("phone_on_site", ""),
            "address_on_site":  site.get("address_on_site", ""),
            "social_links":     site.get("social_links", ""),
            "processed_at":     datetime.now().strftime("%Y-%m-%d %H:%M"),
            # Original lead fields
            "first_name":       lead.get("first_name", ""),
            "business_name":    lead.get("business_name", ""),
            "email":            lead.get("email", ""),
            "city":             lead.get("city", ""),
            "state":            lead.get("state", ""),
            "country":          lead.get("country", ""),
            "niche":            lead.get("niche", ""),
            "phone":            lead.get("phone", ""),
            "website":          lead.get("website", ""),
            "row_number":       lead.get("row_number", ""),
        }

        print(json.dumps(output, ensure_ascii=False))
        sys.exit(0)

    except Exception as e:
        log.error(f"  Pipeline error: {e}", exc_info=True)
        print(json.dumps({
            "status":       "ERROR",
            "error":        str(e),
            "email":        lead.get("email", ""),
            "business_name": lead.get("business_name", ""),
            "row_number":   lead.get("row_number", ""),
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()
