import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="DB Builder", page_icon="📇", layout="wide", initial_sidebar_state="expanded")

# ============================================================
# ROLE GROUPS  (your original groups + a few new ones)
# To add a designation type, add a group here and give it priorities below.
# ============================================================
ROLE_GROUPS = {
    "Corporate Communications": [
        "Corporate Communications", "Corporate Communication", "Communications",
        "Communication", "External Communications", "Internal Communications",
        "Executive Communications"],
    "PR / Media": [
        "Public Relations", "PR", "Media Relations", "Media", "Press",
        "Press Relations", "Public Affairs"],
    "Marketing": [
        "Marketing", "Marketing Head", "Head of Marketing", "VP Marketing",
        "Director Marketing", "Brand Marketing", "Growth Marketing", "CMO",
        "Chief Marketing Officer"],
    "Founder's / CEO Office": [
        "Founder's Office", "Founders Office", "Founder Office", "CEO Office",
        "Office of the CEO", "Chief of Staff", "Executive Office",
        "Executive Assistant to CEO", "Assistant to CEO"],
    "Partnerships / Business Development": [
        "Partnerships", "Strategic Partnerships", "Business Development",
        "Business Development Head", "Head of Partnerships", "Partnerships Head",
        "Strategic Relations", "Alliances"],
    "Corporate Affairs": [
        "Corporate Affairs", "Government Relations", "Government Affairs",
        "External Affairs", "Institutional Relations", "Public Policy", "Policy"],
    "Leadership": [
        "Founder", "Co-Founder", "CEO", "Chief Executive Officer",
        "Managing Director", "MD", "President", "Chairman", "Chairperson"],
    "Investor / Corporate Relations": [
        "Investor Relations", "Corporate Relations", "Corporate Development",
        "Investor Communication", "Corporate Strategy", "Strategy"],
    "Events / Community": [
        "Events", "Event Marketing", "Community", "Community Manager",
        "Community Lead", "Experiential Marketing", "Events Marketing"],
    # ---- new groups ----
    "Product": [
        "Product Manager", "Product Management", "Head of Product", "Product Lead",
        "Associate Product Manager", "Chief Product Officer", "Product Owner"],
    "HR / Campus Hiring": [
        "Campus Hiring", "Campus Recruitment", "University Relations",
        "Talent Acquisition", "Human Resources", "HR Manager", "HR Head", "Recruiter"],
    "VC / Investment Team": [
        "Managing Partner", "General Partner", "Venture Partner", "Partner",
        "Principal", "Senior Associate", "Associate"],
    "VC Analysts & Platform": [
        "Analyst", "Investment Manager", "Platform Head", "Platform Lead"],
}

ROLE_PRIORITY = {
    "Founder": 100, "Co-Founder": 100, "CEO": 95, "Chief Executive Officer": 95,
    "Chairman": 90, "Chairperson": 90, "Managing Director": 88, "MD": 85,
    "Chief of Staff": 90,
    "Founder's Office": 92, "Founders Office": 92, "Founder Office": 92,
    "CEO Office": 92, "Office of the CEO": 92, "Executive Office": 88,
    "Corporate Communications": 90, "Corporate Communication": 90,
    "Executive Communications": 88, "External Communications": 88, "Communications": 82,
    "Public Relations": 88, "PR": 85, "Media Relations": 88, "Press Relations": 86,
    "Public Affairs": 82,
    "Corporate Affairs": 85, "Government Relations": 80, "Government Affairs": 80,
    "External Affairs": 80, "Public Policy": 78,
    "Partnerships": 78, "Strategic Partnerships": 82, "Head of Partnerships": 85,
    "Partnerships Head": 85, "Business Development": 75, "Strategic Relations": 78,
    "CMO": 88, "Chief Marketing Officer": 88, "Head of Marketing": 85,
    "Marketing Head": 85, "VP Marketing": 82, "Director Marketing": 78, "Marketing": 70,
    "Investor Relations": 75, "Corporate Relations": 78, "Corporate Development": 75,
    "Corporate Strategy": 70,
    "Events": 60, "Event Marketing": 65, "Community": 55, "Community Manager": 60,
    # new
    "Managing Partner": 95, "General Partner": 93, "Venture Partner": 85, "Partner": 90,
    "Principal": 85, "Senior Associate": 80, "Associate": 75, "Analyst": 65,
    "Investment Manager": 78, "Platform Lead": 75,
    "Platform Head": 78,
    "Chief Product Officer": 88, "Head of Product": 85, "Product Lead": 78,
    "Product Manager": 75, "Product Management": 72, "Associate Product Manager": 65,
    "Campus Hiring": 80, "Campus Recruitment": 80, "University Relations": 82,
    "Talent Acquisition": 70, "HR Head": 80, "HR Manager": 72, "Human Resources": 65,
}

# Which groups are pre-selected for each vertical
PRESETS = {
    "Corporate Relations": ["Corporate Communications", "PR / Media", "Founder's / CEO Office",
                            "Partnerships / Business Development", "Corporate Affairs",
                            "Investor / Corporate Relations"],
    "Events": ["Events / Community", "Marketing", "Partnerships / Business Development"],
    "PR": ["PR / Media", "Corporate Communications", "Marketing"],
    "Marketing": ["Marketing", "Events / Community", "PR / Media"],
    "VC / Investors": ["VC / Investment Team"],
    "Custom": ["Leadership"],
}

