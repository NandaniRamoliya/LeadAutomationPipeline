"""The Auditor: reads a business website (no AI) and records concrete, checkable problems ("signals"),
emails and decision-makers. Every signal carries its evidence so nothing in an email is invented."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..discovery import USER_AGENT, is_social, normalize_domain

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; " + USER_AGENT + ")",
           "Accept-Language": "en"}
EMAIL_FIND_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,24}")
BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
BOOKING_HINTS = ["book now", "book an appointment", "book appointment", "book online", "book a table", "book a visit",
                 "booking", "reserve a table", "reserve now", "reservation", "schedule an appointment", "schedule a visit", "appointment request",
                 "calendly.com", "setmore", "acuityscheduling", "simplybook", "practo.com", "zocdoc", "opentable",
                 "fresha.com", "vagaro", "booksy", "appointy", "cal.com/", "youcanbook.me", "picktime", "zenoti",
                 "square.site", "squareup.com/appointments", "resy.com", "dineout", "eazydiner", "mindbody"]
ANALYTICS_HINTS = ["googletagmanager.com", "google-analytics.com", "gtag(", "plausible.io", "matomo", "clarity.ms",
                   "hotjar", "analytics.js", "umami"]
ADS_HINTS = ["googleadservices.com", "gtag('config', 'aw-", "gtag(\"config\", \"aw-", "send_to': 'aw-", "connect.facebook.net",
             "fbq(", "snap.licdn.com", "ads-twitter.com"]
CHAT_HINTS = ["wa.me/", "api.whatsapp.com", "whatsapp://", "tawk.to", "intercom", "crisp.chat", "livechatinc", "zendesk",
              "tidio", "drift.com", "freshchat", "hubspot.com/conversations", "chatwoot", "jivosite", "messenger"]
HIRING_HINTS = ["careers", "we're hiring", "we are hiring", "join our team", "vacancies", "job openings", "/jobs",
                "current openings", "work with us"]
SUBPAGE_HINTS = ["contact", "about", "team", "our-team", "people", "staff", "doctors", "founder", "careers", "jobs",
                 "reach-us", "get-in-touch", "who-we-are", "leadership", "blog", "news"]
# Ordering of sub-pages: people + contact pages first, blog last.
SUBPAGE_RANK = ["contact", "team", "about", "people", "staff", "doctors", "founder", "leadership", "who-we-are",
                "reach-us", "get-in-touch", "careers", "jobs", "blog", "news"]
AGENCY_RE = re.compile(r"(?:website|site|web design|designed|developed|built|created|crafted|made|powered|maintained|"
                       r"managed|marketing|seo)\s*(?:&\s*\w+\s*)?(?:by|with)\s*:?\s*$", re.I)
PLATFORMS = {"wordpress", "wix", "shopify", "squarespace", "godaddy", "weebly", "webflow", "joomla", "drupal",
             "blogger", "jimdo", "hostinger", "zoho", "google", "cloudflare", "bootstrap", "elementor", "divi",
             "themeforest", "astra", "oceanwp", "hubspot", "duda", "strikingly", "carrd", "framer", "ghost", "github"}
TECH_HINTS = {"WordPress": ["wp-content", "wp-includes"], "Wix": ["static.wixstatic.com", "wix.com"],
              "Squarespace": ["squarespace.com", "static1.squarespace"], "Shopify": ["cdn.shopify.com", "myshopify"],
              "Webflow": ["webflow.com", "assets.website-files.com"], "GoDaddy Builder": ["img1.wsimg.com", "godaddysites"],
              "Weebly": ["weebly.com"], "Joomla": ["/media/jui/", "joomla"], "Drupal": ["drupal.js", "/sites/default/files"],
              "Google Sites": ["sites.google.com"], "Zoho Sites": ["zohositescontent", "zoho.com/sites"]}
DIY_BUILDERS = {"Wix", "GoDaddy Builder", "Weebly", "Google Sites", "Zoho Sites"}
ATS_RE = {
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_\-]+)", re.I),
    "lever": re.compile(r"jobs\.lever\.co/([A-Za-z0-9_\-]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_\-.%]+)", re.I),
    "workable": re.compile(r"apply\.workable\.com/([A-Za-z0-9_\-]+)", re.I),
}
STARTUP_RE = re.compile(r"\b(?:backed by|part of|alumni of|went through|graduated from|funded by)\s+(?:the\s+)?"
                        r"(Y Combinator|Techstars|500 Startups|500 Global|Antler|Sequoia(?: Surge)?|Entrepreneur First|"
                        r"Plug and Play|a16z)\b|\(\s*(?:YC|Y Combinator)\s+([WSF]\d{2})\s*\)", re.I)
FUNDING_RE = re.compile(r"\b(?:we(?:'ve| have)? raised|announces?|closed|secured)\s+(?:a\s+)?(?:\$|₹|€|£|US\$|INR\s?|USD\s?)"
                        r"\s?([\d.,]+\s?(?:k|m|mn|million|cr|crore|b|bn|billion)?)\s+(pre-seed|seed|series [a-e]|funding round|round)", re.I)
BLOG_DATE_RE = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+((?:19|20)\d{2})\b|"
                          r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+((?:19|20)\d{2})\b|"
                          r"\b((?:19|20)\d{2})-\d{2}-\d{2}\b", re.I)
ROLE_WORDS = (r"Founder|Co-Founder|Cofounder|Owner|Co-Owner|CEO|Chief Executive Officer|Managing Director|Director|"
              r"Principal|Proprietor|Managing Partner|Partner|Head Chef|Chef-Owner|Medical Director|Clinic Director|"
              r"General Manager|Practice Manager")
NAME = r"(?:Dr\.?\s+)?[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z'\-]+){1,2}"
PERSON_RE_1 = re.compile(rf"({NAME})\s*(?:,|–|-|\||\(|:)\s*({ROLE_WORDS})\b")
PERSON_RE_2 = re.compile(rf"\b({ROLE_WORDS})\s*(?:,|–|-|\||:|&)?\s*({NAME})")
DR_RE = re.compile(r"\bDr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z'\-]+){0,2})")
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:[^0-9]{0,20})((?:19|20)\d{2})(?:\s*[-–]\s*((?:19|20)\d{2}))?", re.I)
NOT_NAMES = {"Our Team", "About Us", "Contact Us", "Read More", "Learn More", "Book Now", "Privacy Policy", "Terms Of"}


@dataclass
class SiteAudit:
    url: str | None
    final_url: str | None = None
    ok: bool = False
    status_code: int | None = None
    load_seconds: float | None = None
    signals: dict = field(default_factory=dict)
    emails: list[str] = field(default_factory=list)
    people: list[dict] = field(default_factory=list)
    socials: dict = field(default_factory=dict)
    phones: list[str] = field(default_factory=list)
    tech: str | None = None
    summary: str = ""
    pages_read: int = 0
    error: str | None = None


def decode_cfemail(hexstr: str) -> str:
    """Cloudflare 'email protection' obfuscation."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


