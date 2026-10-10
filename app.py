import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="DB Builder", page_icon="📇", layout="centered", initial_sidebar_state="collapsed")

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
    items = []
    for roles in groups.values():
        items += sorted(roles, key=lambda r: -ROLE_PRIORITY.get(r, 50))[:6]
    items = list(dict.fromkeys(items))
    chunks = [items[i:i + 6] for i in range(0, len(items), 6)][:4]
    queries = [f'site:linkedin.com/in/ "{company}" (' + " OR ".join(f'"{r}"' for r in c) + ")"
               for c in chunks]
    if broad:
        queries.append(f'site:linkedin.com/in/ "{company}" leadership')

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


def add_person(found, company, name, title="", linkedin="", email="", phone=""):
    name = normalize_text(name)
    if not NAME_RE.match(name) or any(w.lower() in NOT_NAMES for w in name.split()):
        return
    p = found.setdefault(name_key(name), {
        "company": company, "name": name, "linkedin": "", "title": "", "category": "", "role": "",
        "score": 0, "instagram": "", "emails": [], "phones": []})
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


def scrape_team(site, company, groups, broad):
    """Read a firm's own website: team pages -> people, emails, phones."""
    if not site.startswith("http"):
        site = "https://" + site
    site = site.rstrip("/")
    found, emails, phones, texts, links, seen = {}, [], [], [], [], set()
    for path in TEAM_PATHS:
        try:
            r = requests.get(site + path, headers=UA, timeout=8)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", "") \
                    or r.url in seen:
                continue
            seen.add(r.url)
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            continue
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        for a in soup.select('a[href^="mailto:"]'):
            emails.append(a["href"][7:].split("?")[0].strip())
        for a in soup.select('a[href^="tel:"]'):
            phones.append(a["href"][4:].strip())
        for a in soup.select('a[href*="linkedin.com/in/"], a[href^="mailto:"], a[href^="tel:"]'):
            links.append(f"{normalize_text(a.get_text())[:40]} -> {a['href'].split('?')[0]}")
        cards_from_soup(soup, company, found)
        text = " ".join(soup.get_text(" ").split())
        texts.append(text[:3000])
        emails += EMAIL_RE.findall(text)
        phones += PHONE_RE.findall(text)

    # AI reads the page text too (optional; if the AI fails we still keep what we found above)
    if CFG.get("api_key") and texts:
        blob = "\n".join(texts)[:6500] + "\nLINKS:\n" + "\n".join(dict.fromkeys(links))[:2500]
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

    bad = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", "sentry", "wixpress", "example.")
    emails = [e for e in dict.fromkeys(x.lower() for x in emails) if not any(b in e for b in bad)]
    phones = list(dict.fromkeys(re.sub(r"\s+", " ", p).strip() for p in phones))

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
    web_people, emails, phones = [], [], []
    if site:
        try:
            web_people, emails, phones = scrape_team(site, company, groups, broad)
        except Exception:
            pass
    searched = []
    if broad or len(web_people) < per_co:  # website already gave enough? skip the slower search
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
            p["emails"] = (personal + (generic if use_company_level else []))[:4]
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
             "Company": p["company"], "Website": p.get("website", ""), "LinkedIn": p["linkedin"]}
        if "Instagram" in cols:
            d["Instagram"] = p["instagram"]
        for k in range(4):
            d[f"Email {k + 1}"] = p["emails"][k] if k < len(p["emails"]) else ""
        d["Mobile"] = p["phones"][0] if p["phones"] else ""
        out.append(d)
    return pd.DataFrame(out)


def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


# ============================================================
# UI  (one simple page)
# ============================================================
st.markdown("""
<style>
#MainMenu, footer {visibility: hidden;}
.block-container {max-width: 760px; padding-top: 2rem;}
div.stButton > button, div.stDownloadButton > button {
    width: 100%; padding: 0.85rem 1rem; font-size: 1.1rem; border-radius: 12px;
}
.hint {color: #6b7280; font-size: 0.95rem; margin-bottom: 0.8rem;}
</style>
""", unsafe_allow_html=True)