CFG = {}
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+\d[\d\s().-]{8,16}\d")
IN_MOBILE_RE = re.compile(r"(?<![\d])(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?![\d])")
SKIP_HOSTS = ("linkedin", "facebook", "instagram", "twitter", "x.com", "wikipedia", "youtube",
              "crunchbase", "glassdoor", "naukri", "indeed", "bloomberg", "zaubacorp",
              "tracxn", "ambitionbox", "yourstory", "economictimes")


# ============================================================
# HELPERS  (from your original script)
# ============================================================
def clean_url(url):
    return url.split("?")[0].rstrip("/") if url else ""


def normalize_text(text):
    return re.sub(r"\s+", " ", text).strip() if text else ""


def extract_name(title):
    title = re.sub(r"\s*\|\s*LinkedIn.*$", "", normalize_text(title), flags=re.I)
    return title.split(" - ", 1)[0].strip() if title else ""


def extract_title(title, company=""):
    title = re.sub(r"\s*\|\s*LinkedIn.*$", "", normalize_text(title), flags=re.I)
    if " - " not in title:
        return ""
    t = title.split(" - ", 1)[1].strip()
    t = re.sub(r"\s+(at|@)\s+.*$", "", t, flags=re.I)  # drop "at Company"
    parts = [p.strip() for p in t.split(" - ")]
    parts = [p for p in parts if company.lower() not in p.lower()] or parts[:1]
    return parts[0]


def classify_person(text, groups):
    text = text.lower()
    best = ("", "", 0)
    for category, roles in groups.items():
        for role in roles:
            if re.search(r"(?<![a-z])" + re.escape(role.lower()) + r"(?![a-z])", text):
                p = ROLE_PRIORITY.get(role, 50)
                if p > best[2]:
                    best = (category, role, p)
    return best


def company_relevance(company, title, body):
    c, t, b = company.lower(), (title or "").lower(), (body or "").lower()
    score = 0
    if c in t:
        score += 35
    if c in b:
        score += 15
    if "linkedin.com/in/" in t:
        score += 5
    return score


# ============================================================
# SEARCH
# ============================================================
@st.cache_resource
def _search_cache():
    return {}


def web_search(query, n=10):
    key = (query, n, bool(CFG.get("serper")))
    cache = _search_cache()
    if key in cache:
        return cache[key]
    out = _web_search(query, n)
    if out:
        cache[key] = out
    return out


def _web_search(query, n=10):
    for _ in range(2):
        try:
            if CFG.get("serper"):
                r = requests.post("https://google.serper.dev/search",
                                  headers={"X-API-KEY": CFG["serper"]},
                                  json={"q": query, "num": n}, timeout=15)
                return [{"title": x.get("title", ""), "href": x.get("link", ""),
                         "body": x.get("snippet", "")} for x in r.json().get("organic", [])]
            from ddgs import DDGS
            with DDGS() as d:
                return list(d.text(query, max_results=n, backend="auto"))
        except Exception:
            time.sleep(2)
    return []


def search_company(company, groups, broad):
    """A few combined LinkedIn searches (instead of one per job type)."""
    candidates = {}
    # one search per job group (like the original company_people_finder), best roles first
    queries = []
    for roles in groups.values():
        top = sorted(roles, key=lambda r: -ROLE_PRIORITY.get(r, 50))[:5]
        queries.append(f'site:linkedin.com/in/ "{company}" (' + " OR ".join(f'"{r}"' for r in top) + ")")
    queries = queries[:12]
    if broad:
        queries += [f'site:linkedin.com/in/ "{company}"',
                    f'site:linkedin.com/in/ "{company}" employee',
                    f'site:linkedin.com/in/ "{company}" leadership']

    for query in queries:
        for res in web_search(query, 10):
            url = clean_url(res.get("href", ""))
            if "linkedin.com/in/" not in url.lower():
                continue
            title = normalize_text(res.get("title", ""))
            body = normalize_text(res.get("body", ""))
            name = extract_name(title)
            if not name:
                continue
            cat, role, prio = classify_person(title + " " + body, groups)
            if not cat:
                if not broad:
                    continue
                cat, role, prio = "Other", "", 30
            score = company_relevance(company, title, body) + prio
            old = candidates.get(url)
            if not old or score > old["score"]:
                candidates[url] = {"company": company, "name": name, "linkedin": url,
                                   "title": extract_title(title, company), "category": cat,
                                   "role": role, "score": score,
                                   "instagram": "", "emails": [], "phones": []}
        if not CFG.get("serper"):
            time.sleep(1.0)
    return list(candidates.values())


def rank_people(candidates, limit):
    good = [p for p in candidates if p["score"] >= 40]
    good.sort(key=lambda x: (x["score"], ROLE_PRIORITY.get(x["role"], 0)), reverse=True)
    return good[:limit]


# ============================================================
# EMAIL / PHONE FROM THE COMPANY'S OWN WEBSITE
# ============================================================
def guess_website(company):
    for r in web_search(f"{company} official website", 6):
        host = urlparse(r.get("href", "")).netloc.lower().replace("www.", "")
        if host and not any(s in host for s in SKIP_HOSTS):
            return "https://" + host
    return ""


