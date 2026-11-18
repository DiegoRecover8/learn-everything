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
import base64
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import time
import unicodedata
import zipfile
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "0.2.2"
TAG = "[Learn Everything]"

SUPPORTED = {
    ".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".ipynb": "notebook", ".rtf": "rtf",
    ".txt": "text", ".md": "text", ".csv": "text",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".gif": "image",
}
EXTRACTABLE = ("pdf", "docx", "pptx", "notebook", "rtf")
READ_WITH = {
    "pdf": "le.py extract <file>, then le.py render <file> --pages N for pages with figures or scans",
    "image": "Read tool",
    "text": "Read tool",
    "docx": "le.py extract <file>",
    "pptx": "le.py extract <file>",
    "notebook": "le.py extract <file>",
    "rtf": "le.py extract <file>",
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


@contextlib.contextmanager
def write_lock():
    """One writer at a time: two sessions open in parallel (one per exam, say) share
    profile.json, and neither may lose the other's XP or streak."""
    path = home() / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "a+") as f:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except ImportError:  # Windows
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # retries for about 10 s
            except OSError:
                raise LEError("another session is saving right now - try again in a moment")
        yield  # closing the file releases the lock


_STDIN = []  # main() reads the payload before taking the write lock, so a slow pipe never holds it


def read_stdin_json():
    raw = _STDIN[0] if _STDIN else sys.stdin.read()
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
        if isinstance(ans, str) and ans.strip().lower() in ("true", "false"):
            ans = ans.strip().lower() == "true"
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
    others = [e for e in info.get("others", []) if e.get("name")]
    if others:
        def label(e):
            left = e.get("days_left")
            if left is None or left < 0:
                return e["name"]
            return "%s (%s)" % (e["name"], T["exam_today_short"] if left == 0 else
                                T["in_days"].format(n=left, days=plural(left)))
        lines.append(T["others"].format(list=" · ".join(label(e) for e in others[:3])))
    return lines


def exams_overview(profile):
    """Every exam, with what it needs today: what /learn shows to choose one."""
    rows = []
    for slug in list_exams():
        try:
            info = summary(profile, home() / slug)
        except Exception:  # one broken exam must not hide the others
            rows.append({"slug": slug, "name": None, "error": "unreadable"})
            continue
        p = info["plan"]
        rows.append({"slug": slug, "name": info["exam"]["name"], "days_left": p["days_left"],
                     "today": {"reviews": p["reviews_today_remaining"], "new_topics": p["new_today_remaining"]},
                     "topics": info["topics"], "next_action": info["next_action"],
                     "active": slug == profile.get("active_exam")})
    return rows


def cmd_status(args):
    if args.hook:
        try:
            profile = get_profile(required=False)
            exams = exams_overview(profile) if profile else []
            slugs = [e["slug"] for e in exams]
            lang = lang_of(profile) if profile else env_lang()
            if not slugs:
                print("%s %s" % (TAG, texts(lang, "hook")["setup"]))
                return
            active = profile.get("active_exam") if profile.get("active_exam") in slugs else slugs[0]
            info = summary(profile, home() / active)
            info["others"] = [e for e in exams if e["slug"] != active]
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
    exams = exams_overview(profile)
    slugs = [e["slug"] for e in exams]
    res = {"learner": {"name": profile.get("name"), "language": profile.get("language"),
                       "education_level": profile.get("education_level")},
           "streak": streak(profile), "studied_today": today().isoformat() in profile.get("study_days", []),
           "freezes": profile.get("freezes", 0), "level": level_info(profile.get("xp", 0)),
           "achievements": len(profile.get("achievements", [])), "exams": exams,
           "home": str(home())}
    if args.exam:
        res.update(summary(profile, exam_dir(profile, args.exam)))
    elif not slugs:
        res["next_action"] = "setup"
    elif profile.get("active_exam") not in slugs:
        res["next_action"] = "choose"  # the last exam used was deleted: ask which one
    else:
        res.update(summary(profile, exam_dir(profile)))
    if len(slugs) > 1 and not args.exam:
        res["choose_exam"] = True  # several exams: ask which one this session is for
    out(res)


def cmd_list(args):
    profile = get_profile(required=False)
    rows = []
    for slug in list_exams():
        d = home() / slug
        info = summary(profile, d)
        rows.append({"slug": slug, "name": info["exam"]["name"], "exam_date": info["exam"]["exam_date"],
                     "active": slug == profile.get("active_exam"), "topics": info["topics"],
                     "cards": info["cards"], "sources": info["sources"],
                     "dashboard": str(d / "dashboard.html") if (d / "dashboard.html").exists() else None})
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
    slug = args.exam or (profile or {}).get("active_exam")
    if slug and (home() / slug / "sources" / "index.json").is_file():
        index = load(home() / slug / "sources" / "index.json", default={"sources": []})
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


PAGE_MARK = re.compile(r"\[\s*(?:p|pp|pg|pág|pag|page|pages|slide|slides|diapositiva|diapo|folie|cell|celda)\.?\s*(\d+)",
                       re.I)
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


def _joined(x):
    return "".join(x) if isinstance(x, list) else str(x or "")


def extract_notebook(path):
    """Jupyter notebook -> Markdown with [cell N] marks, plus the images of its outputs (plots)."""
    try:
        nb = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LEError("could not read %s: %s" % (Path(path).name, e))
    meta = nb.get("metadata") or {}
    lang = (meta.get("kernelspec") or {}).get("language") or (meta.get("language_info") or {}).get("name") or ""
    cells = nb.get("cells")
    if cells is None:  # nbformat 3
        cells = [c for ws in nb.get("worksheets", []) for c in ws.get("cells", [])]
    chunks, images = [], []
    for i, c in enumerate(cells, 1):
        src = _joined(c.get("source", c.get("input", ""))).strip()
        part = []
        if c.get("cell_type") == "code":
            if src:
                part.append("```%s\n%s\n```" % (lang, src))
            for k, o in enumerate(c.get("outputs", []), 1):
                data = o.get("data") or {}
                if o.get("output_type") == "error":
                    text = "%s: %s" % (o.get("ename", "Error"), o.get("evalue", ""))
                else:
                    text = _joined(o.get("text", data.get("text/plain", "")))
                text = re.sub(r"\x1b\[[0-9;]*m", "", text).strip()
                if text:
                    part.append("Output:\n```\n%s\n```" % (text[:1500] + "\n…" if len(text) > 1500 else text))
                for mime, ext in (("image/png", "png"), ("image/jpeg", "jpg")):
                    if mime in data:
                        name = "cell-%d-%d.%s" % (i, k, ext)
                        try:
                            images.append((name, base64.b64decode(_joined(data[mime]))))
                            part.append("[image: %s]" % name)
                        except (ValueError, TypeError):
                            pass
        elif src:
            part.append(src)
        if part:
            chunks.append("[cell %d]\n%s" % (i, "\n".join(part)))
    return "\n\n".join(chunks), images


_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "headerl", "headerr",
             "footerl", "footerr", "listtable", "listoverridetable", "rsidtbl", "xmlnstbl", "themedata",
             "colorschememapping", "datastore", "latentstyles", "generator", "expandedcolortbl", "object"}
_RTF_CHARS = {"emdash": "—", "endash": "–", "bullet": "•", "lquote": "‘", "rquote": "’",
              "ldblquote": "“", "rdblquote": "”", "~": "\u00a0", "tab": "\t", "cell": "\t",
              "par": "\n", "line": "\n", "row": "\n", "sect": "\n\n", "page": "\n\n"}
_RTF_TOKEN = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|[\r\n]+|([^\\{}\r\n]+)")