FRIENDLY = {
    "Corporate Communications": "Corporate communications",
    "PR / Media": "PR and media",
    "Marketing": "Marketing and brand",
    "Founder's / CEO Office": "Founder's office / CEO office",
    "Partnerships / Business Development": "Partnerships and business development",
    "Corporate Affairs": "Corporate affairs",
    "Leadership": "Founders and leadership (CEO, MD)",
    "Investor / Corporate Relations": "Investor and corporate relations",
    "Events / Community": "Events and community",
    "Product": "Product managers",
    "HR / Campus Hiring": "HR and campus hiring",
    "VC / Investment Team": "VC investors (Partner, Principal, Senior Associate, Associate)",
    "VC Analysts & Platform": "VC analysts and platform team",
}
LABEL_TO_GROUP = {v: k for k, v in FRIENDLY.items()}
COMPANY_DEFAULT = ["Corporate Communications", "PR / Media", "Marketing", "Founder's / CEO Office",
                   "Partnerships / Business Development", "Events / Community"]
VC_DEFAULT = ["VC / Investment Team"]

# Keys live in Streamlit "Secrets", so friends never see or need them.
api_key = get_secret("LLM_API_KEY") or get_secret("GROQ_API_KEY")
serper = get_secret("SERPER_API_KEY")
base_url = get_secret("LLM_BASE_URL") or "https://api.groq.com/openai/v1"
model = get_secret("LLM_MODEL") or "llama-3.3-70b-versatile"