def site_contacts(site):
    if not site:
        return [], []
    if not site.startswith("http"):
        site = "https://" + site
    site = site.rstrip("/")
    emails, phones = [], []
    for p in ("", "/contact", "/contact-us", "/team", "/about", "/about-us"):
        try:
            r = requests.get(site + p, headers=UA, timeout=8)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select('a[href^="mailto:"]'):
                emails.append(a["href"][7:].split("?")[0].strip())
            for a in soup.select('a[href^="tel:"]'):
                phones.append(a["href"][4:].strip())
            for t in soup(["script", "style", "noscript"]):
                t.decompose()
            text = " ".join(soup.get_text(" ").split())
            emails += EMAIL_RE.findall(text)
            phones += PHONE_RE.findall(text)
        except Exception:
            continue
    bad = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", "sentry", "wixpress", "example.")
    emails = [e for e in dict.fromkeys(x.lower() for x in emails) if not any(b in e for b in bad)]
    phones = list(dict.fromkeys(re.sub(r"\s+", " ", p).strip() for p in phones))
    return emails, phones


def assign_emails(name, emails, use_company_level):
    parts = [p.lower() for p in re.findall(r"[A-Za-z]{3,}", name)]
    personal = [e for e in emails if any(p in e.split("@")[0] for p in parts)]
    rest = [e for e in emails if e not in personal] if use_company_level else []
    return (personal + rest)[:4]


def find_instagram(name, company):
    for r in web_search(f'site:instagram.com "{name}" {company}', 4):
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", r.get("href", ""))
        if m and m.group(1) not in ("p", "reel", "reels", "explore", "accounts", "stories"):
            return "@" + m.group(1)
    return ""


GENERIC_LOCALS = {"info", "hello", "hi", "contact", "contactus", "press", "media", "pr", "team", "office",
                  "admin", "support", "careers", "hr", "sales", "enquiries", "inquiries", "enquiry",
                  "mail", "connect", "invest", "pitch", "pitches", "founders", "startups", "deals",
                  "dealflow", "marketing", "communications", "events", "business", "bd", "general",
                  "reach", "ask", "help", "partnerships", "partners", "investors", "ir", "feedback"}
TEAM_PATHS = ("", "/team", "/our-team", "/people", "/about", "/about-us", "/who-we-are",
              "/partners", "/contact", "/contact-us")
ROLE_WORDS = re.compile(r"\b(partner|principal|associate|analyst|founder|co-founder|managing|director|"
                        r"vice president|vp|investor|investments?|head|platform|ceo|cio|cfo|coo|chief|"
                        r"venture|manager|advisor|operating|president)\b", re.I)
NAME_RE = re.compile(r"^[A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+){1,3}$")
NOT_NAMES = {"team", "about", "contact", "us", "read", "more", "view", "profile", "privacy", "policy",
             "terms", "portfolio", "news", "blog", "careers", "home", "linkedin", "twitter", "our",
             "meet", "get", "touch", "partners", "investors", "advisors", "follow", "email", "view"}


def name_key(n):
    return re.sub(r"[^a-z ]", "", (n or "").lower()).strip()


def add_person(found, company, name, title="", linkedin="", email="", phone="", profile=""):
    name = normalize_text(name)
    if not NAME_RE.match(name) or any(w.lower() in NOT_NAMES for w in name.split()):
        return
    p = found.setdefault(name_key(name), {
        "company": company, "name": name, "linkedin": "", "title": "", "category": "", "role": "",
        "score": 0, "instagram": "", "emails": [], "phones": [], "profile": ""})
    p["profile"] = p["profile"] or profile
    p["title"] = p["title"] or normalize_text(title)
    p["linkedin"] = p["linkedin"] or clean_url(linkedin)
    if email and email.lower() not in p["emails"]:
        p["emails"].append(email.lower())
    if phone and phone not in p["phones"]:
        p["phones"].append(phone)


def cards_from_soup(soup, company, found):
    """Team pages usually show each person in a 'card' with a LinkedIn / email link. Read those cards."""
    for a in soup.select('a[href*="linkedin.com/in/"], a[href^="mailto:"]'):
        href = a["href"]
        node = a
        for _ in range(6):
            node = node.parent
            if node is None or len(node.select('a[href*="linkedin.com/in/"]')) > 1:
                break
            lines = [normalize_text(t) for t in node.get_text("\n").split("\n")]
            lines = [x for x in lines if x]
            name = next((x for x in lines if len(x) < 40 and NAME_RE.match(x)
                         and not any(w.lower() in NOT_NAMES for w in x.split())), "")
            if name:
                role = next((x for x in lines if x != name and len(x) < 80 and ROLE_WORDS.search(x)), "")
                if "linkedin.com/in/" in href:
                    add_person(found, company, name, role, linkedin=href)
                else:
                    add_person(found, company, name, role, email=href[7:].split("?")[0])
                break


KEY_PATH = re.compile(r"team|people|partner|leadership|about|contact|founder|investment|professional|"
                      r"management|member|who-we|our-|principal|associate|advisor|reach", re.I)
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|svg|gif|webp|zip|mp4|docx?|xlsx?)$", re.I)
PROFILE_PATH = re.compile(r"/(team|people|partners?|our-team|leadership|members?|professionals|"
                          r"investment-team|founders?|principals?|about/[a-z-]+)/[^/]+$", re.I)