def rtf_text(path):
    """Plain text of an RTF file: macOS textutil when there is one, else a small reader."""
    if sys.platform == "darwin" and shutil.which("textutil"):
        r = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)], capture_output=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.decode("utf-8", "replace").strip()
    raw = Path(path).read_bytes().decode("latin-1")
    out, stack, skip, uc, pending = [], [], False, 1, 0
    for m in _RTF_TOKEN.finditer(raw):
        word, arg, hexc, sym, brace, text = m.groups()
        if brace:
            if brace == "{":
                stack.append((skip, uc))
            elif stack:
                skip, uc = stack.pop()
            continue
        if pending and (hexc or text):  # the plain-text stand-in that follows a \uN character
            if hexc:
                pending -= 1
                continue
            cut = min(pending, len(text))
            text, pending = text[cut:], pending - cut
        if word:
            if word in _RTF_SKIP:
                skip = True
            elif word == "uc":
                uc = int(arg or 1)
            elif word == "u" and arg is not None:
                n = int(arg)
                if not skip:
                    out.append(chr(n + 65536 if n < 0 else n))
                pending = uc
            elif word in _RTF_CHARS and not skip:
                out.append(_RTF_CHARS[word])
        elif sym:
            if sym == "*":
                skip = True
            elif sym in "\\{}" and not skip:
                out.append(sym)
            elif sym in _RTF_CHARS and not skip:
                out.append(_RTF_CHARS[sym])
            elif sym in "\r\n" and not skip:
                out.append("\n")
        elif hexc and not skip:
            out.append(bytes([int(hexc, 16)]).decode("cp1252", "replace"))
        elif text and not skip:
            out.append(text)
    text = "".join(out)
    try:  # UTF-8 bytes written as \'hh escapes (some editors do it) read back as cp1252
        text = text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ----------------------------------------------------------------------------
# PDF: text and page images, with nothing to install
# ----------------------------------------------------------------------------
# Best first: poppler (pdftotext / pdftoppm) when installed, macOS's own PDFKit
# through osascript (every Mac has it), then a small built-in reader that handles
# common text PDFs and pulls the scanned pictures out of a page.

class PDFError(Exception):
    pass


_JXA_TEXT = r"""
ObjC.import("Quartz");
function run(argv) {
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (doc.isNil()) return JSON.stringify({error: "cannot open the file"});
  if (doc.isLocked) return JSON.stringify({error: "password protected"});
  var n = parseInt(doc.pageCount), pages = [];
  for (var i = 0; i < n; i++) {
    var s = doc.pageAtIndex(i).string;
    pages.push(s && !s.isNil() ? s.js : "");
  }
  return JSON.stringify({pages: pages});
}
"""

_JXA_RENDER = r"""
ObjC.import("Quartz"); ObjC.import("AppKit");
function run(argv) {
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (doc.isNil()) return JSON.stringify({error: "cannot open the file"});
  if (doc.isLocked) return JSON.stringify({error: "password protected"});
  var dir = argv[1], width = parseInt(argv[2]), n = parseInt(doc.pageCount), done = [];
  for (var k = 3; k < argv.length; k++) {
    var p = parseInt(argv[k]);
    if (p < 1 || p > n) continue;
    var page = doc.pageAtIndex(p - 1), box = page.boundsForBox($.kPDFDisplayBoxCropBox);
    var w = box.size.width, h = box.size.height;
    if (parseInt(page.rotation) % 180 != 0) { var t = w; w = h; h = t; }
    var s = width / w;
    var img = page.thumbnailOfSizeForBox($.NSMakeSize(Math.round(w * s), Math.round(h * s)), $.kPDFDisplayBoxCropBox);
    var rep = $.NSBitmapImageRep.imageRepWithData(img.TIFFRepresentation);
    var png = rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $());
    var path = dir + "/page-" + p + ".png";
    if (png.writeToFileAtomically(path, true)) done.push(path);
  }
  return JSON.stringify({images: done, pages: n});
}
"""


def _osascript(script, *argv):
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script] + [str(a) for a in argv],
                       capture_output=True, timeout=600)
    if r.returncode != 0:
        raise PDFError(r.stderr.decode("utf-8", "replace").strip()[:200] or "osascript failed")
    data = json.loads(r.stdout.decode("utf-8"))
    if data.get("error"):
        raise PDFError(data["error"])
    return data


def pdf_engines(task):
    """Ways to read PDFs on this computer, best first. LE_PDF_ENGINE forces one (tests)."""
    order = []
    if shutil.which("pdftotext" if task == "text" else "pdftoppm"):
        order.append("poppler")
    if sys.platform == "darwin" and shutil.which("osascript"):
        order.append("pdfkit")
    order.append("builtin")
    forced = os.environ.get("LE_PDF_ENGINE")
    return [forced] if forced else order


def pdf_text(path):
    """(engine, [text of each page])."""
    errors = []
    for engine in pdf_engines("text"):
        try:
            if engine == "poppler":
                r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=600)
                if r.returncode != 0:
                    raise PDFError(r.stderr.decode("utf-8", "replace").strip()[:200])
                pages = r.stdout.decode("utf-8", "replace").split("\f")
                if pages and not pages[-1].strip():
                    pages.pop()
            elif engine == "pdfkit":
                pages = _osascript(_JXA_TEXT, path)["pages"]
            else:
                pages = MiniPDF(path).texts()
            if pages:
                return engine, pages
            errors.append("%s: no pages" % engine)
        except (PDFError, OSError, ValueError, KeyError, RecursionError, subprocess.SubprocessError) as e:
            errors.append("%s: %s" % (engine, e))
    raise LEError("could not read the text of %s (%s)" % (Path(path).name, "; ".join(errors)))


def pdf_render(path, pages, width, dest):
    """(engine, [image paths]) for the given page numbers."""
    errors = []
    for engine in pdf_engines("render"):
        try:
            if engine == "poppler":
                images = []
                for n in pages:
                    prefix = dest / ("page-%d" % n)
                    r = subprocess.run(["pdftoppm", "-png", "-singlefile", "-f", str(n), "-l", str(n),
                                        "-scale-to-x", str(width), "-scale-to-y", "-1", str(path), str(prefix)],
                                       capture_output=True, timeout=300)
                    if r.returncode != 0:
                        raise PDFError(r.stderr.decode("utf-8", "replace").strip()[:200])
                    if Path(str(prefix) + ".png").exists():
                        images.append(str(prefix) + ".png")
            elif engine == "pdfkit":
                images = _osascript(_JXA_RENDER, path, dest, width, *pages)["images"]
            else:
                images = MiniPDF(path).page_images(pages, dest)
            if images:
                return engine, images
            errors.append("%s: no image" % engine)
        except (PDFError, OSError, ValueError, KeyError, RecursionError, subprocess.SubprocessError) as e:
            errors.append("%s: %s" % (engine, e))
    raise LEError("could not turn those pages into images (%s)" % "; ".join(errors))


# ---- the built-in reader --------------------------------------------------

class _Name(str):
    pass


class _Op(str):
    pass


class _Ref(int):
    pass


_PDF_SKIP = re.compile(rb"(?:\s|%[^\r\n]*)*")
_PDF_REGULAR = re.compile(rb"[^\s()<>\[\]{}/%]+")
_PDF_NUM = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)$")
_PDF_REF = re.compile(rb"\s+\d+\s+R(?![^\s()<>\[\]{}/%])")
_PDF_OBJ = re.compile(rb"(\d+)\s+\d+\s+obj\b")
_PDF_ESC = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}