def _fetch(session: requests.Session, url: str, timeout: float) -> tuple[requests.Response | None, float, str | None]:
    t0 = time.monotonic()
    try:
        resp = session.get(url, timeout=timeout, headers=HEADERS, allow_redirects=True)
        if "charset" not in resp.headers.get("content-type", "").lower():
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp, time.monotonic() - t0, None
    except requests.RequestException as exc:
        return None, time.monotonic() - t0, f"{type(exc).__name__}"


AT_RE = re.compile(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", re.I)
DOT_RE = re.compile(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", re.I)


def deobfuscate(text: str) -> str:
    """'info [at] clinic [dot] com' -> 'info@clinic.com'"""
    return DOT_RE.sub(".", AT_RE.sub("@", text))


def extract_emails(html: str, soup: BeautifulSoup) -> list[str]:
    found: list[str] = []
    for a in soup.select("a[href^=mailto]"):
        addr = a.get("href", "")[7:].split("?")[0].strip()
        if addr:
            found.append(addr)
    for el in soup.select("[data-cfemail]"):
        found.append(decode_cfemail(el.get("data-cfemail", "")))
    text = deobfuscate(soup.get_text(" ", strip=True))
    found += EMAIL_FIND_RE.findall(text)
    found += EMAIL_FIND_RE.findall(html)
    clean: list[str] = []
    for e in found:
        e = e.strip().strip(".,;:").lower()
        if not e or e.endswith(BAD_EMAIL_SUFFIX) or "@2x" in e or e.count("@") != 1 or len(e) > 80:
            continue
        if e not in clean:
            clean.append(e)
    return clean


def extract_people(text: str, html: str) -> list[dict]:
    people: dict[str, dict] = {}

    def add(name: str, role: str) -> None:
        name = re.sub(r"\s+", " ", name).strip(" ,.-")
        if not name or name in NOT_NAMES or len(name) > 40 or len(name.split()) > 4:
            return
        people.setdefault(name, {"full_name": name, "role": role})

    for m in PERSON_RE_1.finditer(text):
        add(m.group(1), m.group(2))
    for m in PERSON_RE_2.finditer(text):
        add(m.group(2), m.group(1))
    # JSON-LD founders / employees
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict):
                continue
            for key, role in (("founder", "Founder"), ("employee", "Team"), ("author", "Owner")):
                v = it.get(key)
                for p in (v if isinstance(v, list) else [v]):
                    if isinstance(p, dict) and p.get("name"):
                        add(str(p["name"]), str(p.get("jobTitle") or role))
    if not people:
        for m in DR_RE.finditer(text):
            add("Dr. " + m.group(1), "Doctor")
            if len(people) >= 3:
                break
    return list(people.values())[:5]


def audit_website(url: str | None, timeout: float = 12, max_pages: int = 5,
                  this_year: int | None = None) -> SiteAudit:
    this_year = this_year or datetime.now().year
    audit = SiteAudit(url=url)
    if not url:
        audit.signals["no_website"] = "no website listed"
        return audit
    if is_social(url):
        audit.signals["instagram_only"] = urlparse(url if "://" in url else "http://" + url).netloc
        audit.signals["no_website"] = "social media page only"
        return audit
    if "://" not in url:
        url = "https://" + url

    session = requests.Session()
    resp, secs, err = _fetch(session, url, timeout)
    if resp is None and url.startswith("https://"):
        http_url = "http://" + url[len("https://"):]
        resp, secs, err = _fetch(session, http_url, timeout)
    if resp is None:
        audit.error = err
        audit.signals["site_broken"] = f"could not load ({err})"
        return audit
    audit.status_code = resp.status_code
    audit.final_url = resp.url
    audit.load_seconds = round(secs, 1)
    if resp.status_code >= 400:
        audit.signals["site_broken"] = f"HTTP {resp.status_code}"
        return audit
    audit.ok = True
    if resp.url.startswith("http://"):
        audit.signals["no_https"] = "served over plain http"
    if secs > 3.0:
        audit.signals["slow_site"] = {"seconds": round(secs, 1)}

    html_pages = [resp.text or ""]
    soup = BeautifulSoup(resp.text or "", "html.parser")
    home_soup = soup
    base_domain = normalize_domain(resp.url)
    # Follow a few useful internal pages (contact, about, team, careers)
    links: list[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        full = urljoin(resp.url, href)
        if normalize_domain(full) != base_domain or full in links or full.rstrip("/") == resp.url.rstrip("/"):
            continue
        label = (href + " " + a.get_text(" ", strip=True)).lower()
        if any(h in label for h in SUBPAGE_HINTS):
            links.append(full.split("#")[0])
    def _rank(u: str) -> int:
        low_u = u.lower()
        return next((i for i, h in enumerate(SUBPAGE_RANK) if h in low_u), len(SUBPAGE_RANK))
    links.sort(key=_rank)
    for link in links[: max(max_pages - 1, 0)]:
        r, _, _ = _fetch(session, link, timeout)
        if r is not None and r.ok and "html" in r.headers.get("content-type", "html"):
            html_pages.append(r.text or "")
    audit.pages_read = len(html_pages)

    all_html = "\n".join(html_pages)
    low = all_html.lower()
    soups = [BeautifulSoup(h, "html.parser") for h in html_pages]
    texts = [s.get_text(" ", strip=True) for s in soups]
    all_text = "\n".join(texts)

    if not home_soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}):
        audit.signals["not_mobile_friendly"] = "no mobile viewport tag"
    years = [int(y2 or y1) for y1, y2 in COPYRIGHT_RE.findall(texts[0] if texts else "")]
    if years and max(years) < this_year - 2:
        audit.signals["outdated_site"] = {"copyright_year": f"© {max(years)}"}
    if not any(h in low for h in BOOKING_HINTS):
        audit.signals["no_online_booking"] = "no booking button or booking tool found"
    has_form = any(s.find("form") and (s.find("textarea") or s.find("input", attrs={"type": re.compile("email|tel", re.I)}))
                   for s in soups)
    if not has_form:
        audit.signals["no_contact_form"] = "no enquiry form found"
    if any(h in low for h in ADS_HINTS):
        audit.signals["running_ads"] = "ad conversion tracking found"
    if not any(h in low for h in ANALYTICS_HINTS):
        audit.signals["no_analytics"] = "no analytics script found"
    if any(h in low for h in HIRING_HINTS):
        audit.signals["hiring"] = "careers/jobs page found"
    if not any(h in low for h in CHAT_HINTS):
        audit.signals["no_live_chat"] = "no chat/WhatsApp button found"
    title = (home_soup.title.get_text(strip=True) if home_soup.title else "")
    desc_tag = home_soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = desc_tag.get("content", "").strip() if desc_tag else ""
    if not title or not desc:
        audit.signals["weak_seo"] = "missing page title" if not title else "missing meta description"

    emails: list[str] = []
    for h, s in zip(html_pages, soups):
        for e in extract_emails(h, s):
            if e not in emails:
                emails.append(e)
    audit.emails = emails
    audit.people = extract_people(all_text, all_html)

    # ---- channels: social profiles, WhatsApp, phone numbers -------------------
    from ..channels import extract_socials
    hrefs = [a.get("href", "") for sp in soups for a in sp.select("a[href]")]
    audit.socials = extract_socials(hrefs)
    phones = []
    for h in hrefs:
        if h.lower().startswith("tel:"):
            num = h[4:].strip()
            if num and num not in phones:
                phones.append(num)
    audit.phones = phones[:3]

    # ---- who built it / what it's built with -------------------------------------
    for name, hints in TECH_HINTS.items():
        if any(x in low for x in hints):
            audit.tech = name
            break
    if audit.tech in DIY_BUILDERS:
        audit.signals["diy_builder"] = {"builder": audit.tech}
    agency = find_agency(home_soup)
    if agency:
        audit.signals["agency_built"] = {"agency": agency}

    # ---- SEO / content -------------------------------------------------------------
    if "application/ld+json" not in low and "itemtype=" not in low:
        audit.signals["no_schema_markup"] = "no structured data (schema.org)"
    for h, sp, t in zip(html_pages[1:], soups[1:], texts[1:]):
        if any(k in (sp.title.get_text(" ", strip=True).lower() if sp.title else "") for k in ("blog", "news", "article")) \
                or "/blog" in h[:3000].lower():
            years = [int(next(y for y in g if y)) for g in BLOG_DATE_RE.findall(t)]
            if years and max(years) < this_year - 1:
                audit.signals["blog_stale"] = {"last_post_year": str(max(years))}
            break

    # ---- hiring (job boards their site links to) ------------------------------------
    roles = ats_roles(all_html, timeout)
    if roles:
        audit.signals["hiring"] = {"roles": ", ".join(roles[:3]), "open_roles": len(roles)}
    elif "hiring" in audit.signals:
        audit.signals["hiring"] = "careers/jobs page found"

    # ---- startup signals (only the company's own words) ---------------------------------
    m = STARTUP_RE.search(all_text)
    if m:
        audit.signals["startup_backed"] = {"backer": m.group(1) or f"Y Combinator {m.group(2)}"}
    m = FUNDING_RE.search(all_text)
    if m:
        audit.signals["recently_funded"] = {"round": f"{m.group(2)} ({m.group(1)})"}
    audit.summary = " | ".join(x for x in [title, desc, (texts[0] if texts else "")[:600]] if x)[:1200]
    return audit