MAX_PAGES = 30


def fetch_page(url):
    try:
        r = requests.get(url, headers=UA, timeout=8)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            return None
        return r.url, r.text
    except Exception:
        return None


def sitemap_pages(site):
    out = []
    for sm in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"):
        try:
            r = requests.get(site + sm, headers=UA, timeout=8)
            if r.status_code == 200:
                out += [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
                        if KEY_PATH.search(urlparse(u).path) and not u.endswith(".xml")]
        except Exception:
            continue
    return out[:20]


def deobfuscate(text):
    text = re.sub(r"\s*[\[(]\s*at\s*[\])]\s*", "@", text, flags=re.I)
    return re.sub(r"\s*[\[(]\s*dot\s*[\])]\s*", ".", text, flags=re.I)


def digits10(p):
    return re.sub(r"\D", "", p)[-10:]


def find_phones(text):
    return PHONE_RE.findall(text) + IN_MOBILE_RE.findall(text)


def scrape_team(site, company, groups, broad):
    """Read a firm's own website: team pages + every person's profile page -> people, emails, phones."""
    if not site.startswith("http"):
        site = "https://" + site
    site = site.rstrip("/")
    host = urlparse(site).netloc.lower().replace("www.", "")
    found, texts, links, pages = {}, [], [], {}

    # ---- 1. crawl: standard team/contact pages, then the team-looking links found on them ----
    queue = [site + p for p in TEAM_PATHS] + sitemap_pages(site)
    seen = set()
    for _ in range(3):
        batch = [u for u in dict.fromkeys(queue) if u not in seen][:max(0, MAX_PAGES - len(seen))]
        if not batch:
            break
        seen.update(batch)
        with ThreadPoolExecutor(6) as ex:
            results = list(ex.map(fetch_page, batch))
        queue = []
        for res in results:
            if not res or res[0] in pages:
                continue
            url, html = res
            soup = BeautifulSoup(html, "html.parser")
            pages[url] = soup
            for a in soup.select("a[href]"):
                h = urljoin(url, a["href"]).split("#")[0].split("?")[0].rstrip("/")
                p = urlparse(h)
                if p.scheme in ("http", "https") and p.netloc.lower().replace("www.", "") == host \
                        and not SKIP_EXT.search(p.path) and KEY_PATH.search(p.path):
                    queue.append(h)

    # ---- 2. read every page ----
    page_data = []   # (url, soup, emails, phones)
    for url, soup in pages.items():
        pe = [a["href"][7:].split("?")[0].strip() for a in soup.select('a[href^="mailto:"]')]
        pp = [a["href"][4:].strip() for a in soup.select('a[href^="tel:"]')]
        for a in soup.select('a[href*="linkedin.com/in/"], a[href^="mailto:"], a[href^="tel:"]'):
            links.append(f"{normalize_text(a.get_text())[:40]} -> {a['href'].split('?')[0]}")
        cards_from_soup(soup, company, found)
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        text = deobfuscate(" ".join(soup.get_text(" ").split()))
        texts.append(text[:3000])
        pe += EMAIL_RE.findall(text)
        pp += find_phones(text)
        page_data.append((url, soup, pe, pp))

    bad = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", "sentry", "wixpress", "example.")
    email_pages, phone_pages = Counter(), Counter()
    for _, _, pe, pp in page_data:
        for e in {x.lower() for x in pe if not any(b in x.lower() for b in bad)}:
            email_pages[e] += 1
        for d in {digits10(x) for x in pp}:
            phone_pages[d] += 1

    # ---- 3. person profile pages: heading = name; their own email / phone / LinkedIn ----
    for url, soup, pe, pp in page_data:
        if not PROFILE_PATH.search(urlparse(url).path):
            continue
        h = soup.find("h1") or soup.find("h2")
        name = normalize_text(h.get_text()) if h else ""
        if not NAME_RE.match(name) or any(w.lower() in NOT_NAMES for w in name.split()):
            continue
        lines = [x for x in (normalize_text(t) for t in soup.get_text("\n").split("\n")) if x]
        seg = lines[lines.index(name) + 1: lines.index(name) + 8] if name in lines else []
        role = next((x for x in seg if len(x) < 80 and ROLE_WORDS.search(x)), "")
        li = next((a["href"] for a in soup.select('a[href*="linkedin.com/in/"]')), "")
        toks = re.findall(r"[a-z]{3,}", name.lower())
        add_person(found, company, name, role, li, profile=url)
        for e in dict.fromkeys(x.lower() for x in pe):
            local = e.split("@")[0]
            generic_local = re.split(r"[^a-z]", local)[0] in GENERIC_LOCALS
            if any(b in e for b in bad):
                continue
            if any(t in local for t in toks) or (not generic_local and email_pages[e] <= 2):
                add_person(found, company, name, email=e)
        for ph in dict.fromkeys(re.sub(r"\s+", " ", x).strip() for x in pp):
            if phone_pages[digits10(ph)] == 1:
                add_person(found, company, name, phone=ph)

    # ---- 4. AI reads the page text too (optional) ----
    if CFG.get("api_key") and texts:
        blob = "\n".join(texts[:6])[:6500] + "\nLINKS:\n" + "\n".join(dict.fromkeys(links))[:2500]
        prompt = f"""Below is text and links from the website of "{company}". List the team members
(partners, principals, associates, analysts, founders, staff). Return ONLY a JSON array of objects with keys
"name", "designation", "email", "phone", "linkedin". Use ONLY what is explicitly present in the text or links.
Use "" if unknown. Never guess emails or phone numbers.

{blob}"""
        try:
            low = blob.lower()
            for p in parse_json(llm(prompt, 2500)):
                if not isinstance(p, dict) or not p.get("name") or name_key(p["name"]) not in name_key(low):
                    continue
                em = p.get("email", "") if p.get("email", "").lower() in low else ""
                li = p.get("linkedin", "") if p.get("linkedin", "").split("?")[0].lower() in low else ""
                add_person(found, company, p["name"], p.get("designation", ""), li, em, "")
        except Exception:
            pass

    emails = list(email_pages)
    phones = []
    for d, _ in phone_pages.most_common():       # company-wide numbers (on many pages) first
        for _, _, _, pp in page_data:
            hit = next((re.sub(r"\s+", " ", x).strip() for x in pp if digits10(x) == d), "")
            if hit:
                phones.append(hit)
                break

    people = []
    for p in found.values():
        cat, role, prio = classify_person(p["title"], groups)
        if cat or broad or not p["title"]:
            p["category"], p["role"], p["score"] = cat, role, 100 + prio
            people.append(p)
    return people, emails, phones