class _Lexer(object):
    """PDF objects, and content-stream tokens, from bytes."""

    def __init__(self, data, pos=0, refs=True):
        self.d, self.p, self.refs = data, pos, refs

    def skip(self):
        self.p = _PDF_SKIP.match(self.d, self.p).end()

    def at_end(self):
        self.skip()
        return self.p >= len(self.d)

    def value(self):
        while True:
            self.skip()
            d, p = self.d, self.p
            if p >= len(d):
                raise PDFError("unexpected end")
            c = d[p:p + 1]
            if c == b"(":
                return self._string()
            if c == b"<" and d[p + 1:p + 2] == b"<":
                self.p += 2
                out = {}
                while not self.at_end() and self.d[self.p:self.p + 2] != b">>":
                    key = self.value()
                    val = self.value()
                    if isinstance(key, _Name):
                        out[str(key)] = val
                self.p += 2
                return out
            if c == b"<":
                end = d.find(b">", p)
                end = len(d) if end < 0 else end
                self.p = end + 1
                digits = re.sub(rb"[^0-9A-Fa-f]", b"", d[p + 1:end])
                return bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode())
            if c == b"[":
                self.p += 1
                out = []
                while not self.at_end() and self.d[self.p:self.p + 1] != b"]":
                    out.append(self.value())
                self.p += 1
                return out
            if c == b"/":
                m = _PDF_REGULAR.match(d, p + 1)
                raw = m.group() if m else b""
                self.p = p + 1 + len(raw)
                raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda x: bytes([int(x.group(1), 16)]), raw)
                return _Name(raw.decode("latin-1"))
            m = _PDF_REGULAR.match(d, p)
            if not m:
                self.p += 1  # a stray delimiter
                continue
            tok, self.p = m.group(), m.end()
            if _PDF_NUM.match(tok):
                num = float(tok) if b"." in tok else int(tok)
                if self.refs and isinstance(num, int):
                    r = _PDF_REF.match(d, self.p)
                    if r:
                        self.p = r.end()
                        return _Ref(num)
                return num
            if tok in (b"true", b"false"):
                return tok == b"true"
            if tok == b"null":
                return None
            return _Op(tok.decode("latin-1"))

    def _string(self):
        d, p, depth, out, n = self.d, self.p + 1, 1, bytearray(), len(self.d)
        while p < n:
            c = d[p]
            if c == 0x5C:  # backslash
                p += 1
                e = d[p] if p < n else 0
                if e in _PDF_ESC:
                    out += _PDF_ESC[e]
                    p += 1
                elif 0x30 <= e <= 0x37:
                    m = re.match(rb"[0-7]{1,3}", d[p:p + 3])
                    out.append(int(m.group(), 8) & 0xFF)
                    p += len(m.group())
                elif e in (0x0D, 0x0A):
                    p += 2 if d[p:p + 2] == b"\r\n" else 1
                else:
                    out.append(e)
                    p += 1
                continue
            if c == 0x28:
                depth += 1
            elif c == 0x29:
                depth -= 1
                if not depth:
                    p += 1
                    break
            out.append(c)
            p += 1
        self.p = p
        return bytes(out)


def _inflate(raw):
    """zlib, keeping whatever comes out before a broken byte."""
    z, out = zlib.decompressobj(), bytearray()
    for i in range(0, len(raw), 65536):
        try:
            out += z.decompress(raw[i:i + 65536])
        except zlib.error:
            break
    return bytes(out)


def _unpredict(data, parms):
    """Undo the PNG row filters some streams use (Predictor >= 10)."""
    if not isinstance(parms, dict) or not isinstance(parms.get("Predictor"), int) or parms["Predictor"] < 10:
        return data
    colors, bpc, cols = parms.get("Colors", 1), parms.get("BitsPerComponent", 8), parms.get("Columns", 1)
    bpp, row = max(1, colors * bpc // 8), (colors * bpc * cols + 7) // 8
    out, prev = bytearray(), bytearray(row)
    for i in range(0, len(data) - row, row + 1):
        kind, line = data[i], bytearray(data[i + 1:i + 1 + row])
        for j in range(row):
            left = line[j - bpp] if j >= bpp else 0
            up, corner = prev[j], prev[j - bpp] if j >= bpp else 0
            if kind == 1:
                line[j] = (line[j] + left) & 255
            elif kind == 2:
                line[j] = (line[j] + up) & 255
            elif kind == 3:
                line[j] = (line[j] + ((left + up) >> 1)) & 255
            elif kind == 4:
                pa, pb, pc = abs(up - corner), abs(left - corner), abs(left + up - 2 * corner)
                line[j] = (line[j] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        out += line
        prev = line
    return bytes(out)


def _pdf_decode(raw, filters, parms):
    filters = filters if isinstance(filters, list) else [filters] if filters else []
    parms = parms if isinstance(parms, list) else [parms] * len(filters)
    for f, prm in zip(filters, parms):
        f = str(f)
        if f in ("FlateDecode", "Fl"):
            raw = _unpredict(_inflate(raw), prm)
        elif f in ("ASCIIHexDecode", "AHx"):
            digits = re.sub(rb"[^0-9A-Fa-f]", b"", raw.split(b">")[0])
            raw = bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode())
        elif f in ("ASCII85Decode", "A85"):
            body = raw.strip()
            body = (body[2:] if body.startswith(b"<~") else body).split(b"~>")[0]
            raw = base64.a85decode(re.sub(rb"\s", b"", body))
        else:
            raise PDFError("unsupported filter " + f)
    return raw


_GLYPHS = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#", "dollar": "$", "percent": "%",
    "ampersand": "&", "quotesingle": "'", "quoteright": "’", "quoteleft": "‘", "parenleft": "(",
    "parenright": ")", "asterisk": "*", "plus": "+", "comma": ",", "hyphen": "-", "minus": "−",
    "period": ".", "slash": "/", "colon": ":", "semicolon": ";", "less": "<", "equal": "=", "greater": ">",
    "question": "?", "at": "@", "bracketleft": "[", "backslash": "\\", "bracketright": "]",
    "asciicircum": "^", "underscore": "_", "grave": "`", "braceleft": "{", "bar": "|", "braceright": "}",
    "asciitilde": "~", "quotedblleft": "“", "quotedblright": "”", "quotesinglbase": "‚",
    "quotedblbase": "„", "endash": "–", "emdash": "—", "bullet": "•", "ellipsis": "…", "fi": "fi",
    "fl": "fl", "ff": "ff", "ffi": "ffi", "ffl": "ffl", "exclamdown": "¡", "questiondown": "¿",
    "guillemotleft": "«", "guillemotright": "»", "degree": "°", "multiply": "×", "divide": "÷",
    "plusminus": "±", "section": "§", "paragraph": "¶", "periodcentered": "·", "dagger": "†",
    "daggerdbl": "‡", "copyright": "©", "registered": "®", "trademark": "™", "Euro": "€",
    "sterling": "£", "yen": "¥", "cent": "¢", "germandbls": "ß", "ordfeminine": "ª",
    "ordmasculine": "º", "dotlessi": "ı", "mu": "µ", "AE": "Æ", "ae": "æ", "OE": "Œ", "oe": "œ",
    "Oslash": "Ø", "oslash": "ø", "infinity": "∞", "lessequal": "≤", "greaterequal": "≥",
    "notequal": "≠", "approxequal": "≈", "summation": "∑", "integral": "∫", "radical": "√",
    "partialdiff": "∂", "arrowright": "→", "arrowleft": "←", "logicalnot": "¬", "nbspace": "\u00a0",
}
_LIGATURES = {0xFB00: "ff", 0xFB01: "fi", 0xFB02: "fl", 0xFB03: "ffi", 0xFB04: "ffl", 0xFB05: "st", 0xFB06: "st"}
_DIGIT_NAMES = "zero one two three four five six seven eight nine".split()
_ACCENTS = {"acute": "ACUTE", "grave": "GRAVE", "circumflex": "CIRCUMFLEX", "dieresis": "DIAERESIS",
            "tilde": "TILDE", "cedilla": "CEDILLA", "ring": "RING ABOVE", "caron": "CARON",
            "macron": "MACRON", "breve": "BREVE", "ogonek": "OGONEK", "dotaccent": "DOT ABOVE",
            "hungarumlaut": "DOUBLE ACUTE"}