def find_agency(soup: BeautifulSoup) -> str | None:
    """'Website by Pixel Studio' in the footer -> 'Pixel Studio'. Only credited, labelled links count."""
    footer = soup.find("footer") or soup
    for a in footer.select("a[href]"):
        name = a.get_text(" ", strip=True)
        if not name or len(name) > 40 or name.lower() in PLATFORMS or any(p in name.lower() for p in PLATFORMS):
            continue
        before = ""
        prev = a.previous_sibling
        hops = 0
        while prev is not None and hops < 3 and len(before) < 80:
            before = (prev.get_text(" ", strip=True) if hasattr(prev, "get_text") else str(prev)) + " " + before
            prev = prev.previous_sibling
            hops += 1
        if not before.strip() and a.parent is not None:
            before = a.parent.get_text(" ", strip=True).split(name)[0]
        if AGENCY_RE.search(before.strip()[-60:]):
            return name
    return None


def ats_roles(html: str, timeout: float = 10) -> list[str]:
    """Open roles from a job board the company's own site links to (Greenhouse, Lever, Ashby, Workable)."""
    feeds = {
        "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
        "lever": "https://api.lever.co/v0/postings/{t}?mode=json",
        "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}",
        "workable": "https://apply.workable.com/api/v1/widget/accounts/{t}",
    }
    for board, rx in ATS_RE.items():
        m = rx.search(html)
        if not m:
            continue
        try:
            r = requests.get(feeds[board].format(t=m.group(1)), timeout=timeout, headers=HEADERS)
            if not r.ok:
                return []
            data = r.json()
        except (requests.RequestException, ValueError):
            return []
        if board == "greenhouse":
            return [j.get("title", "") for j in data.get("jobs", []) if j.get("title")]
        if board == "lever":
            return [j.get("text", "") for j in (data if isinstance(data, list) else []) if j.get("text")]
        if board == "ashby":
            return [j.get("title", "") for j in data.get("jobs", []) if j.get("title")]
        if board == "workable":
            return [j.get("title", "") for j in data.get("jobs", []) if j.get("title")]
    return []