def merge_people(web, searched):
    out = list(web)
    for f in searched:
        fk = name_key(f["name"]).split()
        if not fk:
            continue
        match = None
        for w in out:
            wk = name_key(w["name"]).split()
            if wk and wk[0] == fk[0] and wk[-1] == fk[-1]:
                match = w
                break
        if match:
            match["linkedin"] = match["linkedin"] or f["linkedin"]
            match["title"] = match["title"] or f["title"]
        else:
            out.append(f)
    return out


def process_company(row, groups, per_co, broad, want_ig, use_company_level):
    company = row["company"]
    site = row.get("website")
    if not isinstance(site, str) or not site.strip():
        site = guess_website(company)
    if site:
        u = urlparse(site.strip() if site.strip().startswith("http") else "https://" + site.strip())
        site = f"{u.scheme}://{u.netloc}"
    web_people, emails, phones = [], [], []
    if site:
        try:
            web_people, emails, phones = scrape_team(site, company, groups, broad)
        except Exception:
            pass
    searched = []
    if broad or len(web_people) < per_co or any(not p["linkedin"] for p in web_people):
        searched = rank_people(search_company(company, groups, broad), per_co)
    people = merge_people(web_people, searched)
    people.sort(key=lambda p: p["score"], reverse=True)
    people = people[:per_co]
    # company-level emails = ones that don't belong to any named person (info@, hello@, press@)
    generic = [e for e in emails if re.split(r"[^a-z]", e.split("@")[0])[0] in GENERIC_LOCALS]
    for p in people:
        p["website"] = site or ""
        if not p["emails"]:
            mine = re.findall(r"[a-z]{3,}", p["name"].lower())
            personal = [e for e in emails if any(t in e.split("@")[0] for t in mine)]
            p["emails"] = (personal + (generic if use_company_level else []))[:5]
        if not p["phones"] and use_company_level:
            p["phones"] = phones[:1]
        if want_ig:
            p["instagram"] = find_instagram(p["name"], company)
    return people


# ============================================================
# LLM (only used for: sector -> companies, and specific-person lookup)
# ============================================================
def llm(prompt, max_tokens=3000):
    """Works with any OpenAI-compatible API. Default: Groq (free)."""
    url = CFG["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {CFG['api_key'].strip()}", "Content-Type": "application/json"}
    models = [CFG["model"]] + [m for m in ("llama-3.1-8b-instant",) if m != CFG["model"]]
    last = "no response"
    for model in models:
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}]}
        r = None
        for attempt in range(4):
            r = requests.post(url, headers=headers, json=body, timeout=60)
            if r.status_code != 429:
                break
            time.sleep(min(float(r.headers.get("retry-after", 5 * (attempt + 1))), 30))  # free-tier limit
        if r.ok:
            return r.json()["choices"][0]["message"]["content"] or ""
        last = f"{r.status_code} - {r.text[:300]}"
        if r.status_code in (401, 403):  # key problem: another model will not help
            break
    raise RuntimeError(last)


def explain_ai_error(e):
    msg = str(e)
    if msg.startswith("401"):
        return ("The AI key was rejected. Check that GROQ_API_KEY in Secrets is copied fully "
                "(starts with gsk_), inside quotes, with no spaces.")
    if msg.startswith("429"):
        return "The free AI limit was reached for now. Please wait a minute and try again."
    return "The AI service gave an error. Details for the app owner: " + msg


def parse_json(text):
    m = re.search(r"\[.*\]", text, re.S)
    try:
        return json.loads(m.group(0)) if m else []
    except Exception:
        return []


def fmt(results):
    return "\n".join(f"{r.get('title', '')} | {r.get('href', '')} | {r.get('body', '')}"
                     for r in results)