def _glyph(name):
    """Unicode text for a PostScript glyph name (aacute, uni00E9, alpha...)."""
    name = str(name).split(".")[0]
    if "_" in name:
        return "".join(_glyph(part) for part in name.split("_"))
    if len(name) == 1:
        return name
    if name in _GLYPHS:
        return _GLYPHS[name]
    if name in _DIGIT_NAMES:
        return str(_DIGIT_NAMES.index(name))
    m = re.match(r"uni((?:[0-9A-Fa-f]{4})+)$", name)
    if m:
        return "".join(chr(int(m.group(1)[i:i + 4], 16)) for i in range(0, len(m.group(1)), 4))
    m = re.match(r"u([0-9A-Fa-f]{4,6})$", name)
    if m:
        return chr(int(m.group(1), 16))
    m = re.match(r"([A-Za-z])(%s)$" % "|".join(_ACCENTS), name)
    lookups = []
    if m:
        lookups.append("LATIN %s LETTER %s WITH %s" % ("CAPITAL" if m.group(1).isupper() else "SMALL",
                                                       m.group(1).upper(), _ACCENTS[m.group(2)]))
    lookups.append("GREEK %s LETTER %s" % ("CAPITAL" if name[0].isupper() else "SMALL", name.upper()))
    for full in lookups:
        try:
            return unicodedata.lookup(full)
        except KeyError:
            pass
    return ""


def _u16(hexs):
    try:
        return bytes.fromhex(hexs if len(hexs) % 2 == 0 else "0" + hexs).decode("utf-16-be", "ignore")
    except ValueError:
        return ""


def _cmap(data):
    """A ToUnicode CMap -> ({code: text}, bytes per code)."""
    t = data.decode("latin-1")
    m = re.search(r"begincodespacerange\s*<([0-9A-Fa-f]+)>", t)
    width = max(1, len(m.group(1)) // 2) if m else 1
    table = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", t, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            table[int(src, 16)] = _u16(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", t, re.S):
        for lo, hi, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
            lo, hi = int(lo, 16), int(hi, 16)
            if dst.startswith("["):
                for i, d in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", dst)):
                    table[lo + i] = _u16(d)
            else:
                base, digits = int(dst[1:-1] or "0", 16), len(dst) - 2
                for i in range(min(hi - lo, 4096) + 1):
                    table[lo + i] = _u16(format(base + i, "0%dX" % digits))
    return table, width


class _Font(object):
    def __init__(self, pdf, fd):
        fd = fd if isinstance(fd, dict) else {}
        self.width, self.map, self.widths, self.dw = 1, {}, {}, 500
        if fd.get("Subtype") == "Type0":
            self.width = 2  # no ToUnicode: the codes are glyph numbers and cannot be read
            desc = pdf.get(fd.get("DescendantFonts"))
            desc = pdf.get(desc[0]) if isinstance(desc, list) and desc else {}
            desc = desc if isinstance(desc, dict) else {}
            self.dw = _num(pdf.get(desc.get("DW"))) or 1000
            w, i = pdf.get(desc.get("W")), 0
            w = w if isinstance(w, list) else []
            while i + 1 < len(w):  # [c [w1 w2 ...]] or [c_first c_last w]
                first, nxt = pdf.get(w[i]), pdf.get(w[i + 1])
                if isinstance(nxt, list):
                    for k, val in enumerate(nxt):
                        self.widths[int(first) + k] = _num(pdf.get(val)) or 0
                    i += 2
                elif i + 2 < len(w):
                    for code in range(int(first), min(int(nxt), int(first) + 65536) + 1):
                        self.widths[code] = _num(pdf.get(w[i + 2])) or 0
                    i += 3
                else:
                    break
        else:
            widths, first = pdf.get(fd.get("Widths")), _num(pdf.get(fd.get("FirstChar"))) or 0
            for k, val in enumerate(widths if isinstance(widths, list) else []):
                self.widths[int(first) + k] = _num(pdf.get(val)) or 0
            enc, diffs = pdf.get(fd.get("Encoding")), None
            if isinstance(enc, dict):
                enc, diffs = pdf.get(enc.get("BaseEncoding")), pdf.get(enc.get("Differences"))
            codec = {"WinAnsiEncoding": "cp1252", "MacRomanEncoding": "mac_roman"}.get(str(enc or ""), "latin-1")
            self.map = {i: bytes([i]).decode(codec, "replace").replace("\ufffd", "") for i in range(256)}
            code = 0
            for item in diffs if isinstance(diffs, list) else []:
                if isinstance(item, int) and not isinstance(item, (bool, _Ref)):
                    code = item
                elif isinstance(item, _Name):
                    self.map[code] = _glyph(item)
                    code += 1
        self.unmapped = ""
        if isinstance(fd.get("ToUnicode"), _Ref):
            try:
                table, width = _cmap(pdf.stream_of(fd["ToUnicode"]))
                if table:
                    # With a ToUnicode map, a code left out of it is almost always the space
                    # (macOS and PowerPoint leave it out), never the base encoding's letter.
                    self.map, self.unmapped = table, " "
                if fd.get("Subtype") == "Type0":
                    self.width = width
            except PDFError:
                pass

    def codes(self, raw):
        if self.width == 2:
            return [(raw[i] << 8) | raw[i + 1] for i in range(0, len(raw) - 1, 2)]
        return list(raw)

    def decode(self, raw):
        return "".join(self.map.get(c, self.unmapped) for c in self.codes(raw))

    def advance(self, raw):
        """Width of the string in thousandths of the font size."""
        return sum(self.widths.get(c, self.dw) for c in self.codes(raw))


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


