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
        "Principal", "Associate", "Analyst", "Investment Manager", "Platform Head",
        "Venture Capital", "Investments"],
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
    "Principal": 85, "Associate": 75, "Analyst": 65, "Investment Manager": 78,
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
    "VC / Investors": ["VC / Investment Team", "Leadership"],
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
def web_search(query, n=10):
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
    candidates = {}
    queries = []
    for category, roles in groups.items():
        role_string = " OR ".join(f'"{r}"' for r in roles[:5])
        queries.append((category, f'site:linkedin.com/in/ "{company}" ({role_string})'))
    if broad:
        queries += [("General", f'site:linkedin.com/in/ "{company}"'),
                    ("General", f'site:linkedin.com/in/ "{company}" leadership')]

    for category, query in queries:
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
                if category == "General":
                    continue  # unrelated person from a broad search
                cat, role, prio = category, "", 30
            score = company_relevance(company, title, body) + prio
            old = candidates.get(url)
            if not old or score > old["score"]:
                candidates[url] = {"company": company, "name": name, "linkedin": url,
                                   "title": extract_title(title, company), "category": cat,
                                   "role": role, "score": score,
                                   "instagram": "", "emails": [], "phones": []}
        time.sleep(1.0 if not CFG.get("serper") else 0)
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


def process_company(row, groups, per_co, broad, want_ig, want_contact, use_company_level):
    company = row["company"]
    people = rank_people(search_company(company, groups, broad), per_co)
    if want_contact and people:
        site = row.get("website") or guess_website(company)
        emails, phones = site_contacts(site)
        for p in people:
            p["emails"] = assign_emails(p["name"], emails, use_company_level)
            p["phones"] = phones[:1] if use_company_level else []
    if want_ig:
        for p in people:
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
             "Company": p["company"], "LinkedIn": p["linkedin"]}
        if "Category" in cols:
            d["Role Category"] = p["category"]
        if "Instagram" in cols:
            d["Instagram"] = p["instagram"]
        if "Email" in cols:
            for k in range(4):
                d[f"Email {k + 1}"] = p["emails"][k] if k < len(p["emails"]) else ""
        if "Phone" in cols:
            d["Phone"] = p["phones"][0] if p["phones"] else ""
        out.append(d)
    return pd.DataFrame(out)


def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


# ============================================================
# UI  (simple step-by-step wizard for non-technical users)
# ============================================================
st.markdown("""
<style>
#MainMenu, footer {visibility: hidden;}
.block-container {max-width: 760px; padding-top: 2rem;}
div.stButton > button, div.stDownloadButton > button {
    width: 100%; padding: 0.85rem 1rem; font-size: 1.05rem; border-radius: 12px;
}
.hint {color: #6b7280; font-size: 0.95rem; margin-bottom: 0.8rem;}
</style>
""", unsafe_allow_html=True)

FRIENDLY = {
    "Corporate Communications": "Corporate communications (company comms teams)",
    "PR / Media": "PR and media (PR managers, press, media relations)",
    "Marketing": "Marketing (marketing heads, brand, growth, CMO)",
    "Founder's / CEO Office": "Founder's office / CEO office (chief of staff, interns)",
    "Partnerships / Business Development": "Partnerships and business development",
    "Corporate Affairs": "Corporate affairs and government relations",
    "Leadership": "Founders and leadership (CEO, MD, co-founders)",
    "Investor / Corporate Relations": "Investor and corporate relations",
    "Events / Community": "Events and community (event marketing, community managers)",
    "Product": "Product managers",
    "HR / Campus Hiring": "HR and campus hiring",
    "VC / Investment Team": "VC team (partners, principals, associates, analysts)",
}

GOALS = [
    ("🤝", "Company contacts", "For sponsorships, collaborations and corporate relations", "Corporate Relations"),
    ("🎪", "Events and community people", "Event teams, community managers, brand partnerships", "Events"),
    ("📰", "PR and media people", "PR managers, communications, press", "PR"),
    ("📣", "Marketing people", "Marketing heads, brand managers, growth teams", "Marketing"),
    ("💰", "Investors and VCs", "Partners, principals and associates at VC firms", "VC / Investors"),
    ("✏️", "Something else", "You choose exactly which jobs you want", "Custom"),
]

STEP_NAMES = ["What you need", "Companies", "People", "Your database"]

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

CFG.update(api_key=api_key, serper=serper, model=model, base_url=base_url)

ss = st.session_state
ss.setdefault("step", 1)
ss.setdefault("goal", None)
ss.setdefault("source", "Find companies for me")


def go(n):
    ss.step = n


def pick_goal(goal):
    ss.goal = goal
    ss.pop("companies", None)
    ss.pop("result", None)
    ss.step = 2


def start_over():
    for k in list(ss.keys()):
        del ss[k]