def find_companies(sector, n, entity, region):
    blob = ""
    for q in (f"top {sector} {entity} {region}", f"list of {sector} {entity} {region}",
              f"best {sector} {entity} {region} {time.strftime('%Y')}",
              f"leading {sector} {entity} {region}"):
        blob += fmt(web_search(q, 10)) + "\n"
        time.sleep(1)
    prompt = f"""From the search results below, list up to {n} real {entity} in the "{sector}" space
(region: {region or 'any'}). Return ONLY a JSON array of objects with keys:
"company" (name), "website" (official domain if visible in the results, else ""), "note" (max 8 words).
No duplicates. Do not invent companies that are not supported by the results.

RESULTS:
{blob[:9000]}"""
    return parse_json(llm(prompt))


def lookup_person(name):
    blob = fmt(web_search(f"{name} company designation linkedin", 8)) + "\n" + \
        fmt(web_search(f"{name} founder OR CEO OR works at", 6))
    prompt = f"""Using ONLY the sources below, identify the person "{name}".
Return ONLY a JSON array with ONE object with keys: "name", "designation", "company", "linkedin", "instagram".
Use "" when unknown. Never guess.

SOURCES:
{blob[:8000]}"""
    res = parse_json(llm(prompt, 1000))
    p = res[0] if res and isinstance(res[0], dict) else {"name": name}
    li = p.get("linkedin", "") if p.get("linkedin", "") in blob else ""
    return {"company": p.get("company", ""), "name": p.get("name", name), "linkedin": li,
            "title": p.get("designation", ""), "category": "", "role": "", "score": 0,
            "instagram": p.get("instagram", ""), "emails": [], "phones": []}


# ============================================================
# OUTPUT
# ============================================================
def build_dataframe(people, cols):
    out = []
    for i, p in enumerate(people, 1):
        d = {"Sr No": i, "Name": p["name"], "Designation": p["title"],
             "Company": p["company"], "Website": p.get("website", ""),
             "Profile Page": p.get("profile", ""), "LinkedIn": p["linkedin"]}
        if "Instagram" in cols:
            d["Instagram"] = p["instagram"]
        for k in range(5):
            d[f"Email {k + 1}"] = p["emails"][k] if k < len(p["emails"]) else ""
        if "Mobile" in cols:
            for k in range(3):
                d["Mobile" if k == 0 else f"Mobile {k + 1}"] = p["phones"][k] if k < len(p["phones"]) else ""
        out.append(d)
    return pd.DataFrame(out)


def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


# ============================================================
# UI  (everything on ONE page)
# ============================================================
ROLE_GROUPS.update({
    "Sponsorship / Brand Partnerships": [
        "Sponsorship", "Sponsorships", "Brand Partnerships", "Brand Alliances",
        "Sponsorship Manager", "Brand Collaboration", "Influencer Marketing"],
    "Brand / Creative / Content": [
        "Brand Manager", "Brand Head", "Head of Brand", "Creative Director", "Content Marketing",
        "Content Head", "Social Media", "Digital Marketing", "Head of Content"],
    "Sales / Revenue": [
        "Sales", "Head of Sales", "Sales Head", "VP Sales", "Chief Revenue Officer", "CRO",
        "Key Account Manager", "Account Manager"],
    "Finance / CFO": [
        "CFO", "Chief Financial Officer", "Finance Head", "Head of Finance", "Finance Controller"],
    "Operations / COO": [
        "COO", "Chief Operating Officer", "Operations Head", "Head of Operations",
        "Operations Manager"],
    "Technology / CTO": [
        "CTO", "Chief Technology Officer", "Head of Engineering", "VP Engineering"],
    "CSR / Sustainability": [
        "CSR", "Corporate Social Responsibility", "Sustainability", "ESG", "Social Impact"],
})
ROLE_PRIORITY.update({
    "Sponsorship": 70, "Sponsorships": 70, "Sponsorship Manager": 72, "Brand Partnerships": 78,
    "Brand Alliances": 72, "Brand Collaboration": 68, "Influencer Marketing": 62,
    "Brand Manager": 70, "Brand Head": 82, "Head of Brand": 82, "Creative Director": 72,
    "Content Marketing": 62, "Content Head": 72, "Head of Content": 72, "Social Media": 55,
    "Digital Marketing": 66,
    "Sales": 55, "Head of Sales": 78, "Sales Head": 78, "VP Sales": 78,
    "Chief Revenue Officer": 85, "CRO": 82, "Key Account Manager": 55, "Account Manager": 50,
    "CFO": 85, "Chief Financial Officer": 85, "Finance Head": 75, "Head of Finance": 75,
    "Finance Controller": 65,
    "COO": 85, "Chief Operating Officer": 85, "Operations Head": 75, "Head of Operations": 75,
    "Operations Manager": 60,
    "CTO": 85, "Chief Technology Officer": 85, "Head of Engineering": 75, "VP Engineering": 75,
    "CSR": 65, "Corporate Social Responsibility": 68, "Sustainability": 60, "ESG": 60,
    "Social Impact": 58,
})