class MiniPDF(object):
    """Just enough of PDF to get the text of each page and its scanned pictures."""

    def __init__(self, path):
        self.data = Path(path).read_bytes()
        if b"%PDF" not in self.data[:1024]:
            raise PDFError("not a PDF file")
        self.objs, self._fonts = {}, {}
        self._scan()
        self.trailer = self._trailer()
        if "Encrypt" in self.trailer:
            raise PDFError("the PDF is encrypted")

    def _scan(self):
        d, pos, packed = self.data, 0, []
        while True:
            m = _PDF_OBJ.search(d, pos)
            if not m:
                break
            lx = _Lexer(d, m.end())
            try:
                val = lx.value()
            except (PDFError, ValueError, IndexError, RecursionError):
                pos = m.end()
                continue
            lx.skip()
            stream = None
            if isinstance(val, dict) and d.startswith(b"stream", lx.p):
                start = lx.p + 6
                start += 2 if d[start:start + 2] == b"\r\n" else 1 if d[start:start + 1] in (b"\n", b"\r") else 0
                length, end = val.get("Length"), -1
                if isinstance(length, int) and not isinstance(length, _Ref) and \
                        d[start + length:start + length + 24].lstrip().startswith(b"endstream"):
                    end = start + length
                if end < 0:
                    end = d.find(b"endstream", start)
                    end = len(d) if end < 0 else end
                stream, pos = (start, end), end
            else:
                pos = lx.p
            self.objs[int(m.group(1))] = (val, stream)
            if isinstance(val, dict) and val.get("Type") == "ObjStm" and stream:
                packed.append(int(m.group(1)))
        for num in packed:  # objects stored inside compressed object streams
            head = self.objs[num][0]
            try:
                data = self.stream_of(num)
                first = int(self.get(head.get("First")))
                nums = [int(x) for x in data[:first].split()]
            except (PDFError, ValueError, TypeError):
                continue
            for i in range(0, len(nums) - 1, 2):
                if nums[i] not in self.objs:
                    try:
                        self.objs[nums[i]] = (_Lexer(data, first + nums[i + 1]).value(), None)
                    except (PDFError, ValueError, IndexError, RecursionError):
                        pass

    def _trailer(self):
        t = {}
        for m in re.finditer(rb"trailer\s*<<", self.data):
            try:
                val = _Lexer(self.data, m.end() - 2).value()
            except (PDFError, ValueError, IndexError, RecursionError):
                continue
            if isinstance(val, dict):
                t.update(val)
        for val, _ in self.objs.values():
            if isinstance(val, dict) and val.get("Type") == "XRef":
                t.update({k: val[k] for k in ("Root", "Encrypt") if k in val})
        return t

    def get(self, v):
        for _ in range(32):
            if not isinstance(v, _Ref):
                return v
            v = self.objs.get(int(v), (None, None))[0]
        return None

    def stream_of(self, ref):
        entry = self.objs.get(int(ref))
        if not entry or not entry[1]:
            raise PDFError("missing stream %s" % ref)
        head, (a, b) = entry
        return _pdf_decode(self.data[a:b], self.get(head.get("Filter")), self.get(head.get("DecodeParms")))

    def pages(self):
        found, seen = [], set()

        def walk(node, res, depth):
            if isinstance(node, _Ref):
                if int(node) in seen:
                    return
                seen.add(int(node))
            node = self.get(node)
            if not isinstance(node, dict) or depth > 60:
                return
            res = node.get("Resources", res)
            kids = self.get(node.get("Kids"))
            if node.get("Type") != "Page" and isinstance(kids, list):
                for k in kids:
                    walk(k, res, depth + 1)
            elif node.get("Type") == "Page" or "Contents" in node:
                found.append((node, self.get(res)))

        root = self.get(self.trailer.get("Root"))
        if not isinstance(root, dict):
            root = next((v for v, _ in self.objs.values() if isinstance(v, dict) and v.get("Type") == "Catalog"), {})
        walk(root.get("Pages"), None, 0)
        if not found:
            found = [(v, self.get(v.get("Resources"))) for _, (v, _) in sorted(self.objs.items())
                     if isinstance(v, dict) and v.get("Type") == "Page"]
        return found

    def font(self, ref):
        key = int(ref) if isinstance(ref, _Ref) else id(ref)
        if key not in self._fonts:
            self._fonts[key] = _Font(self, self.get(ref))
        return self._fonts[key]

    def texts(self):
        out = []
        for page, res in self.pages():
            c = page.get("Contents")
            refs = self.get(c)
            refs = refs if isinstance(refs, list) else [c] if isinstance(c, _Ref) else []
            parts = []
            for r in refs:
                try:
                    parts.append(self.stream_of(r))
                except PDFError:
                    pass
            text = self._text(b"\n".join(parts), res).translate(_LIGATURES)
            text = re.sub(r"[ \t\u00a0]+", " ", text)
            out.append(re.sub(r"\n\s*\n\s*\n+", "\n\n", "\n".join(l.strip() for l in text.split("\n"))).strip())
        return out

    def _text(self, content, res, depth=0):
        res = res if isinstance(res, dict) else {}
        fonts, xobjs = self.get(res.get("Font")), self.get(res.get("XObject"))
        fonts, xobjs = fonts if isinstance(fonts, dict) else {}, xobjs if isinstance(xobjs, dict) else {}
        out, ops, font = [], [], None
        # Where the next glyph goes and where the last one ended, to tell a new line or a
        # word gap from glyphs merely placed one after another.
        st = {"fs": 1.0, "sx": 1.0, "sy": 1.0, "lx": 0.0, "ly": 0.0, "cx": 0.0, "tl": 0.0,
              "end_x": None, "end_y": None}
        ctm, saved = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], []  # graphics transform (q / Q / cm)

        def dev(x, y):
            return ctm[0] * x + ctm[2] * y + ctm[4], ctm[1] * x + ctm[3] * y + ctm[5]

        def brk(sep):
            if out and not out[-1].endswith(("\n", sep)):
                out.append(sep)

        def draw(raw):
            size = abs(st["fs"] * st["sy"] * (math.hypot(ctm[2], ctm[3]) or 1.0)) or 1.0
            x, y = dev(st["cx"], st["ly"])
            if st["end_y"] is not None:
                if abs(y - st["end_y"]) > 0.5 * size:
                    brk("\n")
                elif x - st["end_x"] > 0.2 * size or st["end_x"] - x > 2 * size:
                    brk(" ")
            if font:
                out.append(font.decode(raw))
                st["cx"] += font.advance(raw) / 1000.0 * st["fs"] * st["sx"]
            st["end_x"], st["end_y"] = dev(st["cx"], st["ly"])

        def move(tx, ty):
            st["lx"] += tx * st["sx"]
            st["ly"] += ty * st["sy"]
            st["cx"] = st["lx"]

        lx = _Lexer(content, refs=False)
        while not lx.at_end():
            try:
                tok = lx.value()
            except (PDFError, ValueError, IndexError, RecursionError):
                break
            if not isinstance(tok, _Op):
                ops.append(tok)
                continue
            n = [_num(x) for x in ops]
            try:
                if tok == "BI":  # inline image: skip its binary data
                    m = re.compile(rb"\sID\s").search(content, lx.p - 3)
                    e = re.compile(rb"\sEI(?=\s|$)").search(content, m.end()) if m else None
                    lx.p = e.end() if e else len(content)
                elif tok == "BT":
                    st.update(sx=1.0, sy=1.0, lx=0.0, ly=0.0, cx=0.0)
                elif tok == "q":
                    saved.append(list(ctm))
                elif tok == "Q":
                    ctm = saved.pop() if saved else ctm
                elif tok == "cm" and len(n) >= 6 and None not in n[-6:]:
                    a, b, c, d, e, f = n[-6:]
                    A, B, C, D, E, F = ctm
                    ctm[:] = [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D,
                              e * A + f * C + E, e * B + f * D + F]
                elif tok == "Tf" and len(ops) >= 2 and isinstance(ops[-2], _Name):
                    ref = fonts.get(str(ops[-2]))
                    font = self.font(ref) if ref is not None else None
                    st["fs"] = n[-1] if n[-1] is not None else st["fs"]
                elif tok == "TL" and n and n[-1] is not None:
                    st["tl"] = n[-1]
                elif tok in ("Td", "TD") and len(n) >= 2 and None not in n[-2:]:
                    move(n[-2], n[-1])
                    if tok == "TD":
                        st["tl"] = -n[-1]
                elif tok == "Tm" and len(n) >= 6 and None not in n[-6:]:
                    a, b, c, d, e, f = n[-6:]
                    st.update(sx=math.hypot(a, b) or 1.0, sy=(d if abs(d) >= abs(c) else c) or 1.0,
                              lx=e, ly=f, cx=e)
                elif tok in ("T*", "'", '"'):
                    move(0, -st["tl"])
                    if not st["tl"]:
                        brk("\n")
                    if tok != "T*" and ops and isinstance(ops[-1], bytes):
                        draw(ops[-1])
                elif tok == "Tj" and ops and isinstance(ops[-1], bytes):
                    draw(ops[-1])
                elif tok == "TJ" and ops and isinstance(ops[-1], list):
                    for item in ops[-1]:
                        if isinstance(item, bytes):
                            draw(item)
                        elif _num(item) is not None:
                            st["cx"] -= _num(item) / 1000.0 * st["fs"] * st["sx"]
                elif tok == "Do" and ops and isinstance(ops[-1], _Name) and depth < 4:
                    ref = xobjs.get(str(ops[-1]))
                    xo = self.get(ref)
                    if isinstance(ref, _Ref) and isinstance(xo, dict) and xo.get("Subtype") == "Form":
                        inner = self._text(self.stream_of(ref), self.get(xo.get("Resources")) or res, depth + 1)
                        if inner.strip():
                            brk("\n")
                            out.append(inner)
                            brk("\n")
            except (PDFError, TypeError, ValueError, KeyError):
                pass
            ops = []
        return "".join(out)

    def page_images(self, numbers, dest):
        """The pictures drawn on each page (a scanned page is one big picture)."""
        pages, saved = self.pages(), []
        for n in numbers:
            if not 1 <= n <= len(pages):
                continue
            res = pages[n - 1][1] if isinstance(pages[n - 1][1], dict) else {}
            xobjs = self.get(res.get("XObject"))
            k = 0
            for _, ref in sorted(xobjs.items()) if isinstance(xobjs, dict) else []:
                img = self._image(ref)
                if img:
                    k += 1
                    path = dest / ("page-%d-%d.%s" % (n, k, img[0]))
                    path.write_bytes(img[1])
                    saved.append(str(path))
        return saved

    def _image(self, ref):
        entry = self.objs.get(int(ref)) if isinstance(ref, _Ref) else None
        if not entry or not entry[1] or not isinstance(entry[0], dict) or entry[0].get("Subtype") != "Image":
            return None
        head, (a, b) = entry
        w, h = self.get(head.get("Width")), self.get(head.get("Height"))
        if not isinstance(w, int) or not isinstance(h, int) or w * h < 120 * 120:
            return None  # icons and bullets
        f = self.get(head.get("Filter"))
        names = [str(x) for x in (f if isinstance(f, list) else [f] if f else [])]
        raw = self.data[a:b]
        if names and names[-1] in ("DCTDecode", "DCT"):
            return "jpg", _pdf_decode(raw, names[:-1], None)
        if names not in (["FlateDecode"], ["Fl"]) or self.get(head.get("BitsPerComponent")) != 8:
            return None
        cs = self.get(head.get("ColorSpace"))
        if isinstance(cs, list) and cs and cs[0] == "ICCBased":
            icc = self.objs.get(int(cs[1]), ({}, None))[0] if isinstance(cs[1], _Ref) else {}
            ch = icc.get("N") if isinstance(icc, dict) else None
        else:
            ch = {"DeviceGray": 1, "CalGray": 1, "DeviceRGB": 3, "CalRGB": 3}.get(str(cs))
        if ch not in (1, 3):
            return None
        data, row = _inflate(raw), w * ch
        parms = self.get(head.get("DecodeParms"))
        if isinstance(parms, dict) and isinstance(parms.get("Predictor"), int) and parms["Predictor"] >= 10:
            rows = data[:(row + 1) * h]  # PNG row filters, exactly what a PNG file stores
        else:
            rows = b"".join(b"\x00" + data[i * row:(i + 1) * row] for i in range(h))
        if len(rows) != (row + 1) * h:
            return None

        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0 if ch == 1 else 2, 0, 0, 0))
        return "png", png + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