def details_picker(key):
    st.markdown("**What details do you need?**")
    st.checkbox("Name, job title and LinkedIn link (always included)", value=True,
                disabled=True, key=f"{key}_base")
    c1, c2, c3 = st.columns(3)
    ig = c1.checkbox("Instagram", key=f"{key}_ig")
    em = c2.checkbox("Email", key=f"{key}_em")
    ph = c3.checkbox("Phone", key=f"{key}_ph")
    st.markdown('<div class="hint">We only add emails and phone numbers that are publicly listed. '
                "If we can't find one, that cell stays empty.</div>", unsafe_allow_html=True)
    return ig, em, ph


def to_cols(ig, em, ph):
    return [k for k, w in [("Instagram", ig), ("Email", em), ("Phone", ph)] if w]


st.title("📇 DB Builder")
st.progress(ss.step / 4, text=f"Step {ss.step} of 4: {STEP_NAMES[ss.step - 1]}")

# ------------------------------------------------------------
# STEP 1: what do you need?
# ------------------------------------------------------------
if ss.step == 1:
    st.header("What do you want to find?")
    st.markdown('<div class="hint">Tap the one that fits best. You can change details later.</div>',
                unsafe_allow_html=True)
    for i in range(0, len(GOALS), 2):
        cols2 = st.columns(2)
        for col, (icon, title, desc, goal) in zip(cols2, GOALS[i:i + 2]):
            with col:
                st.button(f"{icon}  {title}", key=f"goal_{goal}", on_click=pick_goal, args=(goal,))
                st.caption(desc)

# ------------------------------------------------------------
# STEP 2: companies
# ------------------------------------------------------------
elif ss.step == 2:
    goal = ss.goal
    st.header("Which companies?")
    st.markdown('<div class="hint">Tell us where to look.</div>', unsafe_allow_html=True)
    ss.source = st.radio(
        "How do you want to pick companies?",
        ["Find companies for me", "I already have a list of companies", "I have names of specific people"],
        index=["Find companies for me", "I already have a list of companies",
               "I have names of specific people"].index(ss.source),
        label_visibility="collapsed")

    needs_ai = ss.source != "I already have a list of companies"
    if needs_ai and not api_key:
        st.warning("This option needs the app owner to finish setup (open the small arrow at the top-left "
                   "and use 'Admin setup'). Meanwhile, you can choose 'I already have a list of companies'.")

    # ---- A) find companies for me
    elif ss.source == "Find companies for me":
        entity = "VC firms" if goal == "VC / Investors" else "companies"
        sector = st.text_input(f"What type of {entity}?",
                               placeholder="Example: fintech, clothing brands, EV startups, early-stage funds")
        count = st.select_slider("How many?", options=[10, 20, 30, 40, 50], value=20)
        with st.expander("More options"):
            region = st.text_input("Country or city", value="India")
        if st.button("🔍  Find them", type="primary", disabled=not sector.strip()):
            comps, failed = [], False
            with st.spinner("Looking for companies. This takes about a minute..."):
                try:
                    comps = find_companies(sector.strip(), count, entity, region)
                except Exception as e:
                    failed = True
                    st.error(explain_ai_error(e))
            df = pd.DataFrame(comps)
            if failed:
                pass
            elif df.empty:
                st.error("We couldn't find any. Try a simpler or broader type, like 'fintech'.")
            else:
                df.insert(0, "Include", True)
                ss.companies = df

    # ---- B) paste a list
    elif ss.source == "I already have a list of companies":
        pasted = st.text_area("Type or paste one company per line", height=170,
                              placeholder="Zerodha\nRazorpay\nSequoia Capital India")
        if st.button("✅  Use this list", type="primary", disabled=not pasted.strip()):
            rows = []
            for line in pasted.splitlines():
                if line.strip():
                    parts = [x.strip() for x in line.split(",")]
                    rows.append({"Include": True, "company": parts[0],
                                 "website": parts[1] if len(parts) > 1 else "", "note": ""})
            ss.companies = pd.DataFrame(rows)

    # ---- C) specific people
    else:
        names = st.text_area("Type one person's name per line", height=150,
                             placeholder="Alia Bhatt\nNandan Nilekani")
        ig, em, ph = details_picker("ppl")
        if st.button("🔍  Find these people", type="primary", disabled=not names.strip()):
            lst = [n.strip() for n in names.splitlines() if n.strip()]
            found, bar = [], st.progress(0.0, text="Searching...")
            with ThreadPoolExecutor(2) as ex:
                for i, f in enumerate(as_completed([ex.submit(lookup_person, n) for n in lst]), 1):
                    try:
                        found.append(f.result())
                    except Exception as e:
                        st.error(explain_ai_error(e))
                        st.stop()
                    bar.progress(i / len(lst), text=f"Done {i} of {len(lst)}")
            ss.result = build_dataframe(found, to_cols(ig, em, ph))
            ss.step = 4
            st.rerun()

    # ---- review list (A and B)
    if "companies" in ss and ss.source != "I have names of specific people":
        st.markdown("---")
        st.subheader("Here's your list")
        st.markdown('<div class="hint">Untick any company you don\'t want.</div>', unsafe_allow_html=True)
        ss.companies = st.data_editor(
            ss.companies, hide_index=True, use_container_width=True,
            column_order=["Include", "company", "note"],
            column_config={"Include": st.column_config.CheckboxColumn("Keep"),
                           "company": st.column_config.TextColumn("Company", disabled=True),
                           "note": st.column_config.TextColumn("About", disabled=True)})
        st.button("Next  →", type="primary", on_click=go, args=(3,))

    st.button("←  Back", key="back2", on_click=go, args=(1,))