JOBS = {
    "Company": {
        "Corporate Communications": ROLE_GROUPS["Corporate Communications"],
        "PR / Media": ROLE_GROUPS["PR / Media"],
        "Marketing": ROLE_GROUPS["Marketing"],
        "Founder's / CEO Office": ROLE_GROUPS["Founder's / CEO Office"],
        "Partnerships / Business Dev": ROLE_GROUPS["Partnerships / Business Development"],
        "Events / Community": ROLE_GROUPS["Events / Community"],
        "Corporate Affairs": ROLE_GROUPS["Corporate Affairs"],
        "Investor / Corporate Relations": ROLE_GROUPS["Investor / Corporate Relations"],
        "Leadership (Founder, CEO, MD)": ROLE_GROUPS["Leadership"],
        "Sponsorship / Brand Partnerships": ROLE_GROUPS["Sponsorship / Brand Partnerships"],
        "Brand / Creative / Content": ROLE_GROUPS["Brand / Creative / Content"],
        "Sales / Revenue": ROLE_GROUPS["Sales / Revenue"],
        "Product": ROLE_GROUPS["Product"],
        "HR / Campus Hiring": ROLE_GROUPS["HR / Campus Hiring"],
        "CSR / Sustainability": ROLE_GROUPS["CSR / Sustainability"],
        "Finance / CFO": ROLE_GROUPS["Finance / CFO"],
        "Operations / COO": ROLE_GROUPS["Operations / COO"],
        "Technology / CTO": ROLE_GROUPS["Technology / CTO"],
    },
    "VC fund": {
        "Partner": ["Managing Partner", "General Partner", "Venture Partner", "Partner"],
        "Principal": ["Principal"],
        "Senior Associate": ["Senior Associate"],
        "Associate": ["Associate"],
    },
}
TICKED = {
    "Company": ["Corporate Communications", "PR / Media", "Marketing", "Founder's / CEO Office",
                "Partnerships / Business Dev", "Events / Community", "Corporate Affairs",
                "Investor / Corporate Relations", "Leadership (Founder, CEO, MD)",
                "Sponsorship / Brand Partnerships"],
    "VC fund": ["Partner", "Principal", "Senior Associate", "Associate"],
}

st.markdown("""
<style>
/* hide every Streamlit owner/admin control from visitors */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
.stAppDeployButton, [data-testid="stAppDeployButton"], [class*="viewerBadge"], [data-testid="manage-app-button"],
[class*="StatusWidget"] {display: none !important; visibility: hidden !important;}
header[data-testid="stHeader"] {background: transparent;}
.block-container {max-width: 100%; padding: 0.8rem 1.6rem 0.5rem;}
div[data-testid="InputInstructions"] {display: none;}
@media (min-width: 992px) {
  [data-testid="stSidebar"] {min-width: 370px !important; max-width: 370px !important;}
  [data-testid="stSidebarCollapseButton"], [data-testid="stSidebarCollapsedControl"] {display: none !important;}
}
[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {display: none;}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {padding-top: 0.6rem;}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap: 0.55rem;}
[data-testid="stSidebar"] label p {font-size: 0.86rem; font-weight: 600;}
.brand {font-size: 1.35rem; font-weight: 800; margin: 0;}
.hint {color: #6b7280; font-size: .8rem; margin: -.3rem 0 0;}
div.stButton > button, div.stDownloadButton > button {
    width: 100%; padding: .65rem 1rem; font-size: 1.02rem; border-radius: 12px; font-weight: 700;}
div[role="radiogroup"] label {border: 1.5px solid #d1d5db; border-radius: 12px; padding: .35rem .9rem;
                              background: rgba(127,127,127,.06);}
div[role="radiogroup"] label:has(input:checked) {border-color: #4f46e5; background: rgba(79,70,229,.14);}
.stat {border: 1px solid rgba(127,127,127,.3); border-radius: 14px; padding: .55rem .8rem; text-align: center;}
.stat b {display: block; font-size: 1.45rem; color: #4f46e5; line-height: 1.2;}
.stat small {color: #6b7280;}
.empty {border: 2px dashed rgba(127,127,127,.35); border-radius: 18px; padding: 3rem 1.5rem;
        text-align: center; color: #6b7280; margin-top: 1rem;}
.empty h3 {color: inherit; margin: 0 0 .4rem;}
</style>
""", unsafe_allow_html=True)

# Keys come ONLY from Streamlit Secrets (nothing is shown to visitors).
CFG.update(api_key=get_secret("LLM_API_KEY") or get_secret("GROQ_API_KEY"),
           serper=get_secret("SERPER_API_KEY"),
           model=get_secret("LLM_MODEL") or "llama-3.3-70b-versatile",
           base_url=get_secret("LLM_BASE_URL") or "https://api.groq.com/openai/v1")
ss = st.session_state

URL_END = re.compile(r"[\s|]+((?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?:/\S*)?)\s*$", re.I)


def parse_names(text):
    """One per line or comma separated. Optional website after the name: 'Blume Ventures | blume.vc'."""
    out, seen = [], set()
    for part in re.split(r"[\n,;]+", text or ""):
        part = re.sub(r"^\s*(\d+[.)]|[-•*])\s*", "", part).strip()
        url, name = "", part
        m = URL_END.search(part)
        if m:
            url, name = m.group(1), part[:m.start()].strip(" |-")
        elif re.fullmatch(r"(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?:/\S*)?", part, re.I):
            url, name = part, ""
        if url and not name:
            name = urlparse(url if url.startswith("http") else "https://" + url).netloc \
                .replace("www.", "").split(".")[0].title()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"name": name, "url": url})
    return out


def pick_all(kind):
    ss[f"jobs_{kind}"] = list(JOBS[kind]) if ss.get(f"allj_{kind}") else list(TICKED[kind])