# ---- extract and render ---------------------------------------------------

TEXT_INLINE_MAX = 24000  # longer output is saved to a file: a command's output is cut off around 30k


def cache_dir(kind):
    """Scratch space inside the data folder, emptied of anything older than two weeks."""
    d = home() / ".cache" / kind
    d.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 14 * 86400
    for p in d.iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(str(p)) if p.is_dir() else p.unlink()
        except OSError:
            pass
    return d


def parse_pages(spec, total=None):
    """"1-5,9" -> [1, 2, 3, 4, 5, 9], within 1..total when it is known."""
    pages = []
    for part in str(spec or "").replace(" ", "").split(","):
        if not part:
            continue
        m = re.match(r"(\d+)(?:-(\d+))?$", part)
        if not m:
            raise LEError("pages must look like 3 or 1-5,9")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        for n in range(min(a, b), max(a, b) + 1):
            if n >= 1 and (total is None or n <= total) and n not in pages:
                pages.append(n)
    return pages


def page_ranges(numbers):
    """[4, 9, 10, 11] -> "4,9-11"."""
    out, run = [], []
    for n in sorted(set(numbers)):
        if run and n == run[-1] + 1:
            run.append(n)
        else:
            if run:
                out.append(str(run[0]) if len(run) == 1 else "%d-%d" % (run[0], run[-1]))
            run = [n]
    if run:
        out.append(str(run[0]) if len(run) == 1 else "%d-%d" % (run[0], run[-1]))
    return ",".join(out)