# ------------------------------------------------------------
# STEP 3: people + details
# ------------------------------------------------------------
elif ss.step == 3:
    goal = ss.goal
    st.header("What kind of people?")
    st.markdown('<div class="hint">We pre-ticked the best ones for you. Tick or untick as you like.</div>',
                unsafe_allow_html=True)

    recommended = PRESETS[goal]
    others = [g for g in ROLE_GROUPS if g not in recommended]
    chosen = []
    for g in recommended:
        if st.checkbox(FRIENDLY[g], value=True, key=f"g_{goal}_{g}"):
            chosen.append(g)
    with st.expander("More job types"):
        for g in others:
            if st.checkbox(FRIENDLY[g], value=False, key=f"g_{goal}_{g}"):
                chosen.append(g)

    extra = st.text_input("Don't see the job you want? Type it here",
                          placeholder="Example: Sponsorship Manager, Fund Manager (separate with commas)")
    st.markdown("---")
    ig, em, ph = details_picker("co")
    st.markdown("---")
    per_co = st.radio("How many people per company?", [3, 5, 10], index=1, horizontal=True)

    with st.expander("More options"):
        broad = st.checkbox("Search more widely (finds more people, but some may be less relevant)")
        use_company_level = st.checkbox(
            "If a person's own email or phone isn't found, use the company's general one "
            "(like info@ or press@)", value=True)

    groups = {g: ROLE_GROUPS[g] for g in chosen}
    custom = [x.strip() for x in extra.split(",") if x.strip()]
    if custom:
        groups["Custom"] = custom
        for x in custom:
            ROLE_PRIORITY.setdefault(x, 80)

    todo = [r for _, r in ss.companies[ss.companies["Include"]].iterrows()]
    st.info(f"Ready to search {len(todo)} companies. This can take 1 to 5 minutes. "
            "Please keep this page open.")

    if st.button("🚀  Build my database", type="primary", disabled=not groups or not todo):
        results, bar = {}, st.progress(0.0, text="Starting...")
        workers = 4 if serper else 2
        with ThreadPoolExecutor(workers) as ex:
            futs = {ex.submit(process_company, r, groups, per_co, broad, ig, em or ph,
                              use_company_level): i for i, r in enumerate(todo)}
            for n, f in enumerate(as_completed(futs), 1):
                i = futs[f]
                try:
                    results[i] = f.result()
                except Exception:
                    results[i] = []
                bar.progress(n / len(futs), text=f"Done {n} of {len(futs)}: {todo[i]['company']}")
        ordered = [p for i in sorted(results) for p in results[i]]
        ss.result = build_dataframe(ordered, to_cols(ig, em, ph))
        ss.step = 4
        st.rerun()

    st.button("←  Back", key="back3", on_click=go, args=(2,))

# ------------------------------------------------------------
# STEP 4: result
# ------------------------------------------------------------
else:
    res = ss.get("result")
    if res is None or res.empty:
        st.header("We couldn't find anyone this time")
        st.markdown("Try one of these:")
        st.markdown("- Tick **Search more widely** in More options\n"
                    "- Pick more job types\n"
                    "- Try a different or bigger list of companies")
        st.button("←  Change my choices", on_click=go, args=(3 if ss.get("companies") is not None else 2,))
    else:
        n_co = res["Company"].replace("", pd.NA).nunique()
        st.success(f"Done! We found {len(res)} people from {n_co} companies.")
        st.download_button("⬇️  Download my database (CSV, opens in Excel)",
                           res.to_csv(index=False).encode("utf-8-sig"),
                           "database.csv", "text/csv", type="primary")
        st.dataframe(res, use_container_width=True, hide_index=True)
        st.caption("Empty cells mean that detail isn't publicly available. "
                   "Please double-check important contacts before reaching out.")
        if ss.get("companies") is not None:
            st.button("←  Change job types or details", on_click=go, args=(3,))
    st.button("🔄  Start a new database", key="restart", on_click=start_over)
