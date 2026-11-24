#!/usr/bin/env python3
"""Content radar: daily GitHub + newsletter signals for one niche. Python 3.11+, standard library only.

  python radar.py collect               fetch everything, write data/, state/, RADAR.md (the GitHub Action runs this)
  python radar.py ask                   pull, check freshness, print the radar
  python radar.py show <key>            one entity and its last 10 observations
  python radar.py ship <post> <key>     log a post you made from the radar  (--angle "..." --rubric 7)
  python radar.py plays <post> <n>      log its plays about 72 hours later

Settings live in sources.toml (what to read) and keywords.txt (your niche).
"""
import argparse
import csv
import datetime as dt
import email.utils
import html
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OBS_DIR = ROOT / "data" / "obs"
HEALTH = ROOT / "state" / "health.json"
LEDGER = ROOT / "ledger.csv"
RADAR = ROOT / "RADAR.md"
LEDGER_COLS = ["date", "post", "key", "angle", "rubric", "tags", "status_at_pick", "plays_72h", "outcome"]
ONE = dt.timedelta(days=1)
D = dt.date.fromisoformat
UA = "Mozilla/5.0 (content-radar)"


# ---------------- settings ----------------

def load_cfg():
    with open(ROOT / "sources.toml", "rb") as f:
        return tomllib.load(f)


def load_keywords():
    """keywords.txt: '[name weight cap]' headers, one term per line. [block] drops a repo outright."""
    groups, cur = [], None
    for line in (ROOT / "keywords.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if not line:
            continue
        if line.startswith("["):
            p = line.strip("[]").split()
            cur = {"name": p[0], "weight": float(p[1]) if len(p) > 1 else 0.0,
                   "cap": float(p[2]) if len(p) > 2 else None, "terms": []}
            groups.append(cur)
        elif cur is not None:
            cur["terms"].append(line)
    return groups


def _has(term, text):
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?:s|es)?(?![a-z0-9])", text) is not None


def relevance(name, desc, topics, groups):
    """Keyword score over name + description + topics. Returns (score, tags)."""
    text = f"{name} {desc or ''} {' '.join(topics or [])}".lower().replace("_", "-")
    rel, tags = 0.0, []
    for g in groups:
        n = sum(1 for t in g["terms"] if _has(t, text))
        if not n:
            continue
        if g["name"] == "block":
            return -99, []
        pts = g["weight"] * n
        if g["cap"] is not None and pts > 0:
            pts = min(g["cap"], pts)
        rel += pts
        if g["weight"] >= 0 and g["name"] != "broad":
            tags.append(g["name"])
    return rel, tags


# ---------------- HTTP ----------------

def get(url, headers=None, data=None, timeout=30):
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise last


_token = None


def token():
    """GITHUB_TOKEN inside the Action; `gh auth token` on your own computer."""
    global _token
    if _token is None:
        _token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if not _token:
            try:
                _token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15).stdout.strip()
            except Exception:
                _token = ""
    return _token


def gh_headers():
    h = {"Accept": "application/vnd.github+json"}
    if token():
        h["Authorization"] = "Bearer " + token()
    return h


# ---------------- sources ----------------