def emit_text(text, src, args, meta):
    """Print the text, or save it to a file when it is too long for one command output."""
    if args.out:
        target = Path(args.out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        meta.update(written=str(target), chars=len(text), lines=text.count("\n") + 1)
        out(meta)
    elif len(text) <= TEXT_INLINE_MAX:
        sys.stdout.write(text + "\n")
        if meta:
            sys.stdout.write("\n--- le.py: " + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        target = cache_dir("text") / ("%s-%s.md" % (slugify(src.stem)[:40], sha256(src)[:10]))
        target.write_text(text, encoding="utf-8")
        meta.update(text_file=str(target), chars=len(text), lines=text.count("\n") + 1,
                    hint="Too long for one output: read text_file with the Read tool in parts (offset and limit).")
        out(meta)


def cmd_extract(args):
    p = Path(args.file).expanduser()
    kind = SUPPORTED.get(p.suffix.lower())
    if kind not in EXTRACTABLE:
        raise LEError("extract only handles PDF, DOCX, PPTX, IPYNB and RTF (read other files with the Read tool)")
    if not p.is_file():
        raise LEError("file not found: %s" % p)
    meta, images = {}, []
    if kind == "pdf":
        engine, pages = pdf_text(p)
        chosen = parse_pages(args.pages, len(pages)) if args.pages else list(range(1, len(pages) + 1))
        parts, little = [], []
        for n in chosen:
            body = pages[n - 1].strip()
            if len(body) < 150:
                little.append(n)
            parts.append("[p. %d]\n%s" % (n, body))
        text = "\n\n".join(parts)
        meta = {"pages": len(pages), "engine": engine}
        if args.pages:
            meta["shown"] = page_ranges(chosen)
        if little:
            meta["little_text"] = page_ranges(little)
            meta["render"] = 'le.py render "%s" --pages %s' % (p, page_ranges(little[:20]))
    elif kind == "notebook":
        text, images = extract_notebook(p)
    elif kind == "rtf":
        text = rtf_text(p)
    else:
        try:
            with zipfile.ZipFile(str(p)) as z:
                text = extract_docx(z) if kind == "docx" else extract_pptx(z)
                images = [(Path(n).name, z.read(n)) for n in z.namelist()
                          if re.match(r"(word|ppt)/media/[^/]+$", n)]
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
            raise LEError("could not read %s: %s" % (p.name, e))
    if images:
        meta["embedded_images"] = len(images)
        if args.media:
            dest = Path(args.media).expanduser()
            dest.mkdir(parents=True, exist_ok=True)
            meta["images_saved"] = []
            for name, data in images:
                (dest / name).write_bytes(data)
                meta["images_saved"].append(str(dest / name))
        else:
            meta["images_hint"] = "rerun with --media <folder inside the data home> to save them, then Read them"
    emit_text(text, p, args, meta)


def cmd_render(args):
    """Turn PDF pages into images, to read figures, formulas and scanned pages."""
    p = Path(args.file).expanduser()
    if not p.is_file():
        raise LEError("file not found: %s" % p)
    if SUPPORTED.get(p.suffix.lower()) != "pdf":
        raise LEError("render only handles PDF files (images are read directly)")
    pages = parse_pages(args.pages)
    if not pages:
        raise LEError("say which pages, for example --pages 3,7-9")
    if len(pages) > 20:
        raise LEError("at most 20 pages per call")
    dest = cache_dir("pages") / sha256(p)[:12]
    dest.mkdir(exist_ok=True)
    engine, images = pdf_render(p, pages, max(400, min(2400, args.width)), dest)
    res = {"images": images, "engine": engine, "hint": "Open each image with the Read tool."}
    if engine == "builtin":
        res["note"] = "these are the pictures embedded in the pages (scans, photos), not full page drawings"
    out(res)


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


def cmd_labels_show(args):
    """Every name and short text the dashboard shows, to translate them after a language change."""
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    exam = load(d / "exam.json")
    index = load(d / "sources" / "index.json", default={"sources": []})
    out({"exam": exam["name"],
         "units": [{"id": u["id"], "name": u["name"]} for u in exam["units"]],
         "topics": [{"id": t["id"], "name": t["name"], "summary": t.get("summary", ""),
                     "concepts": t.get("concepts", [])} for _, t in all_topics(exam)],
         "sources": [{"id": s["id"], "title": s.get("title") or s["name"], "summary": s.get("summary", "")}
                     for s in index["sources"]]})


def cmd_labels_set(args):
    """Rename units, topics and materials by id. Progress, questions and notes stay as they are."""
    data = read_stdin_json()
    if not isinstance(data, dict):
        raise LEError('payload must be {"units": [...], "topics": [...], "sources": [...]}')
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    exam = load(d / "exam.json")
    index = load(d / "sources" / "index.json", default={"version": 1, "next_id": 1, "sources": []})
    units = {u["id"]: u for u in exam["units"]}
    topics = {t["id"]: t for _, t in all_topics(exam)}
    sources = {s["id"]: s for s in index["sources"]}
    plan = []
    for group, table, fields in (("units", units, ("name",)), ("topics", topics, ("name", "summary", "concepts")),
                                 ("sources", sources, ("title", "summary"))):
        for item in data.get(group, []):
            if not isinstance(item, dict) or item.get("id") not in table:
                raise LEError("%s: unknown id %r" % (group, item.get("id") if isinstance(item, dict) else item))
            for f in fields:
                if f not in item:
                    continue
                val = item[f]
                if f == "concepts":
                    if not isinstance(val, list):
                        raise LEError("topic concepts must be a list of short strings")
                    val = [str(c).strip()[:60] for c in val if str(c).strip()][:8]
                elif not isinstance(val, str) or (f in ("name", "title") and not val.strip()):
                    raise LEError("%s %s: %s must be a non-empty string" % (group, item["id"], f))
                plan.append((table[item["id"]], f, val.strip() if isinstance(val, str) else val))
    if not plan:
        raise LEError("nothing to change")
    backup("labels", d)
    for target, f, val in plan:
        target[f] = val
    save(d / "exam.json", exam)
    save(d / "sources" / "index.json", index)
    out({"changed": len(plan)})


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


def source_ids(topic):
    """{"s1", "s3"} from a topic's sources ("s1 p.3-8", "s3")."""
    return {m.group(1) for m in (re.match(r"\s*(s\d+)", s) for s in topic.get("sources", [])) if m}


def resolve_targets(exam, index, words):
    """Topic, unit or source ids (t3, u2, s1), or words from their names -> topic ids."""
    pairs = all_topics(exam)
    ids_like = [w for w in words if re.fullmatch(r"[tus]\d+", w.strip().lower())]
    phrases = ids_like + ([" ".join(w for w in words if w not in ids_like)]
                          if len(ids_like) < len(words) else [])
    chosen, unknown = [], []
    for raw in phrases:
        key = raw.strip().lower()
        if not key:
            continue
        if re.fullmatch(r"t\d+", key):
            ids = [t["id"] for _, t in pairs if t["id"] == key]
        elif re.fullmatch(r"u\d+", key):
            ids = [t["id"] for u, t in pairs if u["id"] == key]
        elif re.fullmatch(r"s\d+", key):
            ids = [t["id"] for _, t in pairs if key in source_ids(t)]
        else:
            n = norm(key)
            srcs = {s["id"] for s in index["sources"] if n in norm(s.get("title") or "") or n in norm(s["name"])}
            ids = ([t["id"] for _, t in pairs if n in norm(t["name"])]
                   or [t["id"] for u, t in pairs if n in norm(u["name"])]
                   or [t["id"] for _, t in pairs if srcs & source_ids(t)])
        if ids:
            chosen += [i for i in ids if i not in chosen]
        else:
            unknown.append(raw.strip())
    return chosen, unknown


def cmd_review(args):
    """A review of chosen topics, units or materials, whether or not their questions are due."""
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    exam, cards = load(d / "exam.json"), load(d / "cards.json")
    index = load(d / "sources" / "index.json", default={"sources": []})
    by, st = cards_by_topic(cards), statuses(exam, cards)
    if args.targets:
        ids, unknown = resolve_targets(exam, index, args.targets)
        if not ids:
            listing = ", ".join("%s %s" % (t["id"], t["name"]) for _, t in all_topics(exam))[:900]
            raise LEError("nothing matches %s. Topics: %s" % (", ".join(repr(u) for u in unknown), listing))
    else:  # no target: the weakest studied topics, else the ones with most mistakes
        unknown = []
        weak = focus_topics(exam, cards, limit=3)[1]
        ids = [r["id"] for r in weak]
        if not ids:
            ranked = sorted((t for _, t in all_topics(exam) if t.get("studied")),
                            key=lambda t: -sum(c.get("lapses", 0) for c in by.get(t["id"], [])))
            ids = [t["id"] for t in ranked[:2]]
        if not ids:
            raise LEError("nothing studied yet: run a study session first (/learn)")
    key = today().isoformat()

    def priority(c):  # overdue first, then the most often failed, then the shakiest last answer
        return (c.get("due", key) > key, -c.get("lapses", 0), last_q(c) if last_q(c) is not None else -1, c["id"])

    pool = sorted((c for c in cards["cards"] if c["topic"] in ids), key=priority)
    names = {t["id"]: (u["name"], t) for u, t in all_topics(exam)}
    questions = []
    for c in pool[:args.n]:
        item = {k: c[k] for k in ("id", "topic", "type", "prompt", "options", "answer", "accepted",
                                  "rubric", "explanation", "source") if k in c}
        item["topic_name"] = names[c["topic"]][1]["name"]
        questions.append(item)
    topics = []
    for tid in ids:
        unit, t = names[tid]
        acc, n_hist = topic_accuracy(by.get(tid, []))
        topics.append({"id": tid, "name": t["name"], "unit": unit, "status": st[tid], "studied": t.get("studied"),
                       "accuracy": acc, "cards": len(by.get(tid, [])), "summary": t.get("summary", ""),
                       "concepts": t.get("concepts", []), "sources": t["sources"],
                       "notes_cmd": "le.py notes %s" % tid})
    session = session_header(profile, exam, "review")
    session.update(questions=questions, topics=topics)
    path = d / ".session.json"
    save(path, session)
    out({"session_file": str(path), "exam": exam["slug"], "topics": [t["name"] for t in topics],
         "questions": len(questions), "not_studied_yet": [t["name"] for t in topics if not t["studied"]],
         "unknown": unknown,
         "hint": "Read session_file with the Read tool. It holds the answers: never show it."})


def cmd_record(args):
    data = read_stdin_json()
    if not isinstance(data, dict):
        raise LEError("payload must be an object")
    kind = data.get("kind", "study")
    if kind not in ("study", "mock", "review"):
        raise LEError("kind must be study, review or mock")
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

def dashboard_data(profile, d, exams=None):
    exam, cards, log = load(d / "exam.json"), load(d / "cards.json"), read_log(d)
    index = load(d / "sources" / "index.json", default={"sources": []})
    day = today()
    key = day.isoformat()
    lang = lang_of(profile)
    pack = i18n().get(lang, {})
    by, st = cards_by_topic(cards), statuses(exam, cards)
    plan = make_plan(exam, cards, log, day)
    src_names = {s["id"]: s.get("title") or s["name"] for s in index["sources"]}
    slug = d.name

    def review_cmd(target):  # pasted into Claude Code from the dashboard's "Review" buttons
        return "/learn:review %s %s" % (slug, target)

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
                           "due": sum(1 for c in tc if c["due"] <= key), "review": review_cmd(t["id"]),
                           "sources": [src_names.get(m.group(1), r) if m else r for r, m in
                                       ((r, re.match(r"\s*(s\d+)", r)) for r in t["sources"])]})
        units.append({"id": u["id"], "name": u["name"], "topics": topics})
    covered = set()
    for _, t in all_topics(exam):
        covered |= source_ids(t)

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
        "exam": {"slug": slug, "name": exam["name"], "exam_date": exam.get("exam_date"), "goal": exam.get("goal"),
                 "exam_type": exam.get("exam_type"), "daily_minutes": exam.get("daily_minutes")},
        "exams": [{"slug": e["slug"], "name": e["name"], "days_left": e.get("days_left"),
                   "href": "../%s/dashboard.html" % e["slug"], "current": e["slug"] == slug}
                  for e in (exams or []) if e.get("name")],
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
                     "pages": s.get("pages"), "added": s.get("added"), "summary": s.get("summary", ""),
                     "review": review_cmd(s["id"]) if s["id"] in covered else None}
                    for s in index["sources"]],
        "sessions": [dict(e) for e in log[-12:]][::-1],
        "mocks": [{"date": e["date"], "pct": e["mock"]["pct"], "score": e["mock"]["score"],
                   "total": e["mock"]["total"]} for e in log if e.get("mock")],
    }