def show_table(df, height):
    try:
        st.dataframe(df, hide_index=True, width="stretch", height=height)
    except TypeError:
        st.dataframe(df, hide_index=True, use_container_width=True, height=height)


# ======================= SIDE PANEL = ALL INPUTS =======================
with st.sidebar:
    st.markdown('<p class="brand">📇 DB Builder</p>', unsafe_allow_html=True)
    choice = st.radio("Type", ["🏢 Company", "💰 VC fund"], horizontal=True, label_visibility="collapsed")
    kind = "Company" if "Company" in choice else "VC fund"

    txt = st.text_area(f"{kind} names", height=105, key=f"names_{kind}",
                       placeholder="Blume Ventures | blume.vc\nAccel India\nPeak XV Partners" if kind == "VC fund"
                       else "Zerodha\nRazorpay | razorpay.com\nSwiggy")
    names = parse_names(txt)[:60]
    st.markdown(f'<div class="hint">{len(names)} {kind}(s). One per line or commas. '
                'Website optional: Name | site.com</div>', unsafe_allow_html=True)

    ss.setdefault(f"jobs_{kind}", list(TICKED[kind]))
    st.multiselect("Job titles", list(JOBS[kind]), key=f"jobs_{kind}")
    st.checkbox("Select all job titles", key=f"allj_{kind}", on_change=pick_all, args=(kind,))
    extra = st.text_input("Other job title (Enter; commas for several)", key=f"extra_{kind}",
                          placeholder="Sponsorship Manager, Fund Manager")

    c1, c2 = st.columns(2)
    want_mobile = c1.checkbox("Mobile number", value=True)
    all_people = c2.checkbox("All people", value=False, key=f"all_people_{kind}",
                             help="No limit on people per firm")
    per_co = st.slider(f"Most people per {kind.lower()}", 3, 30, 10 if kind == "VC fund" else 8,
                       key=f"per_{kind}", disabled=all_people)
    if all_people:
        per_co = 500
    with st.expander("More options"):
        use_company_level = st.checkbox("Use general email / phone from the website if a person's own "
                                        "isn't listed", value=True)
        broad = st.checkbox("Search more widely (more people, some less relevant)")

    groups = {label: JOBS[kind][label] for label in ss.get(f"jobs_{kind}", [])}
    custom = [x.strip() for x in extra.split(",") if x.strip()]
    if custom:
        groups["Other"] = custom
        for x in custom:
            ROLE_PRIORITY.setdefault(x, 80)

    problem = "Type at least one name." if not names else ("Pick at least one job title." if not groups else "")
    run = st.button("🚀  Build my database", type="primary", disabled=bool(problem), help=problem or None)

# ======================= MIDDLE = PROGRESS + RESULT =======================
live = st.empty()
if run:
    todo = [{"company": n["name"], "website": n["url"]} for n in names]
    results = {}
    with live.container():
        bar = st.progress(0.0, text="Starting...")
        table_ph = st.empty()
    workers = 4 if CFG.get("serper") else 2
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(process_company, r, groups, per_co, broad, False, use_company_level): i
                for i, r in enumerate(todo)}
        for n, f in enumerate(as_completed(futs), 1):
            i = futs[f]
            try:
                results[i] = f.result()
            except Exception:
                results[i] = []
            bar.progress(n / len(futs), text=f"{n} of {len(todo)} done ({todo[i]['company']})")
            so_far = [p for j in sorted(results) for p in results[j]]
            if so_far:
                with table_ph.container():
                    show_table(build_dataframe(so_far, ["Mobile"] if want_mobile else []), 420)
    live.empty()
    ordered = [p for i in sorted(results) for p in results[i]]
    ss.result = build_dataframe(ordered, ["Mobile"] if want_mobile else [])
    ss.empty = [todo[i]["company"] for i in sorted(results) if not results[i]]

res = ss.get("result")
if res is None:
    st.markdown('<div class="empty"><h3>Your database will show here</h3>'
                "Fill the panel on the left, then press <b>Build my database</b>.</div>",
                unsafe_allow_html=True)
elif res.empty:
    st.warning("We couldn't find anyone. Try more job titles, turn on 'Search more widely', "
               "or check the spelling of the names.")
else:
    email_cols = [c for c in res.columns if c.startswith("Email")]
    n_email = int((res[email_cols] != "").any(axis=1).sum())
    n_li = int((res["LinkedIn"] != "").sum())
    s1, s2, s3, s4 = st.columns([1, 1, 1, 2])
    s1.markdown(f'<div class="stat"><b>{len(res)}</b><small>people</small></div>', unsafe_allow_html=True)
    s2.markdown(f'<div class="stat"><b>{n_li}</b><small>with LinkedIn</small></div>', unsafe_allow_html=True)
    s3.markdown(f'<div class="stat"><b>{n_email}</b><small>with email</small></div>', unsafe_allow_html=True)
    with s4:
        st.download_button("⬇️  Download CSV (opens in Excel)", res.to_csv(index=False).encode("utf-8-sig"),
                           "database.csv", "text/csv", type="primary")
    show_table(res, 430)
    note = "Empty cells mean the detail isn't publicly listed."
    if ss.get("empty"):
        note += " No people found for: " + ", ".join(ss.empty) + "."
    st.caption(note)