GH_RE = re.compile(r"(?<![\w.-])github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
GH_DENY = {"sponsors", "orgs", "features", "topics", "marketplace", "collections", "apps", "about", "login",
           "user-attachments", "settings", "search", "site", "enterprise", "pricing", "explore", "trending",
           "contact", "security", "customer-stories", "solutions", "resources", "team", "blog", "events",
           "join", "new", "codespaces", "copilot", "readme", "notifications", "issues", "pulls", "signup",
           "users", "repos", "assets", "images", "organizations", "account"}


def gh_key(owner, repo):
    repo = repo.rstrip(".")
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not repo or owner.lower() in GH_DENY or owner.startswith(".") or repo.startswith("."):
        return None
    return f"gh:{owner}/{repo}".lower()


def _text(s, n):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()[:n]


def _int(s):
    return int(s.replace(",", "")) if s else None


def trending(url, today):
    """GitHub Trending has no API, so this reads the HTML page. Fragile by nature: the canary watches row counts."""
    page = get(url)
    obs = []
    for rank, a in enumerate(page.split('<article class="Box-row"')[1:], 1):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+)/([^"/]+)"', a)
        key = gh_key(m.group(1), m.group(2)) if m else None
        if not key:
            continue
        gain = re.search(r"([\d,]+)\s+stars today", a)
        tot = re.search(r'/stargazers"[^>]*>\s*(?:<svg.*?</svg>)?\s*([\d,]+)', a, re.S)
        desc = re.search(r'<p class="col-9[^>]*>\s*(.*?)\s*</p>', a, re.S)
        v = {"rank": rank}
        if tot:
            v["stars"] = _int(tot.group(1))
        if gain:
            v["today"] = _int(gain.group(1))
        obs.append({"d": today.isoformat(), "src": "trend", "key": key, "v": v,
                    "ctx": _text(desc.group(1), 160) if desc else ""})
    return obs


def search(q, today, known):
    """GitHub Search finds candidates. It never makes a day hot on its own."""
    q = q.format(d30=(today - 30 * ONE).isoformat(), d7=(today - 7 * ONE).isoformat())
    url = ("https://api.github.com/search/repositories?q=" + urllib.parse.quote(q)
           + "&sort=stars&order=desc&per_page=50")
    obs = []
    for it in json.loads(get(url, gh_headers())).get("items", []):
        key = "gh:" + it["full_name"].lower()
        o = {"d": today.isoformat(), "src": "search", "key": key, "v": {"stars": it["stargazers_count"]}}
        if key not in known:
            o["v"]["topics"] = (it.get("topics") or [])[:8]
            o["ctx"] = (it.get("description") or "")[:160]
        obs.append(o)
    return obs


def _date(s):
    s = (s or "").strip()
    for parse in (lambda x: email.utils.parsedate_to_datetime(x),
                  lambda x: dt.datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            return parse(s).date()
        except Exception:
            pass
    return None


def feed(fc, today, window):
    """RSS or Atom. Every github.com/owner/repo link in an item counts as a mention on the item's date."""
    xml = get(fc["url"])
    items = re.split(r"<(?:item|entry)(?=[ >])", xml)[1:]
    obs, newest, lo = [], None, today - window * ONE
    for raw in items:
        t = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S)
        d = re.search(r"<(pubDate|published|updated|dc:date)>([^<]+)<", raw)
        day = _date(d.group(2)) if d else None
        if not day or day > today + ONE:
            continue
        newest = max(newest or day, day)
        if day < lo:
            continue
        title = _text(re.sub(r"^<!\[CDATA\[|\]\]>$", "", t.group(1).strip()) if t else "", 120)
        keys = []
        for o, r in GH_RE.findall(html.unescape(html.unescape(raw))):
            k = gh_key(o, r)
            if k and k not in keys:
                keys.append(k)
        for k in keys:
            obs.append({"d": min(day, today).isoformat(), "src": "rss:" + fc["id"], "key": k, "ctx": title})
    note = ""
    if not items:
        note = "0 items"
    elif not newest or (today - newest).days > 3 * fc.get("cadence_days", 1):
        note = f"newest item {newest}"
    return obs, note


def stars(keys, need_meta, today, batch=50):
    """One GraphQL call per 50 repos: today's star count (and the description if we lack it)."""
    obs = []
    for i in range(0, len(keys), batch):
        chunk, parts = keys[i:i + batch], []
        for j, k in enumerate(chunk):
            owner, name = k[3:].split("/", 1)
            parts.append(f'r{j}: repository(owner:"{owner}",name:"{name}")'
                         '{nameWithOwner stargazerCount description repositoryTopics(first:8){nodes{topic{name}}}}')
        body = json.dumps({"query": "query{" + " ".join(parts) + "}"}).encode()
        data = json.loads(get("https://api.github.com/graphql", {**gh_headers(), "Content-Type": "application/json"},
                              data=body)).get("data")
        if not data:
            raise RuntimeError("GraphQL returned no data (is a token set?)")
        for j, k in enumerate(chunk):
            r = data.get(f"r{j}")
            if not r:
                obs.append({"d": today.isoformat(), "src": "stars", "key": k, "v": {"gone": 1}})
                continue
            o = {"d": today.isoformat(), "src": "stars", "key": "gh:" + r["nameWithOwner"].lower(),
                 "v": {"stars": r["stargazerCount"]}}
            if k in need_meta:
                o["v"]["topics"] = [n["topic"]["name"] for n in r["repositoryTopics"]["nodes"]]
                o["ctx"] = (r.get("description") or "")[:160]
            obs.append(o)
    return obs


# ---------------- storage ----------------

def obs_id(o):
    return (o["d"], o["src"], o["key"])


def load_obs(today, window):
    """Observations of the last `window` days, plus how many days the collector actually ran."""
    out, days = [], 0
    lo = today - window * ONE
    for f in sorted(OBS_DIR.glob("*.jsonl")):
        try:
            fday = D(f.stem)
        except ValueError:
            continue
        if fday < lo or fday > today:
            continue
        days += 1
        out += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [o for o in out if D(o["d"]) >= lo], days


def append_obs(today, new, known):
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    rows = [o for o in new if obs_id(o) not in known]
    known.update(obs_id(o) for o in rows)
    with open(OBS_DIR / f"{today.isoformat()}.jsonl", "a", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_ledger():
    if not LEDGER.exists():
        return []
    with open(LEDGER, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("post")]


def write_ledger(rows):
    with open(LEDGER, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLS)
        w.writeheader()
        w.writerows(rows)


# ---------------- signal maths ----------------

def velocity(s, gain):
    """s: {date: stars}. Returns v1 (stars/day vs the last snapshot) and v3 (3-day average)."""
    days = sorted(s)
    v1, v3 = {}, {}
    for i, d in enumerate(days):
        if i:
            v1[d] = (s[d] - s[days[i - 1]]) / (d - days[i - 1]).days
        elif d in gain:
            v1[d] = gain[d]
    for d in days:
        if d - 3 * ONE in s:
            v3[d] = (s[d] - s[d - 3 * ONE]) / 3
        else:
            vals = [v1[x] for x in (d, d - ONE, d - 2 * ONE) if x in v1]
            if vals:
                v3[d] = sum(vals) / len(vals)
    return v1, v3


def lifecycle(today, hot, v3_today, v3_yday, peak):
    """The Monday-to-Wednesday rule. `hot` is the set of hot days."""
    if not hot:
        return None
    age, cold = (today - min(hot)).days, (today - max(hot)).days
    if cold >= 7:
        return "dead"
    if age <= 1:
        return "new"
    floor = 0.5 * (peak or 0)
    collapsed = v3_today is not None and v3_today < floor
    if cold >= 3 or (collapsed and v3_yday is not None and v3_yday < floor):
        return "peaked"
    hot3 = sum(1 for i in range(3) if today - i * ONE in hot)
    if cold <= 1 and hot3 >= 2 and not collapsed:
        return "sustained"
    return "watching"


def priors(ledger_rows):
    """tag -> [posts, breakouts]; prior = (breakouts + 1) / (posts + 4), so every tag starts at 25%."""
    p = {}
    for r in ledger_rows:
        if not r.get("plays_72h"):
            continue
        for t in [x for x in (r.get("tags") or "").split(",") if x]:
            c = p.setdefault(t, [0, 0])
            c[0] += 1
            c[1] += r.get("outcome") == "breakout"
    return {t: (b + 1) / (n + 4) for t, (n, b) in p.items()}


def build(today, obs, cfg, groups, ndays, ledger_rows):
    raw = {}
    for o in obs:
        d = D(o["d"])
        if d > today:
            continue
        e = raw.setdefault(o["key"], {"T": set(), "gain": {}, "s": {}, "m": {}, "desc": None, "topics": [],
                                      "seen": None, "gone": False})
        src, v = o["src"], o.get("v") or {}
        if src != "stars":
            e["seen"] = max(e["seen"] or d, d)
        if "stars" in v and (src == "stars" or d not in e["s"]):
            e["s"][d] = v["stars"]
        if src == "stars":
            e["gone"] = bool(v.get("gone"))
        if src == "trend":
            e["T"].add(d)
            if "today" in v:
                e["gain"][d] = max(v["today"], e["gain"].get(d, 0))
        elif src.startswith("rss:"):
            e["m"].setdefault(src[4:], set()).add(d)
        if src in ("trend", "search", "stars") and "ctx" in o:
            e["desc"] = o["ctx"]
            e["topics"] = v.get("topics") or e["topics"]

    pri = priors(ledger_rows)
    shipped = {r["key"]: r for r in ledger_rows if r.get("key")}
    week = {today - i * ONE for i in range(7)}
    hot_cfg = cfg["hot"]
    out = {}
    for key, e in raw.items():
        name = key.split("/", 1)[1]
        rel, tags = (None, []) if e["desc"] is None else relevance(name, e["desc"], e["topics"], groups)
        v1, v3 = velocity(e["s"], e["gain"])
        fam = {"T": set(e["T"]),
               "S": {d for d, v in v1.items() if v >= max(hot_cfg["star_floor"], hot_cfg["star_frac"] * e["s"][d])},
               "N": set()}
        pubs7 = set()
        for pub, days in e["m"].items():
            prev = None
            for d in sorted(days):  # a mention is hot when that newsletter was quiet about it for 7 days
                if prev is None or (d - prev).days > hot_cfg["mention_quiet_days"]:
                    fam["N"].add(d)
                prev = d
            if days & week:
                pubs7.add(pub)
        hot = set().union(*fam.values())
        peak = max(v3.values()) if v3 else None
        status = lifecycle(today, hot, v3.get(today), v3.get(today - ONE), peak)
        fam7 = sum(1 for f in "TN" if fam[f] & week)  # S moves with T, so it does not count as agreement
        if status == "sustained" and (fam7 >= 2 or len(pubs7) >= 2):
            status = "sustained+"
        hot3 = sum(1 for i in range(3) if today - i * ONE in hot)
        prior = max([pri.get(t, 0.25) for t in tags] + [0.25])
        ent = {"key": key, "desc": e["desc"] or "", "rel": rel, "tags": tags, "status": status,
               "prov": ndays < 3, "age": (today - min(hot)).days if hot else None, "hot3": hot3,
               "stars": e["s"][max(e["s"])] if e["s"] else None,
               "v1": round(v1[today]) if today in v1 else None, "v3": round(v3[today]) if today in v3 else None,
               "src": " ".join(f"{f}{len(fam[f] & week)}" for f in "TN" if fam[f] & week)
                      + (f"({','.join(sorted(pubs7))})" if pubs7 else ""),
               "fam7": fam7, "pubs7": len(pubs7), "seen": e["seen"], "gone": e["gone"],
               "last_hot": max(hot) if hot else None, "shipped": shipped.get(key)}
        ent["sort"] = 2 * fam7 + len(pubs7) + hot3 + math.log10(max(ent["v3"] or 0, 0) + 1) + 2 * (prior - 0.25)
        out[key] = ent
    return out


def track_list(E, today, cfg):
    """Repos whose stars get checked today: on-niche (or not yet described) and seen in the last 14 days."""
    keys = [k for k, e in E.items()
            if not e["gone"] and e["seen"] and (today - e["seen"]).days <= 14
            and (e["rel"] is None or e["rel"] >= cfg["track_min"] or (e["rel"] >= 1 and e["last_hot"] == today))]
    keys.sort(key=lambda k: (E[k]["last_hot"] or dt.date.min, E[k]["stars"] or 0), reverse=True)
    return keys[:cfg["max_tracked"]]


# ---------------- output ----------------

def _k(n):
    return "" if n is None else (f"{n / 1000:.1f}k" if n >= 1000 else str(n))


def render(E, today, cfg, health):
    bad = [s for s, r in health["sources"].items() if not r["ok"]]
    lines = [f"# RADAR {today} | generated {health['generated']} | sources {len(health['sources']) - len(bad)}"
             f"/{len(health['sources'])} ok" + (f" | FAIL {', '.join(bad)}" if bad else "")
             + f" | tracked {health['tracked']}",
             "key|status|age|hot3|stars|v1|v3|src|tags|desc"]
    tmin = cfg["track_min"]
    sec = {"RERUN (your winners, hot again)": [], "SUSTAINED+": [], "SUSTAINED": [], "NEW, clock live": [], "WATCHING": []}
    for e in sorted(E.values(), key=lambda e: e["sort"], reverse=True):
        st, sh = e["status"], e["shipped"]
        if st in (None, "dead", "peaked") or e["rel"] is None or e["gone"]:
            continue
        if not (e["rel"] >= tmin or (e["rel"] >= 1 and st.startswith("sustained"))):
            continue
        if sh and sh.get("outcome") == "flop":
            continue  # never re-run a flop
        if sh and sh.get("outcome") == "breakout" and (today - D(sh["date"])).days >= 14:
            sec["RERUN (your winners, hot again)"].append(e)
        elif not sh or (today - D(sh["date"])).days >= 14:
            sec[{"sustained+": "SUSTAINED+", "sustained": "SUSTAINED", "new": "NEW, clock live"}.get(st, "WATCHING")].append(e)
    caps = {"RERUN (your winners, hot again)": 2, "SUSTAINED+": 6, "SUSTAINED": 6, "NEW, clock live": 6, "WATCHING": 4}
    for name, rows in sec.items():
        if rows:
            lines.append("## " + name)
            for e in rows[:caps[name]]:
                lines.append("|".join([e["key"], ("~" if e["prov"] else "") + e["status"], str(e["age"]), str(e["hot3"]),
                                       _k(e["stars"]), _k(e["v1"]), _k(e["v3"]), e["src"], ",".join(e["tags"]),
                                       e["desc"].replace("|", "/")[:90]]))
    return "\n".join(lines) + "\n"


# ---------------- commands ----------------

def now():
    return dt.datetime.now(dt.timezone.utc)


def cmd_collect(args):
    cfg, groups, today = load_cfg(), load_keywords(), now().date()
    old, _ = load_obs(today, cfg["window_days"])
    known = {obs_id(o) for o in old}
    described = {o["key"] for o in old if "ctx" in o and o["src"] in ("trend", "search", "stars")}
    results, new = {}, []

    def run(sid, fn, min_rows=1):
        try:
            o, note = fn()
            ok = len(o) >= min_rows and not note
            note = note or ("" if ok else f"{len(o)} rows, expected {min_rows}+")
        except Exception as ex:
            o, ok, note = [], False, f"{type(ex).__name__}: {ex}"[:100]
        new.extend(o)
        results[sid] = {"ok": ok, "items": len(o), "note": note}
        print(f"  {'ok ' if ok else 'BAD'} {sid}: {len(o)} {note}")

    for i, url in enumerate(cfg["trending"]):
        run(f"trending{i + 1}", lambda u=url: (trending(u, today), ""), 10 if i == 0 else 3)
    for i, q in enumerate(cfg["searches"]):
        time.sleep(2.5 if i else 0)  # search allows 30 calls a minute
        run(f"search{i + 1}", lambda q=q: (search(q, today, described), ""))
    for fc in cfg.get("feed", []):
        run("rss:" + fc["id"], lambda fc=fc: feed(fc, today, cfg["window_days"]))
    append_obs(today, new, known)

    ledger_rows = read_ledger()
    obs, ndays = load_obs(today, cfg["window_days"])
    E = build(today, obs, cfg, groups, ndays, ledger_rows)
    keys = track_list(E, today, cfg)
    run("stars", lambda: (stars(keys, {k for k in keys if E[k]["rel"] is None}, today), ""))
    append_obs(today, new, known)  # only the star rows are new; the rest are skipped as already written

    obs, ndays = load_obs(today, cfg["window_days"])
    E = build(today, obs, cfg, groups, ndays, ledger_rows)
    health = {"generated": now().strftime("%Y-%m-%dT%H:%MZ"), "today": today.isoformat(),
              "capture_days": ndays, "tracked": len(keys), "sources": results}
    HEALTH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH.write_text(json.dumps(health, indent=1), encoding="utf-8")
    text = render(E, today, cfg, health)
    RADAR.write_text(text, encoding="utf-8")
    print(f"{len(new)} observations, RADAR.md {len(text.encode())} bytes")
    if not results["trending1"]["ok"] or not results["stars"]["ok"]:
        print("FAILED: trending or star snapshots (see the BAD lines above)", file=sys.stderr)
        return 2
    return 0


def _health():
    return json.loads(HEALTH.read_text(encoding="utf-8")) if HEALTH.exists() else None


def cmd_ask(args):
    if not args.no_pull:
        p = subprocess.run(["git", "pull", "--ff-only", "-q"], cwd=ROOT, capture_output=True, text=True)
        if p.returncode:
            print("WARNING: git pull failed; showing the local copy")
    h = _health()
    if not h:
        print("STALE: the collector has never run. Run it from the Actions tab or `python radar.py collect`.")
        return 1
    cfg = load_cfg()
    gen = dt.datetime.strptime(h["generated"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
    hours = (now() - gen).total_seconds() / 3600
    if hours > cfg["stale_hours"]:
        print(f"STALE: last run {h['generated']} ({hours:.0f}h ago). Check the Actions tab.")
    today = D(h["today"])
    obs, ndays = load_obs(today, cfg["window_days"])
    print(render(build(today, obs, cfg, load_keywords(), ndays, read_ledger()), today, cfg, h), end="")
    return 0


def cmd_show(args):
    cfg, h = load_cfg(), _health()
    today = D(h["today"]) if h else now().date()
    obs, ndays = load_obs(today, cfg["window_days"])
    E = build(today, obs, cfg, load_keywords(), ndays, read_ledger())
    key = args.key.lower() if args.key.startswith("gh:") else "gh:" + args.key.lower()
    if key not in E:
        print(f"unknown key {key}")
        return 1
    print(json.dumps({k: v for k, v in E[key].items() if k != "sort"}, default=str, indent=1))
    for o in [o for o in obs if o["key"] == key][-10:]:
        print(json.dumps(o, ensure_ascii=False))
    return 0


def cmd_ship(args):
    cfg, h = load_cfg(), _health()
    rows = read_ledger()
    if any(r["post"] == args.post for r in rows):
        raise SystemExit(f"{args.post} is already in ledger.csv")
    key = args.key.lower()
    e = {}
    if h and key.startswith("gh:"):
        today = D(h["today"])
        obs, ndays = load_obs(today, cfg["window_days"])
        e = build(today, obs, cfg, load_keywords(), ndays, rows).get(key) or {}
    row = {"date": now().date().isoformat(), "post": args.post, "key": key, "angle": args.angle,
           "rubric": "" if args.rubric is None else str(args.rubric), "tags": ",".join(e.get("tags") or []),
           "status_at_pick": e.get("status") or "", "plays_72h": "", "outcome": ""}
    write_ledger(rows + [row])
    print(row)
    return 0


def cmd_plays(args):
    rows = read_ledger()
    hit = [r for r in rows if r["post"] == args.post]
    if not hit:
        raise SystemExit(f"{args.post} is not in ledger.csv")
    r = hit[0]
    r["plays_72h"] = str(args.n)
    prev = [int(x["plays_72h"]) for x in sorted(rows, key=lambda x: x["date"])
            if x["date"] < r["date"] and x.get("plays_72h")][-10:]
    med = statistics.median(prev) if len(prev) >= 3 else None
    r["outcome"] = ("ok" if med is None else "breakout" if args.n >= 3 * med else "flop" if args.n < 0.5 * med else "ok")
    write_ledger(rows)
    print(r)
    return 0


def main():
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect").set_defaults(fn=cmd_collect)
    p = sub.add_parser("ask")
    p.add_argument("--no-pull", action="store_true")
    p.set_defaults(fn=cmd_ask)
    p = sub.add_parser("show")
    p.add_argument("key")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("ship")
    p.add_argument("post", help="any id you like: a date, a title slug, a URL")
    p.add_argument("key", help="gh:owner/repo from the radar, or any short label for an off-radar topic")
    p.add_argument("--angle", default="")
    p.add_argument("--rubric", type=int)
    p.set_defaults(fn=cmd_ship)
    p = sub.add_parser("plays")
    p.add_argument("post")
    p.add_argument("n", type=int)
    p.set_defaults(fn=cmd_plays)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