with st.sidebar:
    with st.expander("Admin setup (only for the app owner)"):
        api_key = st.text_input("Groq API key (free, from console.groq.com)", type="password", value=api_key)
        serper = st.text_input("Serper API key (optional)", type="password", value=serper)
        model = st.text_input("Model", value=model)
        base_url = st.text_input("API base URL (change only to use another provider)", value=base_url)
        if st.button("Test AI connection"):
            CFG.update(api_key=api_key, serper=serper, model=model, base_url=base_url)
            try:
                r = requests.get(base_url.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {api_key.strip()}"}, timeout=20)
                st.write(f"Key check: HTTP {r.status_code}")
                if r.ok:
                    ids = [m.get("id", "") for m in r.json().get("data", [])]
                    st.write("Model found: " + ("yes" if model in ids else "NO. Try one of: " + ", ".join(ids[:12])))
                    st.success("AI replied: " + llm("Reply with the single word OK", 5))
                else:
                    st.code(r.text[:400])
            except Exception as e:
                st.error(str(e)[:400])

CFG.update(api_key=api_key, serper=serper, model=model, base_url=base_url)
ss = st.session_state

st.title("📇 DB Builder")
st.markdown('<div class="hint">Type an area or some names. We visit each website and build your '
            "contact list: name, job title, LinkedIn, email and mobile.</div>", unsafe_allow_html=True)

kind = st.radio("What are you looking for?", ["🏢 Companies", "💰 VC funds"], horizontal=True)
is_vc = "VC" in kind
entity = "VC firms" if is_vc else "companies"

how = st.radio("How do you want to start?",
               ["🔍 Find them for me (type an area)", "📝 I have the names"], horizontal=True)
find_mode = how.startswith("🔍")

area, names, count = "", "", 20
if find_mode:
    area = st.text_input(f"Which area?", placeholder="Example: fintech, EV startups, clothing brands, early-stage")
    count = st.select_slider(f"How many {entity}?", options=[10, 20, 30, 40, 50], value=20)
    if not api_key:
        st.warning("Searching by area needs the app owner to finish setup (small arrow at the top-left, "
                   "'Admin setup'). Meanwhile choose 'I have the names'.")
else:
    names = st.text_area(f"Type one {'VC fund' if is_vc else 'company'} per line", height=150,
                         placeholder="Zerodha\nRazorpay\nSequoia Capital India" if not is_vc
                         else "Blume Ventures\nAccel India\nPeak XV Partners")

with st.expander("More options (optional)"):
    default_groups = VC_DEFAULT if is_vc else COMPANY_DEFAULT
    picked = st.multiselect("Which job types do you want?", list(LABEL_TO_GROUP),
                            default=[FRIENDLY[g] for g in default_groups], key=f"jobs_{is_vc}")
    extra = st.text_input("Another job title? (separate with commas)",
                          placeholder="Example: Sponsorship Manager, Fund Manager")
    per_co = st.slider("Most people to keep per company", 3, 20, 10 if is_vc else 5, key=f"per_{is_vc}")
    region = st.text_input("Country or city", value="India")
    want_ig = st.checkbox("Also find Instagram IDs (slower)")
    use_company_level = st.checkbox("If a person's own email or mobile isn't listed, use the company's "
                                    "general one (like info@ or the main number)", value=True)
    broad = st.checkbox("Search more widely (more people, some less relevant)")

groups = {LABEL_TO_GROUP[p]: ROLE_GROUPS[LABEL_TO_GROUP[p]] for p in picked}
custom = [x.strip() for x in extra.split(",") if x.strip()]
if custom:
    groups["Custom"] = custom
    for x in custom:
        ROLE_PRIORITY.setdefault(x, 80)

ready = bool(groups) and ((find_mode and area.strip() and api_key) or (not find_mode and names.strip()))
go_clicked = st.button("🚀  Build my database", type="primary", disabled=not ready)

if go_clicked:
    bar = st.progress(0.0, text="Starting...")
    rows = []
    if find_mode:
        bar.progress(0.03, text=f"Finding {entity} in '{area.strip()}'...")
        try:
            for c in find_companies(area.strip(), count, entity, region):
                if isinstance(c, dict) and c.get("company"):
                    rows.append({"company": c["company"], "website": c.get("website", "") or ""})
        except Exception as e:
            bar.empty()
            st.error(explain_ai_error(e))
            st.stop()
    else:
        for line in names.splitlines():
            if line.strip():
                parts = [x.strip() for x in line.split(",")]
                rows.append({"company": parts[0], "website": parts[1] if len(parts) > 1 else ""})
    # remove duplicates, keep order
    seen, todo = set(), []
    for r in rows:
        k = r["company"].lower()
        if k not in seen:
            seen.add(k)
            todo.append(r)
    todo = todo[:60]

    if not todo:
        bar.empty()
        st.error("We couldn't find any. Try a simpler area (like 'fintech') or type the names yourself.")
        st.stop()

    results, workers = {}, (4 if serper else 2)
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(process_company, r, groups, per_co, broad, want_ig, use_company_level): i
                for i, r in enumerate(todo)}
        for n, f in enumerate(as_completed(futs), 1):
            i = futs[f]
            try:
                results[i] = f.result()
            except Exception:
                results[i] = []
            bar.progress(n / len(futs), text=f"Reading websites: {n} of {len(todo)} done ({todo[i]['company']})")
    bar.empty()
    ordered = [p for i in sorted(results) for p in results[i]]
    ss.result = build_dataframe(ordered, ["Instagram"] if want_ig else [])
    ss.empty = [todo[i]["company"] for i in sorted(results) if not results[i]]
    ss.total = len(todo)

# ------------------------------------------------------------
# Results
# ------------------------------------------------------------
if "result" in ss:
    res = ss.result
    st.markdown("---")
    if res.empty:
        st.warning("We couldn't find anyone this time. Try 'Search more widely' in More options, "
                   "pick more job types, or add a Serper key.")
    else:
        st.success(f"Done! Found {len(res)} people from {ss.total - len(ss.empty)} of {ss.total} "
                   f"{'VC funds' if is_vc else 'companies'}.")
        st.download_button("⬇️  Download my database (CSV, opens in Excel)",
                           res.to_csv(index=False).encode("utf-8-sig"), "database.csv", "text/csv",
                           type="primary")
        st.dataframe(res, use_container_width=True, hide_index=True)
        st.caption("Empty cells mean that detail isn't publicly listed. Please double-check important "
                   "contacts before reaching out.")
    if ss.get("empty"):
        st.info("No people found for: " + ", ".join(ss.empty[:30]))