def write_dashboard(profile, d, html, exams):
    blob = json.dumps(dashboard_data(profile, d, exams), ensure_ascii=False).replace("</", "<\\/")
    target = d / "dashboard.html"
    tmp = target.with_name("dashboard.html.tmp")
    tmp.write_text(html.replace("/*__LE_DATA__*/null", blob), encoding="utf-8")
    os.replace(str(tmp), str(target))
    return target


def cmd_dashboard(args):
    """Builds the dashboard of every exam (so the exam switcher never links to a stale page)
    and opens the one asked for, or the active one."""
    profile = get_profile()
    d = exam_dir(profile, args.exam)
    template = Path(__file__).resolve().parent / "dashboard_template.html"
    if not template.exists():
        raise LEError("dashboard template not found next to le.py")
    html = template.read_text(encoding="utf-8")
    if "/*__LE_DATA__*/null" not in html:
        raise LEError("dashboard template is missing its data marker")
    exams = exams_overview(profile)
    target = write_dashboard(profile, d, html, exams)
    others = []
    for e in exams:
        if e["slug"] != d.name and e.get("name"):
            try:
                others.append(str(write_dashboard(profile, home() / e["slug"], html, exams)))
            except Exception:  # a broken exam must not stop the one asked for
                pass
    opened = False
    if not args.no_open:
        try:
            import webbrowser
            opened = bool(webbrowser.open(target.as_uri()))
        except Exception:
            opened = False
    out({"dashboard": str(target), "opened": opened, "other_dashboards": others})


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

    def add(name, fn, help_, writes=False):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn, writes=writes)
        sp.add_argument("--exam", default=argparse.SUPPRESS, help=argparse.SUPPRESS)  # also after the command
        return sp

    def subcmd(group, name, fn, writes=False):
        sp = group.add_parser(name)
        sp.set_defaults(fn=fn, writes=writes)
        sp.add_argument("--exam", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return sp

    add("init", cmd_init, "create/update profile and exam (JSON on stdin)", True)
    add("status", cmd_status, "what to do today").add_argument("--hook", action="store_true")
    add("list", cmd_list, "list exams")
    add("switch", cmd_switch, "set the active exam", True).add_argument("slug")

    src = add("source", None, "materials").add_subparsers(dest="sub")
    src.required = True
    subcmd(src, "check", cmd_source_check).add_argument("paths", nargs="+")
    subcmd(src, "add", cmd_source_add, True)
    subcmd(src, "list", cmd_source_list)
    subcmd(src, "remove", cmd_source_remove, True).add_argument("id")

    sp = add("find", cmd_find, "recent study files in Downloads, Desktop, Documents")
    sp.add_argument("dirs", nargs="*"); sp.add_argument("--days", type=int, default=120)
    sp.add_argument("--limit", type=int, default=25); sp.add_argument("--depth", type=int, default=3)
    sp = add("notes", cmd_notes, "the part of the notes a topic needs")
    sp.add_argument("topic", nargs="?"); sp.add_argument("--ref", action="append", default=[])

    sp = add("extract", cmd_extract, "text of a PDF, DOCX, PPTX, IPYNB or RTF file")
    sp.add_argument("file"); sp.add_argument("--out"); sp.add_argument("--media")
    sp.add_argument("--pages", help="PDF pages, e.g. 1-20 or 3,7-9")
    sp = add("render", cmd_render, "PDF pages as images, for figures and scans")
    sp.add_argument("file"); sp.add_argument("--pages", required=True)
    sp.add_argument("--width", type=int, default=1400)

    rm = add("roadmap", None, "roadmap").add_subparsers(dest="sub")
    rm.required = True
    subcmd(rm, "set", cmd_roadmap_set, True).add_argument("--drop", action="store_true")
    subcmd(rm, "show", cmd_roadmap_show)
    lb = add("labels", None, "names of units, topics and materials").add_subparsers(dest="sub")
    lb.required = True
    subcmd(lb, "show", cmd_labels_show)
    subcmd(lb, "set", cmd_labels_set, True)

    cd = add("cards", None, "question bank").add_subparsers(dest="sub")
    cd.required = True
    subcmd(cd, "add", cmd_cards_add, True)
    subcmd(cd, "list", cmd_cards_list).add_argument("--topic")

    add("next", cmd_next, "prepare today's session", True).add_argument("--extra", action="store_true")
    sp = add("review", cmd_review, "prepare a review of chosen topics, units or materials", True)
    sp.add_argument("targets", nargs="*", help="t3, u2, s1 or words from a name; none = weakest topics")
    sp.add_argument("-n", type=int, default=10)
    sp = add("mock", cmd_mock, "prepare a mock exam", True)
    sp.add_argument("-n", type=int, default=20); sp.add_argument("--seed", type=int)
    add("record", cmd_record, "save a session (JSON on stdin)", True)

    add("dashboard", cmd_dashboard, "build dashboard.html", True).add_argument("--no-open", action="store_true")
    add("backup", cmd_backup, "snapshot the state files", True)
    add("export", cmd_export, "zip an exam").add_argument("--out")
    sp = add("restore", cmd_restore, "restore an exported zip", True)
    sp.add_argument("zip"); sp.add_argument("--force", action="store_true")
    sp = add("delete", cmd_delete, "move an exam to .trash", True)
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
        if args.fn in (cmd_init, cmd_source_add, cmd_roadmap_set, cmd_labels_set, cmd_cards_add, cmd_record):
            _STDIN[:] = [sys.stdin.read()]
        with write_lock() if getattr(args, "writes", False) else contextlib.nullcontext():
            args.fn(args)
    except LEError as e:
        sys.stderr.write(json.dumps({"error": str(e)}, ensure_ascii=False) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
