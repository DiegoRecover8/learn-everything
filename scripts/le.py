#!/usr/bin/env python3
"""Learn Everything - data engine for the /learn skill.

Single file, Python 3.8+ standard library only.
All state lives under ~/learn-everything (override with LEARN_EVERYTHING_HOME):

    profile.json                 global: learner, xp, streak days, achievements
    <exam>/exam.json             exam config + roadmap (units -> topics)
    <exam>/cards.json            question bank + SM-2 state per card
    <exam>/log.jsonl             one line per recorded session
    <exam>/sources/index.json    metadata of ingested materials
    <exam>/sources/<id>.md       extracted study notes (written by Claude)

Statistics are always computed on read, never stored.
Every command prints one JSON object on stdout; errors go to stderr as JSON.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "0.2.0"
TAG = "[Learn Everything]"

SUPPORTED = {
    ".pdf": "pdf", ".docx": "docx", ".pptx": "pptx",
    ".txt": "text", ".md": "text", ".csv": "text",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".gif": "image",
}
READ_WITH = {
    "pdf": "Read tool (use the `pages` parameter, max 20 pages per call)",
    "image": "Read tool",
    "text": "Read tool",
    "docx": "le.py extract <file>",
    "pptx": "le.py extract <file>",
}
CARD_TYPES = ("mcq", "multi", "tf", "cloze", "short", "essay", "order", "match")
BACKUPS_KEPT = 10
MAX_FREEZES = 2

XP_ANSWER = {5: 10, 4: 10, 3: 7, 2: 2, 1: 2, 0: 2}
XP_TOPIC = 20
XP_GOAL = 30
XP_MOCK = 50

# id, icon. Names and descriptions live in scripts/i18n/<lang>.json
ACHIEVEMENTS = [
    ("first_session", "🎯"), ("streak_3", "🔥"), ("streak_7", "🔥"), ("streak_14", "🔥"),
    ("streak_30", "🏆"), ("answers_50", "✏️"), ("answers_200", "✏️"), ("answers_500", "🏆"),
    ("perfect_session", "💎"), ("topic_mastered", "🌟"), ("unit_complete", "📦"), ("halfway", "⛰️"),
    ("roadmap_complete", "🗺️"), ("first_mock", "📝"), ("mock_80", "🥇"), ("comeback", "💪"),
]
EDU_LEVELS = ("secondary", "university", "postgraduate", "professional")
FIND_DIRS = ("Downloads", "Desktop", "Documents")
FIND_SKIP = {"node_modules", "Library", "Applications", "venv", ".venv", "__pycache__", "site-packages"}
I18N_DIR = Path(__file__).resolve().parent / "i18n"


# ----------------------------------------------------------------------------
# basics
# ----------------------------------------------------------------------------

class LEError(Exception):
    pass


def home():
    env = os.environ.get("LEARN_EVERYTHING_HOME")
    return Path(env).expanduser() if env else Path.home() / "learn-everything"


def today():
    env = os.environ.get("LE_TODAY")  # test hook
    return dt.date.fromisoformat(env) if env else dt.date.today()


def parse_date(value, field="date"):
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise LEError("%s must be YYYY-MM-DD, got %r" % (field, value))


def out(obj):
    """Compact JSON on stdout: it goes straight into the model's context."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def load(path, default=None):
    path = Path(path)
    if not path.exists():
        if default is None:
            raise LEError("missing file: %s" % path)
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    """Atomic write: temp file in the same folder, fsync, then replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))


def read_stdin_json():
    raw = sys.stdin.read()
    if not raw.strip():
        raise LEError("expected a JSON payload on stdin")
    try:
        return json.loads(raw)
    except ValueError as e:
        raise LEError("invalid JSON on stdin: %s" % e)


def slugify(text):
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60] or "exam"


_I18N = {}


def i18n():
    """All language packs in scripts/i18n, keyed by code (en, es, ...)."""
    if not _I18N:
        for p in sorted(I18N_DIR.glob("*.json")):
            try:
                _I18N[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return _I18N


def lang_code(text):
    """Map a language as the learner wrote it ("Español", "es", "🇫🇷 Français") to a pack code."""
    t = re.sub(r"[^\w\s-]", "", str(text or ""), flags=re.U).strip().lower()
    packs = i18n()
    if t[:2] in packs and (len(t) == 2 or t[2:3] in ("-", "_")):
        return t[:2]
    first = t.split()[0] if t.split() else ""
    for code, pack in packs.items():
        names = [str(pack.get("name", "")).lower()] + [str(a).lower() for a in pack.get("aliases", [])]
        if t in names or first in names:
            return code
    return "en"


def lang_of(profile):
    return lang_code((profile or {}).get("language", ""))


def texts(lang, section):
    """One section of a language pack, with English filling any gap."""
    packs = i18n()
    merged = dict(packs.get("en", {}).get(section, {}))
    merged.update(packs.get(lang, {}).get(section, {}))
    return merged


def env_lang():
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var, "")
        if val and val[:2].lower() in i18n():
            return val[:2].lower()
    return "en"


def suggested_name():
    try:
        import pwd
        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
        if full:
            return full
    except Exception:
        pass
    return os.environ.get("USER") or os.environ.get("USERNAME") or ""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------
# storage
# ----------------------------------------------------------------------------

def profile_path():
    return home() / "profile.json"


def get_profile(required=True):
    p = load(profile_path(), default={})
    if not p and required:
        raise LEError("no profile yet - run /learn:setup")
    return p


def exam_dir(profile, slug=None):
    slug = slug or profile.get("active_exam")
    if not slug:
        raise LEError("no active exam - run /learn:setup")
    d = home() / slug
    if not (d / "exam.json").exists():
        raise LEError("exam %r not found" % slug)
    return d


def list_exams():
    h = home()
    if not h.exists():
        return []
    return sorted(d.name for d in h.iterdir()
                  if d.is_dir() and not d.name.startswith(".") and (d / "exam.json").exists())


def read_log(d):
    path = d / "log.jsonl"
    if not path.exists():
        return []
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except ValueError:
                    pass  # a torn last line must never break the app
    return lines


def append_log(d, entry):
    with open(d / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def backup(label, d=None):
    """Copy the small state files before a write. Keeps the last BACKUPS_KEPT."""
    root = home() / ".backups"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = root / ("%s-%s" % (stamp, label))
    files = [(profile_path(), "profile.json")]
    if d is not None:
        for rel in ("exam.json", "cards.json", "log.jsonl", "sources/index.json"):
            files.append((d / rel, "%s/%s" % (d.name, rel)))
    copied = False
    for src, rel in files:
        if src.exists():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(target))
            copied = True
    if not copied:
        return None
    old = sorted(p for p in root.iterdir() if p.is_dir())
    for p in old[:-BACKUPS_KEPT]:
        shutil.rmtree(str(p), ignore_errors=True)
    return dest


# ----------------------------------------------------------------------------
# learning logic (deterministic - never decided by the model)
# ----------------------------------------------------------------------------

def level_info(xp):
    lvl = 1
    while xp >= 50 * lvl * (lvl + 1):
        lvl += 1
    return {"level": lvl, "xp": xp, "floor": 50 * (lvl - 1) * lvl, "next": 50 * lvl * (lvl + 1)}


def streak(profile, day=None):
    """Consecutive study days ending today or yesterday. Freeze days bridge
    a gap without adding to the count."""
    day = day or today()
    studied = set(profile.get("study_days", []))
    frozen = set(profile.get("freeze_days", []))
    cur = day if day.isoformat() in studied else day - dt.timedelta(days=1)
    n = 0
    while True:
        key = cur.isoformat()
        if key in studied:
            n += 1
        elif key not in frozen:
            break
        cur -= dt.timedelta(days=1)
    return n


def sm2(card, quality, day, exam_date=None):
    """Standard SM-2. With an exam date, an interval that would jump past the
    exam is cut to half the remaining days so every card comes back in time."""
    ef, interval, reps = card.get("ef", 2.5), card.get("interval", 0), card.get("reps", 0)
    if quality >= 3:
        interval = 1 if reps == 0 else 6 if reps == 1 else int(math.ceil(interval * ef))
        reps += 1
    else:
        reps, interval = 0, 1
        card["lapses"] = card.get("lapses", 0) + 1
    ef = max(1.3, ef + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if exam_date is not None:
        left = (exam_date - day).days
        if left > 0 and interval >= left:
            interval = max(1, left // 2)
    card.update(ef=round(ef, 2), interval=interval, reps=reps,
                due=(day + dt.timedelta(days=interval)).isoformat())
    card.setdefault("history", []).append({"d": day.isoformat(), "q": quality})
    card["history"] = card["history"][-20:]
    return card


def last_q(card):
    hist = card.get("history") or []
    return hist[-1]["q"] if hist else None


def topic_status(topic, tcards):
    """new -> learning -> reviewing -> mastered. Can go down again."""
    if not topic.get("studied"):
        return "new"
    if not tcards:
        return "learning"
    n = len(tcards)
    strong = sum(1 for c in tcards if c.get("reps", 0) >= 3 and (last_q(c) or 0) >= 4)
    ok = sum(1 for c in tcards if c.get("reps", 0) >= 1)
    if strong / n >= 0.8:
        return "mastered"
    if ok / n >= 0.6:
        return "reviewing"
    return "learning"


def all_topics(exam):
    return [(u, t) for u in exam.get("units", []) for t in u.get("topics", [])]


def cards_by_topic(cards):
    by = {}
    for c in cards["cards"]:
        by.setdefault(c["topic"], []).append(c)
    return by


def statuses(exam, cards):
    by = cards_by_topic(cards)
    return {t["id"]: topic_status(t, by.get(t["id"], [])) for _, t in all_topics(exam)}


def make_plan(exam, cards, log, day=None):
    day = day or today()
    key = day.isoformat()
    topics = [t for _, t in all_topics(exam)]
    pending = [t for t in topics if not t.get("studied")]
    studied_today = sum(1 for t in topics if t.get("studied") == key)
    minutes = int(exam.get("daily_minutes", 30))
    review_cap = max(5, int(round(minutes * 2 / 3)))
    due_now = sum(1 for c in cards["cards"] if c.get("due", key) <= key)
    reviews_done = sum(e.get("reviews", 0) for e in log if e.get("date") == key)

    plan = {"days_left": None, "phase": "learn", "finish_estimate": None, "overload": False}
    if exam.get("exam_date"):
        left = (parse_date(exam["exam_date"]) - day).days
        plan["days_left"] = left
        if left <= 0:
            per_day = len(pending) + studied_today
            plan["phase"] = "exam_day" if left == 0 else "past"
        else:
            reserve = min(max(2, int(round(left * 0.2))), max(0, left - 1))
            per_day = int(math.ceil((len(pending) + studied_today) / max(1, left - reserve)))
    else:
        per_day = max(1, minutes // 30)
        if pending:
            days = int(math.ceil(len(pending) / per_day))
            plan["finish_estimate"] = (day + dt.timedelta(days=days)).isoformat()
    if not pending and plan["phase"] == "learn":
        plan["phase"] = "review"
    plan.update(
        daily_minutes=minutes,
        topics_total=len(topics),
        topics_pending=len(pending),
        new_per_day=per_day,
        new_today_remaining=max(0, min(len(pending), per_day - studied_today)),
        due_now=due_now,
        review_cap=review_cap,
        reviews_today_remaining=max(0, min(due_now, review_cap - reviews_done)),
    )
    plan["overload"] = bool(pending) and per_day * 12 > minutes
    return plan


def validate_card(c, topic_ids):
    """Return a normalised card dict or raise LEError."""
    if not isinstance(c, dict):
        raise LEError("card must be an object")
    kind, prompt = c.get("type"), c.get("prompt")
    where = "card %r" % (str(prompt)[:40],)
    if kind not in CARD_TYPES:
        raise LEError("%s: type must be one of %s" % (where, ", ".join(CARD_TYPES)))
    if not isinstance(prompt, str) or not prompt.strip():
        raise LEError("card: prompt is required")
    if c.get("topic") not in topic_ids:
        raise LEError("%s: unknown topic %r" % (where, c.get("topic")))
    ans, opts = c.get("answer"), c.get("options")

    def strs(x, n=1):
        return isinstance(x, list) and len(x) >= n and all(isinstance(i, str) and i.strip() for i in x)

    if kind == "mcq":
        if not strs(opts, 2) or ans not in opts:
            raise LEError("%s: mcq needs options (2+) and answer equal to one option" % where)
    elif kind == "multi":
        if not strs(opts, 2) or not strs(ans) or any(a not in opts for a in ans):
            raise LEError("%s: multi needs options (2+) and answer as a list of options" % where)
    elif kind == "tf":
        if not isinstance(ans, bool):
            raise LEError("%s: tf answer must be true or false" % where)
    elif kind in ("cloze", "short", "essay"):
        if not isinstance(ans, str) or not ans.strip():
            raise LEError("%s: answer must be a non-empty string" % where)
        if kind == "cloze" and "___" not in prompt:
            raise LEError("%s: cloze prompt must mark the gap with ___" % where)
    elif kind == "order":
        if not strs(ans, 2):
            raise LEError("%s: order answer must list 2+ items in the correct order" % where)
    elif kind == "match":
        ok = isinstance(ans, list) and len(ans) >= 2 and all(strs(p, 2) and len(p) == 2 for p in ans)
        if not ok:
            raise LEError("%s: match answer must be a list of [left, right] pairs" % where)
    card = {"topic": c["topic"], "type": kind, "prompt": prompt.strip(), "answer": ans}
    if opts is not None and kind in ("mcq", "multi"):
        card["options"] = opts
    for key in ("accepted", "rubric"):
        if c.get(key):
            if not strs(c[key]):
                raise LEError("%s: %s must be a list of strings" % (where, key))
            card[key] = c[key]
    for key in ("explanation", "source"):
        if c.get(key):
            card[key] = str(c[key])
    return card


def new_card(cards, card, day):
    card["id"] = "c%d" % cards["next_id"]
    cards["next_id"] += 1
    card.update(ef=2.5, interval=0, reps=0, lapses=0, history=[],
                created=day.isoformat(), due=(day + dt.timedelta(days=1)).isoformat())
    cards["cards"].append(card)
    return card


def check_achievements(profile, ctx):
    have = {a["id"] for a in profile.setdefault("achievements", [])}
    rules = {
        "first_session": True,
        "streak_3": ctx["streak"] >= 3, "streak_7": ctx["streak"] >= 7,
        "streak_14": ctx["streak"] >= 14, "streak_30": ctx["streak"] >= 30,
        "answers_50": ctx["total_answers"] >= 50, "answers_200": ctx["total_answers"] >= 200,
        "answers_500": ctx["total_answers"] >= 500,
        "perfect_session": ctx["answers"] >= 5 and ctx["correct"] == ctx["answers"],
        "topic_mastered": ctx["mastered"] >= 1,
        "unit_complete": ctx["unit_complete"],
        "halfway": ctx["topics_total"] >= 2 and ctx["studied"] * 2 >= ctx["topics_total"],
        "roadmap_complete": ctx["topics_total"] >= 1 and ctx["studied"] == ctx["topics_total"],
        "first_mock": ctx["mock"],
        "mock_80": ctx["mock"] and ctx["mock_pct"] >= 80,
        "comeback": ctx["comeback"],
    }
    names, new = texts(lang_of(profile), "achievements"), []
    for aid, icon in ACHIEVEMENTS:
        if rules.get(aid) and aid not in have:
            profile["achievements"].append({"id": aid, "date": ctx["date"]})
            new.append({"id": aid, "icon": icon, "name": names[aid][0], "description": names[aid][1]})
    return new


# ----------------------------------------------------------------------------
# commands: setup and exams
# ----------------------------------------------------------------------------

EXAM_FIELDS = ("name", "exam_date", "daily_minutes", "goal", "exam_type", "notes")


def apply_exam_fields(exam, data):
    for key in EXAM_FIELDS:
        if key not in data:
            continue
        val = data[key]
        if key == "exam_date" and val:
            val = parse_date(val, "exam_date").isoformat()
        elif key == "exam_date":
            val = None
        elif key == "daily_minutes":
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise LEError("daily_minutes must be a number")
            if not 5 <= val <= 480:
                raise LEError("daily_minutes must be between 5 and 480")
        exam[key] = val


def cmd_init(args):
    data = read_stdin_json()
    day = today().isoformat()
    profile = get_profile(required=False)
    created_profile = not profile
    if created_profile:
        profile = {"version": 1, "created": day, "name": "", "language": "English",
                   "education_level": "", "active_exam": None, "xp": 0, "total_answers": 0,
                   "study_days": [], "freeze_days": [], "goal_days": [], "freezes": 0,
                   "achievements": []}
    for key in ("name", "language", "education_level"):
        if key in data:
            profile[key] = data[key]

    result = {"profile": "created" if created_profile else "updated"}
    ex = data.get("exam")
    d = None
    if ex:
        if not isinstance(ex, dict):
            raise LEError("exam must be an object")
        slug = ex.get("slug") or slugify(ex.get("name", ""))
        d = home() / slug
        if (d / "exam.json").exists():
            exam = load(d / "exam.json")
            backup("init", d)
            apply_exam_fields(exam, ex)
            if ex.get("rename") and ex.get("name") and slugify(ex["name"]) != slug:
                new_slug = slugify(ex["name"])
                if (home() / new_slug).exists():
                    raise LEError("an exam folder named %r already exists" % new_slug)
                exam["slug"] = new_slug
                save(d / "exam.json", exam)
                os.replace(str(d), str(home() / new_slug))
                slug, d = new_slug, home() / new_slug
                result["renamed_to"] = new_slug
            else:
                save(d / "exam.json", exam)
            result["exam"] = "updated"
        else:
            if not ex.get("name"):
                raise LEError("exam.name is required to create an exam")
            exam = {"version": 1, "slug": slug, "name": ex["name"], "created": day,
                    "exam_date": None, "daily_minutes": 30, "goal": "pass", "exam_type": "mixed",
                    "next_topic_id": 1, "units": []}
            apply_exam_fields(exam, ex)
            (d / "sources").mkdir(parents=True, exist_ok=True)
            save(d / "exam.json", exam)
            save(d / "cards.json", {"version": 1, "next_id": 1, "cards": []})
            save(d / "sources" / "index.json", {"version": 1, "next_id": 1, "sources": []})
            (d / "log.jsonl").touch()
            result["exam"] = "created"
        profile["active_exam"] = slug
        result["slug"] = slug
        result["folder"] = str(d)
    save(profile_path(), profile)
    result["home"] = str(home())
    out(result)


def topic_accuracy(tcards):
    hist = [h["q"] for c in tcards for h in c.get("history", [])]
    if not hist:
        return None, 0
    return int(round(100.0 * sum(1 for q in hist if q >= 3) / len(hist))), len(hist)


def focus_topics(exam, cards, limit=2):
    """Strongest and weakest studied topics, from their answer history."""
    by, rows = cards_by_topic(cards), []
    for _, t in all_topics(exam):
        acc, n = topic_accuracy(by.get(t["id"], []))
        if t.get("studied") and acc is not None and n >= 3:
            rows.append({"id": t["id"], "name": t["name"], "accuracy": acc, "answers": n})
    strong = [r for r in sorted(rows, key=lambda r: (-r["accuracy"], -r["answers"])) if r["accuracy"] >= 80]
    weak = [r for r in sorted(rows, key=lambda r: (r["accuracy"], -r["answers"])) if r["accuracy"] < 70]
    return strong[:limit], weak[:limit]


def recap(exam, cards, log):
    """What a returning learner needs to hear first: last session, strengths, gaps, next topic."""
    names = {t["id"]: t["name"] for _, t in all_topics(exam)}
    studied = sorted((t for _, t in all_topics(exam) if t.get("studied")), key=lambda t: t["studied"])
    strong, weak = focus_topics(exam, cards)
    last = None
    if log:
        e = log[-1]
        last = {"date": e.get("date"), "kind": e.get("kind", "study"), "answers": e.get("answers", 0),
                "correct": e.get("correct", 0), "topics": [names[x] for x in e.get("topics", []) if x in names]}
        if e.get("mock"):
            last["mock_pct"] = e["mock"].get("pct")
    return {"last_session": last, "recent_topics": [t["name"] for t in studied[-3:]],
            "strong": [r["name"] for r in strong], "weak": [r["name"] for r in weak],
            "next_topic": next((t["name"] for _, t in all_topics(exam) if not t.get("studied")), None)}


def summary(profile, d):
    exam, cards, log = load(d / "exam.json"), load(d / "cards.json"), read_log(d)
    index = load(d / "sources" / "index.json", default={"sources": []})
    plan = make_plan(exam, cards, log)
    st = statuses(exam, cards)
    missing = [s["id"] for s in index["sources"] if not (d / s["notes"]).exists()]
    if not index["sources"]:
        action = "add"
    elif missing:
        action = "add"
    elif not st:
        action = "roadmap"
    elif plan["reviews_today_remaining"] or plan["new_today_remaining"]:
        action = "study"
    else:
        action = "done"
    return {
        "exam": {"slug": exam["slug"], "name": exam["name"], "exam_date": exam.get("exam_date"),
                 "goal": exam.get("goal"), "exam_type": exam.get("exam_type")},
        "plan": plan,
        "topics": {"total": len(st), "studied": sum(1 for v in st.values() if v != "new"),
                   "mastered": sum(1 for v in st.values() if v == "mastered")},
        "cards": len(cards["cards"]),
        "sources": len(index["sources"]),
        "sources_missing_notes": missing,
        "recap": recap(exam, cards, log),
        "next_action": action,
    }


def hook_lines(profile, info):
    T = texts(lang_of(profile), "hook")
    plan, name, left = info["plan"], info["exam"]["name"], info["plan"]["days_left"]
    plural = lambda n: T["day"] if n == 1 else T["days"]
    if left is None:
        lines = [T["exam_nodate"].format(exam=name)]
    elif left > 0:
        lines = [T["exam_days"].format(exam=name, n=left, days=plural(left))]
    else:
        lines = [(T["exam_today"] if left == 0 else T["exam_past"]).format(exam=name)]
    lv, n = level_info(profile.get("xp", 0)), streak(profile)
    lines.append(T["streak"].format(n=n, days=plural(n), level=lv["level"], xp=lv["xp"]))
    tp, rc = info["topics"], info["recap"]
    if tp["studied"]:
        lines.append(T["progress"].format(studied=tp["studied"], total=tp["total"], mastered=tp["mastered"]))
    last = rc.get("last_session")
    if last and last.get("date"):
        ago = (today() - parse_date(last["date"])).days
        when = T["when_today"] if ago <= 0 else T["when_yesterday"] if ago == 1 else T["when_ago"].format(n=ago)
        parts = []
        if last.get("mock_pct") is not None:
            parts.append(T["mock"].format(pct=last["mock_pct"]))
        elif last.get("topics"):
            parts.append(", ".join(last["topics"]))
        if last.get("answers"):
            parts.append(T["score"].format(correct=last["correct"], answers=last["answers"]))
        if parts:
            lines.append(T["last"].format(when=when, what=" · ".join(parts)))
    focus = []
    if rc.get("strong"):
        focus.append(T["strong"].format(list=", ".join(rc["strong"])))
    if rc.get("weak"):
        focus.append(T["weak"].format(list=", ".join(rc["weak"])))
    if focus:
        lines.append(" · ".join(focus))
    action = info["next_action"]
    if action in ("add", "roadmap", "done"):
        lines.append(T[action])
    else:
        r, t = plan["reviews_today_remaining"], plan["new_today_remaining"]
        lines.append(T["today"].format(reviews=T["review_1" if r == 1 else "review_n"].format(n=r),
                                       topics=T["topic_1" if t == 1 else "topic_n"].format(n=t)))
    return lines


def cmd_status(args):
    if args.hook:
        try:
            profile = get_profile(required=False)
            if not profile or not profile.get("active_exam"):
                lang = lang_of(profile) if profile else env_lang()
                print("%s %s" % (TAG, texts(lang, "hook")["setup"]))
                return
            info = summary(profile, exam_dir(profile))
            for line in hook_lines(profile, info):
                print("%s %s" % (TAG, line))
        except Exception:  # a hook must never break the session
            pass
        return
    profile = get_profile(required=False)
    if not profile:
        out({"next_action": "setup", "home": str(home()),
             "suggest": {"name": suggested_name(), "language": i18n().get(env_lang(), {}).get("name", "English")},
             "languages": [{"code": c, "name": p.get("name"), "flag": p.get("flag")} for c, p in i18n().items()],
             "education_levels": list(EDU_LEVELS)})
        return
    res = {"learner": {"name": profile.get("name"), "language": profile.get("language"),
                       "education_level": profile.get("education_level")},
           "streak": streak(profile), "studied_today": today().isoformat() in profile.get("study_days", []),
           "freezes": profile.get("freezes", 0), "level": level_info(profile.get("xp", 0)),
           "achievements": len(profile.get("achievements", [])), "exams": list_exams(),
           "home": str(home())}
    if not profile.get("active_exam") or args.exam is None and profile["active_exam"] not in res["exams"]:
        res["next_action"] = "setup"
    else:
        res.update(summary(profile, exam_dir(profile, args.exam)))
    out(res)


def cmd_list(args):
    profile = get_profile(required=False)
    rows = []
    for slug in list_exams():
        d = home() / slug
        info = summary(profile, d)
        rows.append({"slug": slug, "name": info["exam"]["name"], "exam_date": info["exam"]["exam_date"],
                     "active": slug == profile.get("active_exam"), "topics": info["topics"],
                     "cards": info["cards"], "sources": info["sources"]})
    out({"exams": rows, "home": str(home())})


def cmd_switch(args):
    profile = get_profile()
    exam_dir(profile, args.slug)
    profile["active_exam"] = args.slug
    save(profile_path(), profile)
    out({"active_exam": args.slug})


# ----------------------------------------------------------------------------
# commands: sources
# ----------------------------------------------------------------------------

def expand_paths(paths):
    files = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for sub in sorted(p.rglob("*")):
                if sub.is_file() and not any(part.startswith(".") for part in sub.relative_to(p).parts):
                    if sub.suffix.lower() in SUPPORTED:
                        files.append(sub)
        else:
            files.append(p)
    return files


def cmd_source_check(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    index = load(d / "sources" / "index.json")
    known = {s["sha256"]: s for s in index["sources"] if s.get("sha256")}
    rows = []
    for p in expand_paths(args.paths):
        row = {"path": str(p)}
        kind = SUPPORTED.get(p.suffix.lower())
        if not p.is_file():
            row["status"] = "not_found"
        elif not kind:
            row["status"] = "unsupported"
        else:
            digest = sha256(p)
            row.update(type=kind, size_kb=int(math.ceil(p.stat().st_size / 1024)))
            if digest in known:
                s = known[digest]
                row.update(status="known", id=s["id"], notes=str(d / s["notes"]))
            else:
                row.update(status="new", read_with=READ_WITH[kind])
        rows.append(row)
    fresh = [r for r in rows if r["status"] == "new"]
    out({"files": rows, "new": len(fresh), "known": sum(1 for r in rows if r["status"] == "known"),
         "new_size_kb": sum(r["size_kb"] for r in fresh)})


def cmd_source_add(args):
    data = read_stdin_json()
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    index = load(d / "sources" / "index.json")
    meta = {k: data[k] for k in ("title", "summary", "pages", "topics") if k in data}
    if data.get("pasted"):
        # material pasted or attached in the chat, with no file on disk: no hash, no dedupe
        if not data.get("title"):
            raise LEError("a pasted source needs a title")
        sid = "s%d" % index["next_id"]
        index["next_id"] += 1
        entry = {"id": sid, "sha256": None, "path": None, "name": str(data["title"]), "type": "pasted",
                 "size_kb": 0, "added": today().isoformat(), "notes": "sources/%s.md" % sid}
        entry.update(meta)
        index["sources"].append(entry)
        backup("source", d)
        save(d / "sources" / "index.json", index)
        out({"status": "added", "id": sid, "notes_path": str(d / entry["notes"])})
        return
    p = Path(str(data.get("path", ""))).expanduser()
    if not p.is_file():
        raise LEError("path not found: %s" % p)
    kind = SUPPORTED.get(p.suffix.lower())
    if not kind:
        raise LEError("unsupported file type: %s" % p.suffix)
    digest = sha256(p)
    for s in index["sources"]:
        if s["sha256"] == digest:
            s.update(meta)
            backup("source", d)
            save(d / "sources" / "index.json", index)
            out({"status": "known", "id": s["id"], "notes_path": str(d / s["notes"])})
            return
    sid = "s%d" % index["next_id"]
    index["next_id"] += 1
    entry = {"id": sid, "sha256": digest, "path": str(p.resolve()), "name": p.name, "type": kind,
             "size_kb": int(math.ceil(p.stat().st_size / 1024)), "added": today().isoformat(),
             "notes": "sources/%s.md" % sid}
    entry.update(meta)
    index["sources"].append(entry)
    backup("source", d)
    save(d / "sources" / "index.json", index)
    out({"status": "added", "id": sid, "notes_path": str(d / entry["notes"])})


def cmd_source_list(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    index = load(d / "sources" / "index.json")
    rows = []
    for s in index["sources"]:
        row = {k: s.get(k) for k in ("id", "name", "type", "pages", "title", "summary", "added")}
        row["notes_path"] = str(d / s["notes"])
        row["has_notes"] = (d / s["notes"]).exists()
        rows.append(row)
    out({"sources": rows})


def cmd_source_remove(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    index = load(d / "sources" / "index.json")
    keep = [s for s in index["sources"] if s["id"] != args.id]
    if len(keep) == len(index["sources"]):
        raise LEError("source %r not found" % args.id)
    gone = [s for s in index["sources"] if s["id"] == args.id][0]
    backup("source", d)
    notes = d / gone["notes"]
    if notes.exists():
        trash = home() / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(notes), str(trash / ("%s-%s-%s" % (d.name, today().isoformat(), notes.name))))
    index["sources"] = keep
    save(d / "sources" / "index.json", index)
    out({"removed": args.id, "note": "cards and roadmap were left untouched"})


def cmd_find(args):
    """Recent study files in the usual folders, so the learner can pick instead of typing paths."""
    roots = [Path(x).expanduser() for x in args.dirs] if args.dirs else \
        [Path.home() / n for n in FIND_DIRS if (Path.home() / n).is_dir()]
    known = set()
    profile = get_profile(required=False)
    if profile and profile.get("active_exam") and (home() / profile["active_exam"]).is_dir():
        index = load(home() / profile["active_exam"] / "sources" / "index.json", default={"sources": []})
        known = {s.get("path") for s in index["sources"] if s.get("path")}
    cutoff = dt.datetime.now().timestamp() - args.days * 86400
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        base = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(str(root)):
            depth = len(Path(dirpath).parts) - base
            dirnames[:] = [n for n in dirnames if not n.startswith(".") and n not in FIND_SKIP
                           and not n.endswith((".app", ".photoslibrary")) and depth < args.depth]
            for name in filenames:
                p = Path(dirpath) / name
                if name.startswith(".") or p.suffix.lower() not in SUPPORTED:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime >= cutoff:
                    found.append((st.st_mtime, p, st.st_size))
    found.sort(key=lambda x: -x[0])
    rows = [{"path": str(p), "type": SUPPORTED[p.suffix.lower()], "size_kb": int(math.ceil(size / 1024)),
             "modified": dt.date.fromtimestamp(mtime).isoformat(), "known": str(p.resolve()) in known}
            for mtime, p, size in found[:args.limit]]
    out({"files": rows, "searched": [str(r) for r in roots], "more": max(0, len(found) - args.limit)})


PAGE_MARK = re.compile(r"\[\s*(?:p|pp|pg|pág|pag|page|pages|slide|slides|diapositiva|diapo|folie)\.?\s*(\d+)", re.I)
REF = re.compile(r"\s*(s\d+)(?:\D*?(\d+)(?:\s*[-–]\s*(\d+))?)?")


def notes_excerpt(text, ranges):
    """Lines of the notes whose page mark falls in one of the ranges, with their headings.
    A line takes the page of its own mark, or of the last mark above it."""
    lines, page, keep = text.split("\n"), None, []
    for i, line in enumerate(lines):
        marks = [int(m) for m in PAGE_MARK.findall(line)]
        if marks:
            page = marks[0]
        elif re.match(r"#+\s", line):
            continue  # an unmarked heading belongs to what follows; added below with its section
        if page is not None and any(a <= page <= b for a, b in ranges):
            keep.append(i)
    if not keep:
        return None
    chosen, heads = set(keep), {}
    for i, line in enumerate(lines):
        m = re.match(r"(#+)\s", line)
        if m:
            level = len(m.group(1))
            heads = {k: v for k, v in heads.items() if k < level}
            heads[level] = i
        if i in keep:
            chosen.update(heads.values())
    out_lines, prev = [], None
    for i in sorted(chosen):
        if prev is not None and i != prev + 1:
            out_lines.append("…")
        out_lines.append(lines[i])
        prev = i
    return "\n".join(out_lines)


def cmd_notes(args):
    """Print only the part of the notes a topic needs, instead of whole files."""
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    refs = args.ref
    if args.topic:
        exam = load(d / "exam.json")
        t = next((t for _, t in all_topics(exam) if t["id"] == args.topic), None)
        if t is None:
            raise LEError("unknown topic %r" % args.topic)
        refs = refs + t.get("sources", [])
    if not refs:
        raise LEError("give a topic id or --ref \"s1 p.3-8\"")
    wanted = {}
    for ref in refs:
        m = REF.match(str(ref))
        if not m:
            continue
        rng = wanted.setdefault(m.group(1), [])
        if m.group(2) is None:
            rng.append(None)
        else:
            a = int(m.group(2))
            rng.append((a, int(m.group(3)) if m.group(3) else a))
    chunks = []
    for sid, ranges in wanted.items():
        path = d / "sources" / (sid + ".md")
        if not path.exists():
            chunks.append("=== %s: no notes file ===" % sid)
            continue
        text = path.read_text(encoding="utf-8")
        part = None if None in ranges else notes_excerpt(text, ranges)
        label = "whole file" if part is None else "pages " + ", ".join(
            "%d-%d" % r if r[0] != r[1] else str(r[0]) for r in ranges)
        chunks.append("=== %s (%s) · %s ===\n%s" % (sid, label, path, (part if part is not None else text).strip()))
    sys.stdout.write("\n\n".join(chunks) + "\n")


# ----------------------------------------------------------------------------
# commands: extract (docx / pptx, stdlib only)
# ----------------------------------------------------------------------------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _docx_par(p):
    parts = []
    for node in p.iter():
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            parts.append("\n")
    text = "".join(parts).strip()
    style = p.find("%spPr/%spStyle" % (W, W))
    if text and style is not None:
        m = re.search(r"(?:heading|ttulo|titulo|título)\s*(\d)", style.get(W + "val", ""), re.I)
        if m:
            text = "#" * min(6, int(m.group(1))) + " " + text
    return text


def extract_docx(z):
    body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
    lines = []
    for el in list(body) if body is not None else []:
        if el.tag == W + "p":
            text = _docx_par(el)
            if text:
                lines.append(text)
        elif el.tag == W + "tbl":
            for tr in el.iter(W + "tr"):
                cells = [" ".join(filter(None, (_docx_par(p) for p in tc.iter(W + "p"))))
                         for tc in tr.findall(W + "tc")]
                lines.append("| " + " | ".join(cells) + " |")
    return "\n\n".join(lines)


def _pptx_text(xml_bytes):
    root = ET.fromstring(xml_bytes)
    pars = []
    for p in root.iter(A + "p"):
        text = "".join(t.text or "" for t in p.iter(A + "t")).strip()
        if text:
            pars.append(text)
    return pars


def extract_pptx(z):
    names = z.namelist()
    slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                    key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    chunks = []
    for i, name in enumerate(slides, 1):
        chunk = ["## Slide %d" % i] + _pptx_text(z.read(name))
        rels = "ppt/slides/_rels/%s.rels" % name.rsplit("/", 1)[1]
        if rels in names:
            for rel in ET.fromstring(z.read(rels)):
                target = rel.get("Target", "")
                if "notesSlide" in target:
                    note = "ppt/notesSlides/" + target.rsplit("/", 1)[1]
                    if note in names:
                        text = [t for t in _pptx_text(z.read(note)) if not t.isdigit()]
                        if text:
                            chunk.append("Notes: " + " ".join(text))
        chunks.append("\n".join(chunk))
    return "\n\n".join(chunks)


def cmd_extract(args):
    p = Path(args.file).expanduser()
    kind = SUPPORTED.get(p.suffix.lower())
    if kind not in ("docx", "pptx"):
        raise LEError("extract only handles .docx and .pptx (read other files with the Read tool)")
    try:
        with zipfile.ZipFile(str(p)) as z:
            text = extract_docx(z) if kind == "docx" else extract_pptx(z)
            media = [n for n in z.namelist() if re.match(r"(word|ppt)/media/[^/]+$", n)]
            saved = []
            if args.media and media:
                dest = Path(args.media).expanduser()
                dest.mkdir(parents=True, exist_ok=True)
                for n in media:
                    target = dest / Path(n).name
                    target.write_bytes(z.read(n))
                    saved.append(str(target))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise LEError("could not read %s: %s" % (p.name, e))
    if args.out:
        Path(args.out).expanduser().write_text(text, encoding="utf-8")
        out({"written": args.out, "chars": len(text), "embedded_images": len(media), "images_saved": saved})
    else:
        sys.stdout.write(text + "\n")
        if media and not args.media:
            sys.stderr.write("note: %d embedded images; rerun with --media <dir> to extract them\n" % len(media))


# ----------------------------------------------------------------------------
# commands: roadmap and cards
# ----------------------------------------------------------------------------

def norm(text):
    return slugify(text)


def cmd_roadmap_set(args):
    data = read_stdin_json()
    units_in = data.get("units")
    if not isinstance(units_in, list) or not units_in:
        raise LEError("payload must be {\"units\": [{\"name\", \"topics\": [{\"name\", ...}]}]}")
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    exam, cards = load(d / "exam.json"), load(d / "cards.json")
    old = {}
    for _, t in all_topics(exam):
        old[t["id"]] = t
        old.setdefault("name:" + norm(t["name"]), t)
    used, units = set(), []
    for ui, u in enumerate(units_in, 1):
        if not isinstance(u, dict) or not u.get("name") or not isinstance(u.get("topics"), list):
            raise LEError("each unit needs a name and a topics list")
        topics = []
        for t in u["topics"]:
            if not isinstance(t, dict) or not t.get("name"):
                raise LEError("each topic needs a name")
            prev = old.get(t.get("id")) or old.get("name:" + norm(t["name"]))
            if prev is not None and prev["id"] in used:
                prev = None
            if prev is not None:
                tid, studied = prev["id"], prev.get("studied")
            else:
                tid, studied = "t%d" % exam["next_topic_id"], None
                exam["next_topic_id"] += 1
            used.add(tid)
            try:
                weight = float(t.get("weight", 1))
            except (TypeError, ValueError):
                raise LEError("topic weight must be a number")
            concepts = t.get("concepts", [])
            if not isinstance(concepts, list):
                raise LEError("topic concepts must be a list of short strings")
            topics.append({"id": tid, "name": t["name"], "weight": max(0.1, weight),
                           "sources": [str(s) for s in t.get("sources", [])],
                           "summary": str(t.get("summary", "")),
                           "concepts": [str(c).strip()[:60] for c in concepts if str(c).strip()][:8],
                           "studied": studied})
        units.append({"id": "u%d" % ui, "name": u["name"], "topics": topics})
    by = cards_by_topic(cards)
    lost = [tid for tid in by if tid not in used]
    if lost and not args.drop:
        names = [old[tid]["name"] for tid in lost if tid in old]
        raise LEError("this roadmap removes topics that have cards: %s. Keep them, or rerun with "
                      "--drop to delete those cards too." % ", ".join(names))
    backup("roadmap", d)
    if lost:
        cards["cards"] = [c for c in cards["cards"] if c["topic"] in used]
        save(d / "cards.json", cards)
    exam["units"] = units
    save(d / "exam.json", exam)
    out({"units": len(units), "topics": len(used), "cards_dropped": sum(len(by[t]) for t in lost),
         "plan": make_plan(exam, cards, read_log(d))})


def cmd_roadmap_show(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    exam, cards = load(d / "exam.json"), load(d / "cards.json")
    by, st = cards_by_topic(cards), statuses(exam, cards)
    units = []
    for u in exam["units"]:
        units.append({"id": u["id"], "name": u["name"], "topics": [
            {"id": t["id"], "name": t["name"], "weight": t["weight"], "status": st[t["id"]],
             "cards": len(by.get(t["id"], [])), "sources": t["sources"]} for t in u["topics"]]})
    out({"exam": exam["name"], "units": units})


def cmd_cards_add(args):
    data = read_stdin_json()
    items = data.get("cards") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise LEError("payload must be a list of cards or {\"cards\": [...]}")
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    exam, cards = load(d / "exam.json"), load(d / "cards.json")
    ids = {t["id"] for _, t in all_topics(exam)}
    valid = [validate_card(c, ids) for c in items]
    backup("cards", d)
    added = [new_card(cards, c, today())["id"] for c in valid]
    save(d / "cards.json", cards)
    out({"added": added, "total_cards": len(cards["cards"])})


def cmd_cards_list(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    cards = load(d / "cards.json")
    rows = [{"id": c["id"], "topic": c["topic"], "type": c["type"], "prompt": c["prompt"],
             "reps": c["reps"], "lapses": c.get("lapses", 0), "due": c["due"]}
            for c in cards["cards"] if not args.topic or c["topic"] == args.topic]
    out({"cards": rows, "note": "answers are omitted on purpose"})


# ----------------------------------------------------------------------------
# commands: study loop
# ----------------------------------------------------------------------------

def source_notes(d, refs):
    paths = []
    for ref in refs:
        m = re.match(r"\s*(s\d+)", str(ref))
        if m:
            p = d / "sources" / (m.group(1) + ".md")
            if p.exists() and str(p) not in paths:
                paths.append(str(p))
    return paths


def session_header(profile, exam, kind):
    return {"kind": kind, "started": dt.datetime.now().isoformat(timespec="seconds"),
            "date": today().isoformat(),
            "learner": {"name": profile.get("name"), "language": profile.get("language"),
                        "education_level": profile.get("education_level")},
            "exam": {"name": exam["name"], "exam_type": exam.get("exam_type"), "goal": exam.get("goal"),
                     "exam_date": exam.get("exam_date")}}


def cmd_next(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    exam, cards, log = load(d / "exam.json"), load(d / "cards.json"), read_log(d)
    plan = make_plan(exam, cards, log)
    key = today().isoformat()
    due = sorted((c for c in cards["cards"] if c.get("due", key) <= key),
                 key=lambda c: (c["due"], -c.get("lapses", 0), c["id"]))
    n_reviews = plan["reviews_today_remaining"]
    n_new = plan["new_today_remaining"]
    if args.extra:
        n_reviews = min(len(due), max(n_reviews, plan["review_cap"]))
        n_new = max(n_new, 1)
    names = {t["id"]: (u["name"], t["name"]) for u, t in all_topics(exam)}
    reviews = []
    for c in due[:n_reviews]:
        item = {k: c[k] for k in ("id", "topic", "type", "prompt", "options", "answer", "accepted",
                                  "rubric", "explanation", "source") if k in c}
        item["topic_name"] = names.get(c["topic"], ("", ""))[1]
        item["lapses"] = c.get("lapses", 0)
        reviews.append(item)
    by = cards_by_topic(cards)
    new_topics = []
    for u, t in all_topics(exam):
        if len(new_topics) >= n_new:
            break
        if not t.get("studied"):
            new_topics.append({"id": t["id"], "name": t["name"], "unit": u["name"], "weight": t["weight"],
                               "summary": t.get("summary", ""), "concepts": t.get("concepts", []),
                               "sources": t["sources"], "notes_cmd": "le.py notes %s" % t["id"],
                               "notes_files": source_notes(d, t["sources"]),
                               "existing_cards": len(by.get(t["id"], []))})
    session = session_header(profile, exam, "study")
    session.update(plan=plan, reviews=reviews, new_topics=new_topics)
    path = d / ".session.json"
    save(path, session)
    out({"session_file": str(path), "reviews": len(reviews),
         "new_topics": [t["name"] for t in new_topics], "daily_minutes": plan["daily_minutes"],
         "days_left": plan["days_left"], "phase": plan["phase"], "overload": plan["overload"],
         "hint": "Read session_file with the Read tool. It holds the answers: never show it."})


def cmd_mock(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    exam, cards = load(d / "exam.json"), load(d / "cards.json")
    weights = {t["id"]: t["weight"] for _, t in all_topics(exam)}
    names = {t["id"]: t["name"] for _, t in all_topics(exam)}
    pool = [c for c in cards["cards"] if c["topic"] in weights]
    rng = random.Random(args.seed)
    scored = []
    for c in pool:
        w = weights[c["topic"]] * (1 + c.get("lapses", 0) + (1 if c.get("reps", 0) < 2 else 0))
        scored.append((rng.random() ** (1.0 / w), c))  # weighted sampling without replacement
    scored.sort(key=lambda x: -x[0])
    chosen = [c for _, c in scored[:args.n]]
    rng.shuffle(chosen)
    items = []
    for c in chosen:
        item = {k: c[k] for k in ("id", "topic", "type", "prompt", "options", "answer", "accepted",
                                  "rubric", "explanation", "source") if k in c}
        item["topic_name"] = names[c["topic"]]
        items.append(item)
    st = statuses(exam, cards)
    session = session_header(profile, exam, "mock")
    session.update(questions=items, requested=args.n, topics=[
        {"id": t["id"], "name": t["name"], "weight": t["weight"], "status": st[t["id"]],
         "notes_files": source_notes(d, t["sources"])} for _, t in all_topics(exam) if t.get("studied")])
    path = d / ".session.json"
    save(path, session)
    out({"session_file": str(path), "questions": len(items), "requested": args.n,
         "missing": max(0, args.n - len(items)),
         "hint": "Read session_file with the Read tool. If questions are missing, write new ones "
                 "from the studied topics and send them as new_cards when recording."})


def cmd_record(args):
    data = read_stdin_json()
    if not isinstance(data, dict):
        raise LEError("payload must be an object")
    kind = data.get("kind", "study")
    if kind not in ("study", "mock"):
        raise LEError("kind must be study or mock")
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    exam, cards, log = load(d / "exam.json"), load(d / "cards.json"), read_log(d)
    day = today()
    key = day.isoformat()
    exam_date = parse_date(exam["exam_date"]) if exam.get("exam_date") else None
    topic_ids = {t["id"]: t for _, t in all_topics(exam)}
    index = {c["id"]: c for c in cards["cards"]}

    # ---- validate everything before touching anything
    def quality(v, where):
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 5:
            raise LEError("%s: quality must be an integer from 0 to 5" % where)
        return v

    answers = []
    for a in data.get("answers", []):
        if not isinstance(a, dict) or a.get("card") not in index:
            raise LEError("answers: unknown card %r" % (a.get("card") if isinstance(a, dict) else a))
        answers.append((index[a["card"]], quality(a.get("quality"), a["card"])))
    fresh = []
    for c in data.get("new_cards", []):
        card = validate_card(c, topic_ids)
        q = quality(c["quality"], "new card") if c.get("quality") is not None else None
        fresh.append((card, q))
    studied = data.get("studied_topics", [])
    for tid in studied:
        if tid not in topic_ids:
            raise LEError("studied_topics: unknown topic %r" % tid)

    # ---- apply in memory
    before = statuses(exam, cards)
    level_before = level_info(profile.get("xp", 0))["level"]
    xp, reviews, qualities, comeback = 0, 0, [], False
    for card, q in answers:
        was_hard = card.get("lapses", 0) >= 2
        sm2(card, q, day, exam_date)
        reviews += 1
        qualities.append(q)
        comeback = comeback or (was_hard and card["reps"] >= 3 and q >= 4)
    added = []
    for card, q in fresh:
        new_card(cards, card, day)
        added.append(card["id"])
        if q is not None:
            sm2(card, q, day, exam_date)
            qualities.append(q)
    xp += sum(XP_ANSWER[q] for q in qualities)
    newly_studied = []
    for tid in studied:
        if not topic_ids[tid].get("studied"):
            topic_ids[tid]["studied"] = key
            newly_studied.append(tid)
            xp += XP_TOPIC
    n_answers = len(qualities)
    n_correct = sum(1 for q in qualities if q >= 3)

    if not n_answers and not newly_studied:
        raise LEError("nothing to record: send answers, new_cards with quality, or studied_topics")

    # streak, with one-day freeze
    days = profile.setdefault("study_days", [])
    frozen = profile.setdefault("freeze_days", [])
    freeze_used = freeze_earned = False
    if key not in days:
        y1 = (day - dt.timedelta(days=1)).isoformat()
        y2 = (day - dt.timedelta(days=2)).isoformat()
        seen = set(days) | set(frozen)
        if y1 not in seen and y2 in seen and profile.get("freezes", 0) > 0:
            frozen.append(y1)
            profile["freezes"] -= 1
            freeze_used = True
        days.append(key)
        now_streak = streak(profile, day)
        if now_streak % 7 == 0 and profile.get("freezes", 0) < MAX_FREEZES:
            profile["freezes"] = profile.get("freezes", 0) + 1
            freeze_earned = True
    now_streak = streak(profile, day)

    mock = None
    if kind == "mock":
        m = data.get("mock") or {}
        total = m.get("total", n_answers)
        score = m.get("score", n_correct)
        if not isinstance(total, (int, float)) or not isinstance(score, (int, float)) or total <= 0:
            raise LEError("mock needs answers, or mock: {score, total} with total > 0")
        mock = {"score": score, "total": total, "pct": int(round(100.0 * score / total))}
        xp += XP_MOCK

    minutes = data.get("minutes")
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool):
        minutes = 0
        sess = load(d / ".session.json", default={})
        if sess.get("started"):
            try:
                gone = dt.datetime.now() - dt.datetime.fromisoformat(sess["started"])
                minutes = int(min(180, max(1, round(gone.total_seconds() / 60))))
            except ValueError:
                pass
    minutes = int(max(0, min(600, minutes)))

    entry = {"date": key, "kind": kind, "minutes": minutes, "answers": n_answers,
             "correct": n_correct, "reviews": reviews, "new_cards": len(added),
             "topics": newly_studied}
    if mock:
        entry["mock"] = mock
    plan = make_plan(exam, cards, log + [entry], day)
    goal_met = False
    goal_days = profile.setdefault("goal_days", [])
    if (plan["reviews_today_remaining"] == 0 and plan["new_today_remaining"] == 0
            and key not in goal_days):
        goal_days.append(key)
        goal_met = True
        xp += XP_GOAL
    entry["xp"] = xp

    after = statuses(exam, cards)
    profile["xp"] = profile.get("xp", 0) + xp
    profile["total_answers"] = profile.get("total_answers", 0) + n_answers
    topics_all = [t for _, t in all_topics(exam)]
    new_achievements = check_achievements(profile, {
        "date": key, "streak": now_streak, "total_answers": profile["total_answers"],
        "answers": n_answers, "correct": n_correct,
        "mastered": sum(1 for v in after.values() if v == "mastered"),
        "unit_complete": any(u["topics"] and all(t.get("studied") for t in u["topics"])
                             for u in exam["units"]),
        "topics_total": len(topics_all), "studied": sum(1 for t in topics_all if t.get("studied")),
        "mock": mock is not None, "mock_pct": mock["pct"] if mock else 0, "comeback": comeback,
    })
    lv = level_info(profile["xp"])

    # ---- write
    backup("record", d)
    save(d / "cards.json", cards)
    save(d / "exam.json", exam)
    save(profile_path(), profile)
    append_log(d, entry)
    try:
        (d / ".session.json").unlink()
    except OSError:
        pass

    tomorrow = (day + dt.timedelta(days=1)).isoformat()
    changes = [{"topic": topic_ids[t]["name"], "from": before[t], "to": after[t]}
               for t in after if before.get(t) != after[t]]
    out({
        "answers": n_answers, "correct": n_correct, "minutes": minutes,
        "xp_gained": xp, "level": lv, "level_up": lv["level"] > level_before,
        "streak": now_streak, "freeze_used": freeze_used, "freeze_earned": freeze_earned,
        "freezes": profile.get("freezes", 0), "goal_met_now": goal_met,
        "new_achievements": new_achievements, "topic_changes": changes, "cards_added": added,
        "mock": mock,
        "remaining_today": {"reviews": plan["reviews_today_remaining"],
                            "new_topics": plan["new_today_remaining"]},
        "tomorrow": {"reviews_due": sum(1 for c in cards["cards"] if c["due"] <= tomorrow),
                     "new_topics": min(plan["topics_pending"], plan["new_per_day"])},
        "days_left": plan["days_left"],
    })


# ----------------------------------------------------------------------------
# commands: dashboard
# ----------------------------------------------------------------------------

def dashboard_data(profile, d):
    exam, cards, log = load(d / "exam.json"), load(d / "cards.json"), read_log(d)
    index = load(d / "sources" / "index.json", default={"sources": []})
    day = today()
    key = day.isoformat()
    lang = lang_of(profile)
    pack = i18n().get(lang, {})
    by, st = cards_by_topic(cards), statuses(exam, cards)
    plan = make_plan(exam, cards, log, day)
    src_names = {s["id"]: s.get("title") or s["name"] for s in index["sources"]}

    units = []
    for u in exam["units"]:
        topics = []
        for t in u["topics"]:
            tc = by.get(t["id"], [])
            acc, n_hist = topic_accuracy(tc)
            topics.append({"id": t["id"], "name": t["name"], "weight": t["weight"],
                           "status": st[t["id"]], "studied": t.get("studied"),
                           "summary": t.get("summary", ""), "concepts": t.get("concepts", []),
                           "cards": len(tc), "answers": n_hist, "accuracy": acc,
                           "due": sum(1 for c in tc if c["due"] <= key),
                           "sources": [src_names.get(m.group(1), r) if m else r for r, m in
                                       ((r, re.match(r"\s*(s\d+)", r)) for r in t["sources"])]})
        units.append({"id": u["id"], "name": u["name"], "topics": topics})

    per_day = {}
    for e in log:
        row = per_day.setdefault(e["date"], {"answers": 0, "correct": 0, "minutes": 0, "xp": 0})
        for k in row:
            row[k] += e.get(k, 0)
    activity = []
    for i in range(118, -1, -1):
        cur = (day - dt.timedelta(days=i)).isoformat()
        activity.append({"date": cur, "answers": per_day.get(cur, {}).get("answers", 0),
                         "studied": cur in profile.get("study_days", []),
                         "frozen": cur in profile.get("freeze_days", [])})
    trend = [{"date": k, "accuracy": int(round(100.0 * v["correct"] / v["answers"])), "answers": v["answers"]}
             for k, v in sorted(per_day.items()) if v["answers"]][-30:]
    forecast = []
    for i in range(14):
        cur = (day + dt.timedelta(days=i)).isoformat()
        n = sum(1 for c in cards["cards"] if (c["due"] <= cur if i == 0 else c["due"] == cur))
        forecast.append({"date": cur, "due": n})

    have = {a["id"]: a["date"] for a in profile.get("achievements", [])}
    names = texts(lang, "achievements")
    n_streak, n_total = streak(profile, day), len(st)
    n_studied = sum(1 for v in st.values() if v != "new")
    reach = {"streak_3": (n_streak, 3), "streak_7": (n_streak, 7), "streak_14": (n_streak, 14),
             "streak_30": (n_streak, 30),
             "answers_50": (profile.get("total_answers", 0), 50),
             "answers_200": (profile.get("total_answers", 0), 200),
             "answers_500": (profile.get("total_answers", 0), 500),
             "halfway": (n_studied, max(1, int(math.ceil(n_total / 2.0)))),
             "roadmap_complete": (n_studied, max(1, n_total))}
    achievements = []
    for aid, icon in ACHIEVEMENTS:
        a = {"id": aid, "icon": icon, "name": names[aid][0], "description": names[aid][1],
             "date": have.get(aid)}
        if aid in reach and not a["date"]:
            a["progress"] = [min(*reach[aid]), reach[aid][1]]
        achievements.append(a)
    strong, weak = focus_topics(exam, cards, limit=3)
    total_answers = sum(e.get("answers", 0) for e in log)
    total_correct = sum(e.get("correct", 0) for e in log)
    edu = profile.get("education_level", "")
    return {
        "generated": dt.datetime.now().isoformat(timespec="minutes"), "today": key, "lang": lang,
        "locale": pack.get("locale", "en-GB"), "t": texts(lang, "dashboard"),
        "learner": {"name": profile.get("name", ""),
                    "education_level": texts(lang, "edu").get(edu, edu)},
        "focus": {"strong": strong, "weak": weak},
        "level": level_info(profile.get("xp", 0)), "streak": streak(profile, day),
        "studied_today": key in profile.get("study_days", []), "freezes": profile.get("freezes", 0),
        "goal_done_today": key in profile.get("goal_days", []),
        "exam": {"name": exam["name"], "exam_date": exam.get("exam_date"), "goal": exam.get("goal"),
                 "exam_type": exam.get("exam_type"), "daily_minutes": exam.get("daily_minutes")},
        "plan": plan, "units": units, "activity": activity, "trend": trend, "forecast": forecast,
        "achievements": achievements,
        "totals": {"answers": total_answers,
                   "accuracy": int(round(100.0 * total_correct / total_answers)) if total_answers else None,
                   "minutes": sum(e.get("minutes", 0) for e in log), "sessions": len(log),
                   "cards": len(cards["cards"]),
                   "topics": len(st), "studied": sum(1 for v in st.values() if v != "new"),
                   "mastered": sum(1 for v in st.values() if v == "mastered")},
        "sources": [{"name": s.get("title") or s["name"], "file": s["name"] if s.get("path") else "",
                     "type": s["type"],
                     "pages": s.get("pages"), "added": s.get("added"), "summary": s.get("summary", "")}
                    for s in index["sources"]],
        "sessions": [dict(e) for e in log[-12:]][::-1],
        "mocks": [{"date": e["date"], "pct": e["mock"]["pct"], "score": e["mock"]["score"],
                   "total": e["mock"]["total"]} for e in log if e.get("mock")],
    }


def cmd_dashboard(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    template = Path(__file__).resolve().parent / "dashboard_template.html"
    if not template.exists():
        raise LEError("dashboard template not found next to le.py")
    html = template.read_text(encoding="utf-8")
    marker = "/*__LE_DATA__*/null"
    if marker not in html:
        raise LEError("dashboard template is missing its data marker")
    blob = json.dumps(dashboard_data(profile, d), ensure_ascii=False).replace("</", "<\\/")
    target = d / "dashboard.html"
    tmp = target.with_name("dashboard.html.tmp")
    tmp.write_text(html.replace(marker, blob), encoding="utf-8")
    os.replace(str(tmp), str(target))
    opened = False
    if not args.no_open:
        try:
            import webbrowser
            opened = bool(webbrowser.open(target.as_uri()))
        except Exception:
            opened = False
    out({"dashboard": str(target), "opened": opened})


# ----------------------------------------------------------------------------
# commands: data management
# ----------------------------------------------------------------------------

def cmd_backup(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    dest = backup("manual", d)
    out({"backup": str(dest), "kept": BACKUPS_KEPT})


def cmd_export(args):
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    if args.out:
        target = Path(args.out).expanduser()
    else:
        target = home() / "exports" / ("%s-%s.zip" % (d.name, today().isoformat()))
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(target), "w", zipfile.ZIP_DEFLATED) as z:
        z.write(str(profile_path()), "profile.json")
        for p in sorted(d.rglob("*")):
            rel = p.relative_to(d)
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".tmp"):
                z.write(str(p), "%s/%s" % (d.name, rel.as_posix()))
    out({"export": str(target), "size_kb": int(math.ceil(target.stat().st_size / 1024))})


def cmd_restore(args):
    src = Path(args.zip).expanduser()
    if not src.is_file():
        raise LEError("file not found: %s" % src)
    try:
        z = zipfile.ZipFile(str(src))
    except zipfile.BadZipFile:
        raise LEError("not a valid export: %s" % src)
    with z:
        names = z.namelist()
        slugs = sorted({n.split("/")[0] for n in names if n.endswith("/exam.json") and n.count("/") == 1})
        if len(slugs) != 1:
            raise LEError("this zip is not a Learn Everything export")
        slug = slugs[0]
        if slug != slugify(slug):
            raise LEError("unsafe exam name in export")
        d = home() / slug
        if d.exists() and not args.force:
            raise LEError("exam %r already exists - rerun with --force to replace it "
                          "(the current one is moved to .trash)" % slug)
        base = home().resolve()
        members = []
        for n in names:
            if n.endswith("/"):
                continue
            target = (home() / n).resolve()
            if base not in target.parents:
                raise LEError("unsafe path in export: %s" % n)
            if n == "profile.json" or n.startswith(slug + "/"):
                members.append((n, target))
        if d.exists():
            trash = home() / ".trash"
            trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(trash / ("%s-%s" % (slug, dt.datetime.now().strftime("%Y%m%d-%H%M%S")))))
        restored_profile = False
        for n, target in members:
            if n == "profile.json":
                if profile_path().exists():
                    continue  # never overwrite the live streak / xp
                restored_profile = True
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
    profile = get_profile(required=False)
    if profile:
        profile["active_exam"] = slug
        save(profile_path(), profile)
    out({"restored": slug, "profile_restored": restored_profile, "folder": str(d)})


def cmd_delete(args):
    profile = get_profile()
    d = exam_dir(profile, args.slug)
    if args.confirm != args.slug:
        raise LEError("to delete, pass --confirm %s (ask the learner first)" % args.slug)
    trash = home() / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / ("%s-%s" % (args.slug, dt.datetime.now().strftime("%Y%m%d-%H%M%S")))
    shutil.move(str(d), str(dest))
    if profile.get("active_exam") == args.slug:
        rest = list_exams()
        profile["active_exam"] = rest[0] if rest else None
        save(profile_path(), profile)
    out({"deleted": args.slug, "moved_to": str(dest), "active_exam": profile.get("active_exam"),
         "note": "XP, streak and achievements are kept. Empty .trash by hand to free the space."})


# ----------------------------------------------------------------------------
# cli
# ----------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="le.py", description="Learn Everything data engine")
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--exam", help="exam slug (default: the active exam)")
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    add("init", cmd_init, "create/update profile and exam (JSON on stdin)")
    add("status", cmd_status, "what to do today").add_argument("--hook", action="store_true")
    add("list", cmd_list, "list exams")
    add("switch", cmd_switch, "set the active exam").add_argument("slug")

    src = add("source", None, "materials").add_subparsers(dest="sub")
    src.required = True
    sp = src.add_parser("check"); sp.add_argument("paths", nargs="+"); sp.set_defaults(fn=cmd_source_check)
    sp = src.add_parser("add"); sp.set_defaults(fn=cmd_source_add)
    sp = src.add_parser("list"); sp.set_defaults(fn=cmd_source_list)
    sp = src.add_parser("remove"); sp.add_argument("id"); sp.set_defaults(fn=cmd_source_remove)

    sp = add("find", cmd_find, "recent study files in Downloads, Desktop, Documents")
    sp.add_argument("dirs", nargs="*"); sp.add_argument("--days", type=int, default=120)
    sp.add_argument("--limit", type=int, default=25); sp.add_argument("--depth", type=int, default=3)
    sp = add("notes", cmd_notes, "the part of the notes a topic needs")
    sp.add_argument("topic", nargs="?"); sp.add_argument("--ref", action="append", default=[])

    sp = add("extract", cmd_extract, "text from .docx / .pptx")
    sp.add_argument("file"); sp.add_argument("--out"); sp.add_argument("--media")

    rm = add("roadmap", None, "roadmap").add_subparsers(dest="sub")
    rm.required = True
    sp = rm.add_parser("set"); sp.add_argument("--drop", action="store_true"); sp.set_defaults(fn=cmd_roadmap_set)
    sp = rm.add_parser("show"); sp.set_defaults(fn=cmd_roadmap_show)

    cd = add("cards", None, "question bank").add_subparsers(dest="sub")
    cd.required = True
    sp = cd.add_parser("add"); sp.set_defaults(fn=cmd_cards_add)
    sp = cd.add_parser("list"); sp.add_argument("--topic"); sp.set_defaults(fn=cmd_cards_list)

    add("next", cmd_next, "prepare today's session").add_argument("--extra", action="store_true")
    sp = add("mock", cmd_mock, "prepare a mock exam")
    sp.add_argument("-n", type=int, default=20); sp.add_argument("--seed", type=int)
    add("record", cmd_record, "save a session (JSON on stdin)")

    add("dashboard", cmd_dashboard, "build dashboard.html").add_argument("--no-open", action="store_true")
    add("backup", cmd_backup, "snapshot the state files")
    add("export", cmd_export, "zip an exam").add_argument("--out")
    sp = add("restore", cmd_restore, "restore an exported zip")
    sp.add_argument("zip"); sp.add_argument("--force", action="store_true")
    sp = add("delete", cmd_delete, "move an exam to .trash")
    sp.add_argument("slug"); sp.add_argument("--confirm", default="")
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except LEError as e:
        sys.stderr.write(json.dumps({"error": str(e)}, ensure_ascii=False) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
