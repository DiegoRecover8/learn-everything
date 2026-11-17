"""Tests for scripts/le.py. Standard library only.

    python3 -m unittest discover -s tests

Every test runs the real CLI in a temporary LEARN_EVERYTHING_HOME, so the
learner's real data is never touched.
"""
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LE = ROOT / "scripts" / "le.py"

spec = importlib.util.spec_from_file_location("le", str(LE))
le = importlib.util.module_from_spec(spec)
spec.loader.exec_module(le)

D0 = dt.date(2026, 3, 2)


def day(i):
    return (D0 + dt.timedelta(days=i)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.files = Path(self.tmp.name) / "files"
        self.files.mkdir()
        self.today = day(0)

    def tearDown(self):
        self.tmp.cleanup()

    def run_le(self, *args, payload=None, ok=True):
        env = dict(os.environ, LEARN_EVERYTHING_HOME=str(self.home), LE_TODAY=self.today)
        r = subprocess.run([sys.executable, str(LE)] + [str(a) for a in args],
                           input=None if payload is None else json.dumps(payload),
                           capture_output=True, text=True, env=env)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout) if r.stdout.lstrip().startswith("{") else r.stdout
        self.assertNotEqual(r.returncode, 0, r.stdout)
        return json.loads(r.stderr)["error"]

    def init(self, exam_date=None, minutes=30, language="English"):
        return self.run_le("init", payload={
            "name": "Ana", "language": language, "education_level": "University",
            "exam": {"name": "Física II", "exam_date": exam_date, "daily_minutes": minutes}})

    def roadmap(self, n=4):
        units = [{"name": "Unit A", "topics": [{"name": "Topic %d" % i, "sources": ["s1 p.%d" % i]}
                                               for i in range(n // 2)]},
                 {"name": "Unit B", "topics": [{"name": "Topic %d" % i} for i in range(n // 2, n)]}]
        return self.run_le("roadmap", "set", payload={"units": units})

    def card(self, topic="t1", quality=None, **kw):
        c = {"topic": topic, "type": "tf", "prompt": "Is it?", "answer": True}
        c.update(kw)
        if quality is not None:
            c["quality"] = quality
        return c

    def read(self, rel):
        return json.loads((self.home / rel).read_text(encoding="utf-8"))


class TestSetup(Base):
    def test_status_without_profile(self):
        self.assertEqual(self.run_le("status")["next_action"], "setup")
        self.assertIn("/learn:setup", self.run_le("status", "--hook"))
        self.assertIn("suggest", self.run_le("status"))

    def test_init_creates_and_updates(self):
        r = self.init(exam_date=day(30))
        self.assertEqual((r["profile"], r["exam"], r["slug"]), ("created", "created", "fisica-ii"))
        self.assertEqual(self.run_le("status")["next_action"], "add")
        r = self.run_le("init", payload={"name": "Ana M.", "exam": {"slug": "fisica-ii", "daily_minutes": 60}})
        self.assertEqual((r["profile"], r["exam"]), ("updated", "updated"))
        self.assertEqual(self.read("fisica-ii/exam.json")["daily_minutes"], 60)
        self.assertEqual(self.read("fisica-ii/exam.json")["exam_date"], day(30))
        self.assertEqual(self.read("profile.json")["name"], "Ana M.")

    def test_init_rejects_bad_values(self):
        self.assertIn("YYYY-MM-DD", self.run_le("init", ok=False, payload={"exam": {"name": "X", "exam_date": "soon"}}))
        self.assertIn("between", self.run_le("init", ok=False, payload={"exam": {"name": "X", "daily_minutes": 1}}))

    def test_two_exams_and_switch(self):
        self.init()
        self.run_le("init", payload={"exam": {"name": "History"}})
        self.assertEqual(self.read("profile.json")["active_exam"], "history")
        self.run_le("switch", "fisica-ii")
        self.assertEqual(len(self.run_le("list")["exams"]), 2)
        self.assertEqual(self.read("profile.json")["active_exam"], "fisica-ii")

    def test_hook_never_fails(self):
        self.init(exam_date=day(10), language="Español")
        (self.home / "fisica-ii" / "cards.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.run_le("status", "--hook"), "")


class TestSources(Base):
    def setUp(self):
        super().setUp()
        self.init()
        self.f = self.files / "notes.txt"
        self.f.write_text("Coulomb", encoding="utf-8")

    def test_dedupe_by_hash(self):
        (self.files / "skip.xyz").write_text("x")
        (self.files / ".hidden.txt").write_text("x")
        r = self.run_le("source", "check", self.files)
        self.assertEqual([(Path(f["path"]).name, f["status"]) for f in r["files"]], [("notes.txt", "new")])
        a = self.run_le("source", "add", payload={"path": str(self.f), "title": "T1", "pages": 3})
        self.assertEqual((a["status"], a["id"]), ("added", "s1"))
        copy = self.files / "renamed copy.txt"
        copy.write_text("Coulomb", encoding="utf-8")
        r = self.run_le("source", "check", copy)
        self.assertEqual((r["files"][0]["status"], r["files"][0]["id"]), ("known", "s1"))
        self.assertEqual(self.run_le("source", "add", payload={"path": str(copy)})["status"], "known")
        self.assertEqual(len(self.read("fisica-ii/sources/index.json")["sources"]), 1)

    def test_missing_notes_are_flagged(self):
        a = self.run_le("source", "add", payload={"path": str(self.f)})
        self.assertEqual(self.run_le("status")["sources_missing_notes"], ["s1"])
        Path(a["notes_path"]).write_text("# notes", encoding="utf-8")
        s = self.run_le("status")
        self.assertEqual((s["sources_missing_notes"], s["next_action"]), ([], "roadmap"))
        self.run_le("roadmap", "set", payload={"units": [{"name": "U", "topics": [{"name": "T"}]}]})
        self.assertEqual(self.run_le("status")["next_action"], "study")
        self.run_le("record", payload={"studied_topics": ["t1"],
                                       "new_cards": [{"topic": "t1", "type": "tf", "prompt": "p", "answer": True, "quality": 5}]})
        self.assertEqual(self.run_le("status")["next_action"], "done")

    def test_remove_moves_notes_to_trash(self):
        a = self.run_le("source", "add", payload={"path": str(self.f)})
        Path(a["notes_path"]).write_text("# notes", encoding="utf-8")
        self.run_le("source", "remove", "s1")
        self.assertEqual(self.run_le("source", "list")["sources"], [])
        self.assertEqual(len(list((self.home / ".trash").iterdir())), 1)
        self.assertTrue(self.f.exists(), "the original file must never be touched")

    def test_unsupported_and_missing(self):
        r = self.run_le("source", "check", self.files / "nope.pdf", ROOT / "LICENSE")
        self.assertEqual([f["status"] for f in r["files"]], ["not_found", "unsupported"])


class TestExtract(Base):
    def test_docx(self):
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        xml = ('<w:document xmlns:w="%s"><w:body>'
               '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Título</w:t></w:r></w:p>'
               '<w:p><w:r><w:t>Hola </w:t></w:r><w:r><w:t>mundo</w:t></w:r></w:p>'
               '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>a</w:t></w:r></w:p></w:tc>'
               '<w:tc><w:p><w:r><w:t>b</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
               '</w:body></w:document>') % w
        f = self.files / "doc.docx"
        with zipfile.ZipFile(str(f), "w") as z:
            z.writestr("word/document.xml", xml)
            z.writestr("word/media/image1.png", b"png")
        text = self.run_le("extract", f)
        self.assertIn("# Título", text)
        self.assertIn("Hola mundo", text)
        self.assertIn("| a | b |", text)
        r = self.run_le("extract", f, "--out", self.files / "o.md", "--media", self.files / "m")
        self.assertEqual(r["embedded_images"], 1)
        self.assertTrue((self.files / "m" / "image1.png").exists())

    def test_pptx_with_notes(self):
        a = "http://schemas.openxmlformats.org/drawingml/2006/main"
        slide = '<p:sld xmlns:p="p" xmlns:a="%s"><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:sld>'
        f = self.files / "deck.pptx"
        with zipfile.ZipFile(str(f), "w") as z:
            z.writestr("ppt/slides/slide10.xml", slide % (a, "Ten"))
            z.writestr("ppt/slides/slide2.xml", slide % (a, "Two"))
            z.writestr("ppt/slides/_rels/slide2.xml.rels",
                       '<Relationships><Relationship Target="../notesSlides/notesSlide1.xml"/></Relationships>')
            z.writestr("ppt/notesSlides/notesSlide1.xml", slide % (a, "Remember this"))
        text = self.run_le("extract", f)
        self.assertLess(text.index("Two"), text.index("Ten"))
        self.assertIn("Notes: Remember this", text)

    def test_rejects_other_types(self):
        f = self.files / "a.txt"
        f.write_text("x")
        self.assertIn("only handles", self.run_le("extract", f, ok=False))
        bad = self.files / "bad.docx"
        bad.write_text("not a zip")
        self.assertIn("could not read", self.run_le("extract", bad, ok=False))

    def test_notebook(self):
        png = __import__("base64").b64encode(b"\x89PNG fake").decode()
        nb = {"metadata": {"kernelspec": {"language": "python"}}, "cells": [
            {"cell_type": "markdown", "source": ["# Regresión\n", "La recta $y = ax + b$."]},
            {"cell_type": "code", "source": "print(1 + 1)", "outputs": [
                {"output_type": "stream", "text": ["2\n"]},
                {"output_type": "display_data", "data": {"image/png": png, "text/plain": "<Figure>"}}]},
            {"cell_type": "code", "source": "1/0", "outputs": [
                {"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero"}]}]}
        f = self.files / "clase.ipynb"
        f.write_text(json.dumps(nb), encoding="utf-8")
        text = self.run_le("extract", f)
        for part in ("[cell 1]", "# Regresión", "```python\nprint(1 + 1)\n```", "Output:", "[image: cell-2-2.png]",
                     "ZeroDivisionError: division by zero", '"embedded_images":1'):
            self.assertIn(part, text)
        r = self.run_le("extract", f, "--out", self.files / "nb.md", "--media", self.files / "img")
        self.assertEqual((self.files / "img" / "cell-2-2.png").read_bytes(), b"\x89PNG fake")
        self.assertEqual(r["embedded_images"], 1)

    def test_rtf(self):
        f = self.files / "apuntes.rtf"
        f.write_bytes(rb"{\rtf1\ansi{\fonttbl{\f0 Helvetica;}}{\*\generator Test;}\f0 Tema 1: Electrost\'e1tica\par "
                      rb"Ley de Coulomb \u8212? F = k\par{\info{\title secret}}Fin}")
        real = le.shutil.which
        le.shutil.which = lambda name: None  # the built-in reader, not macOS textutil
        try:
            text = le.rtf_text(f)
        finally:
            le.shutil.which = real
        self.assertEqual(text, "Tema 1: Electrostática\nLey de Coulomb — F = k\nFin")

    def test_long_output_goes_to_a_file(self):
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        body = "".join("<w:p><w:r><w:t>Párrafo número %d con bastante texto de relleno.</w:t></w:r></w:p>" % i
                       for i in range(1200))
        f = self.files / "largo.docx"
        with zipfile.ZipFile(str(f), "w") as z:
            z.writestr("word/document.xml", '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (w, body))
        r = self.run_le("extract", f)
        self.assertGreater(r["chars"], le.TEXT_INLINE_MAX)
        saved = Path(r["text_file"])
        self.assertTrue(str(saved).startswith(str(self.home)), "scratch files stay inside the data folder")
        self.assertIn("Párrafo número 1199", saved.read_text(encoding="utf-8"))


def build_pdf(objects, root=1):
    """A small PDF from {number: bytes body} (streams as (dict bytes, data)). No xref: le.py scans objects."""
    import zlib as _z
    out = [b"%PDF-1.5\n"]
    for num, body in sorted(objects.items()):
        if isinstance(body, tuple):
            head, data = body
            out.append(b"%d 0 obj\n<< %s /Length %d >>\nstream\n%s\nendstream\nendobj\n" % (num, head, len(data), data))
        else:
            out.append(b"%d 0 obj\n%s\nendobj\n" % (num, body))
    out.append(b"trailer\n<< /Root %d 0 R >>\n%%%%EOF\n" % root)
    return b"".join(out)


class TestPDF(Base):
    """The built-in reader, used when neither poppler nor macOS PDFKit is there."""

    def setUp(self):
        super().setUp()
        import zlib as _z
        page1 = _z.compress(b"BT /F1 12 Tf 72 720 Td (Tema 1: Electrost\\341tica) Tj 0 -14 Td "
                            b"[(Ley)-300(de)-300(Coulomb)] TJ T* (ignored-without-leading) Tj ET")
        page2 = b"BT /F2 10 Tf 1 0 0 1 72 700 Tm <000100020003> Tj ET q 200 0 0 150 0 0 cm /Im1 Do Q"
        tounicode = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap 1 begincodespacerange "
                     b"<0000> <FFFF> endcodespacerange 1 beginbfchar <0001> <03A9> endbfchar "
                     b"1 beginbfrange <0002> <0003> <0061> endbfrange endcmap end end")
        font1 = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        packed = b"4 0 " + font1  # font 4 lives inside a compressed object stream, as in modern PDFs
        self.jpeg = b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9"
        self.pdf = self.files / "apuntes.pdf"
        self.pdf.write_bytes(build_pdf({
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 /Resources << /Font << /F1 4 0 R >> >> >>",
            3: b"<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: (b"/Filter /FlateDecode", page1),
            6: b"<< /Type /Page /Parent 2 0 R /Contents 7 0 R /Resources << /Font << /F2 8 0 R >> "
               b"/XObject << /Im1 10 0 R >> >> >>",
            7: (b"", page2),
            8: b"<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H /ToUnicode 9 0 R >>",
            9: (b"", tounicode),
            10: (b"/Type /XObject /Subtype /Image /Width 200 /Height 150 /BitsPerComponent 8 "
                 b"/ColorSpace /DeviceRGB /Filter /DCTDecode", self.jpeg),
            11: (b"/Type /ObjStm /N 1 /First 4 /Filter /FlateDecode", _z.compress(packed)),
        }))

    def run_builtin(self, *args, ok=True):
        old = os.environ.get("LE_PDF_ENGINE")
        os.environ["LE_PDF_ENGINE"] = "builtin"
        try:
            return self.run_le(*args, ok=ok)
        finally:
            if old is None:
                del os.environ["LE_PDF_ENGINE"]
            else:
                os.environ["LE_PDF_ENGINE"] = old

    def test_text_by_page(self):
        text = self.run_builtin("extract", self.pdf)
        self.assertIn("[p. 1]\nTema 1: Electrostática\nLey de Coulomb", text)
        self.assertIn("[p. 2]\nΩab", text)
        meta = json.loads(text.split("--- le.py: ", 1)[1])
        self.assertEqual((meta["pages"], meta["engine"], meta["little_text"]), (2, "builtin", "1-2"))
        self.assertIn("--pages 1-2", meta["render"])
        only2 = self.run_builtin("extract", self.pdf, "--pages", "2")
        self.assertNotIn("Coulomb", only2)

    def test_render_pulls_out_scanned_pictures(self):
        r = self.run_builtin("render", self.pdf, "--pages", "2")
        self.assertEqual(r["engine"], "builtin")
        self.assertEqual(Path(r["images"][0]).read_bytes(), self.jpeg)
        self.assertIn(str(self.home), r["images"][0])
        self.assertIn("could not", self.run_builtin("render", self.pdf, "--pages", "1", ok=False))
        self.assertIn("at most 20", self.run_builtin("render", self.pdf, "--pages", "1-30", ok=False))

    def test_flate_image_becomes_png(self):
        import zlib as _z
        pixels = bytes(i % 256 for i in range(160 * 120))  # 160 x 120 grey
        pdf = self.files / "grey.pdf"
        pdf.write_bytes(build_pdf({
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /Resources << /XObject << /I 4 0 R >> >> >>",
            4: (b"/Subtype /Image /Width 160 /Height 120 /BitsPerComponent 8 /ColorSpace /DeviceGray "
                b"/Filter /FlateDecode", _z.compress(pixels[:160 * 120])),
        }))
        png = Path(self.run_builtin("render", pdf, "--pages", "1")["images"][0]).read_bytes()
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct_unpack(">II", png[16:24]), (160, 120))

    def test_not_a_pdf(self):
        fake = self.files / "fake.pdf"
        fake.write_text("hello")
        self.assertIn("could not read", self.run_builtin("extract", fake, ok=False))

    def test_page_ranges(self):
        self.assertEqual(le.parse_pages("1-3, 7,2", 5), [1, 2, 3])
        self.assertEqual(le.parse_pages("3-1,9"), [1, 2, 3, 9])
        self.assertEqual(le.page_ranges([9, 1, 2, 3, 5]), "1-3,5,9")


def struct_unpack(fmt, data):
    return __import__("struct").unpack(fmt, data)


class TestSM2(unittest.TestCase):
    def test_intervals(self):
        c, d = {}, D0
        le.sm2(c, 5, d)
        self.assertEqual((c["interval"], c["reps"], c["ef"]), (1, 1, 2.6))
        le.sm2(c, 5, d)
        self.assertEqual((c["interval"], c["reps"], c["ef"]), (6, 2, 2.7))
        le.sm2(c, 4, d)
        self.assertEqual((c["interval"], c["reps"], c["ef"]), (17, 3, 2.7))  # ceil(6 * 2.7)
        self.assertEqual(c["due"], (d + dt.timedelta(days=17)).isoformat())

    def test_failure_resets(self):
        c = {"ef": 2.5, "interval": 17, "reps": 3}
        le.sm2(c, 1, D0)
        self.assertEqual((c["interval"], c["reps"], c["lapses"]), (1, 0, 1))
        self.assertEqual(c["ef"], 1.96)

    def test_ef_floor(self):
        c = {}
        for _ in range(12):
            le.sm2(c, 0, D0)
        self.assertEqual(c["ef"], 1.3)
        self.assertEqual(len(c["history"]), 12)

    def test_interval_is_cut_before_the_exam(self):
        c = {"ef": 2.5, "interval": 6, "reps": 2}
        le.sm2(c, 5, D0, D0 + dt.timedelta(days=10))
        self.assertEqual(c["interval"], 5)  # would be 15, exam in 10 -> half
        c = {"ef": 2.5, "interval": 6, "reps": 2}
        le.sm2(c, 5, D0, D0 + dt.timedelta(days=1))
        self.assertEqual(c["interval"], 1)
        c = {"ef": 2.5, "interval": 6, "reps": 2}
        le.sm2(c, 5, D0, D0 - dt.timedelta(days=3))  # exam passed: plain SM-2
        self.assertEqual(c["interval"], 15)


class TestLevelsAndStreak(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(le.level_info(0)["level"], 1)
        self.assertEqual(le.level_info(99)["level"], 1)
        self.assertEqual(le.level_info(100)["level"], 2)
        self.assertEqual(le.level_info(300)["level"], 3)
        self.assertEqual(le.level_info(100)["next"], 300)

    def test_streak(self):
        d = D0
        iso = lambda i: (d - dt.timedelta(days=i)).isoformat()
        self.assertEqual(le.streak({}, d), 0)
        self.assertEqual(le.streak({"study_days": [iso(0), iso(1), iso(2)]}, d), 3)
        self.assertEqual(le.streak({"study_days": [iso(1), iso(2)]}, d), 2)       # today still open
        self.assertEqual(le.streak({"study_days": [iso(2), iso(3)]}, d), 0)       # broken
        self.assertEqual(le.streak({"study_days": [iso(0), iso(2)], "freeze_days": [iso(1)]}, d), 2)


class TestPlan(Base):
    def test_with_exam_date(self):
        self.init(exam_date=day(10))
        r = self.roadmap(8)["plan"]
        # 10 days, 2 reserved for review -> 8 topics over 8 days
        self.assertEqual((r["days_left"], r["new_per_day"], r["new_today_remaining"]), (10, 1, 1))
        self.assertFalse(r["overload"])

    def test_tight_plan_is_flagged(self):
        self.init(exam_date=day(4), minutes=15)
        r = self.roadmap(8)["plan"]
        self.assertEqual(r["new_per_day"], 4)  # 4 days, 2 reserved
        self.assertTrue(r["overload"])

    def test_without_exam_date(self):
        self.init(minutes=60)
        r = self.roadmap(8)["plan"]
        self.assertEqual((r["days_left"], r["new_per_day"]), (None, 2))
        self.assertEqual(r["finish_estimate"], day(4))

    def test_missed_day_redistributes(self):
        self.init(exam_date=day(6))
        self.roadmap(4)
        self.assertEqual(self.run_le("status")["plan"]["new_per_day"], 1)  # 4 topics / (6 - 2)
        self.today = day(3)
        p = self.run_le("status")["plan"]
        self.assertEqual((p["days_left"], p["new_per_day"]), (3, 4))       # 4 topics / (3 - 2)

    def test_exam_day_and_past(self):
        self.init(exam_date=day(0))
        self.roadmap(2)
        self.assertEqual(self.run_le("status")["plan"]["phase"], "exam_day")
        self.today = day(1)
        self.assertEqual(self.run_le("status")["plan"]["phase"], "past")


class TestCards(Base):
    def setUp(self):
        super().setUp()
        self.init()
        self.roadmap(4)

    def test_validation(self):
        bad = [
            ({"topic": "t1", "type": "nope", "prompt": "p", "answer": "a"}, "type must be"),
            ({"topic": "t99", "type": "tf", "prompt": "p", "answer": True}, "unknown topic"),
            ({"topic": "t1", "type": "mcq", "prompt": "p", "options": ["a", "b"], "answer": "c"}, "mcq"),
            ({"topic": "t1", "type": "multi", "prompt": "p", "options": ["a", "b"], "answer": ["z"]}, "multi"),
            ({"topic": "t1", "type": "tf", "prompt": "p", "answer": "yes"}, "true or false"),
            ({"topic": "t1", "type": "cloze", "prompt": "no gap", "answer": "x"}, "___"),
            ({"topic": "t1", "type": "order", "prompt": "p", "answer": ["one"]}, "order"),
            ({"topic": "t1", "type": "match", "prompt": "p", "answer": [["a", "b"], ["c"]]}, "match"),
            ({"topic": "t1", "type": "short", "prompt": "", "answer": "x"}, "prompt"),
        ]
        for card, msg in bad:
            self.assertIn(msg, self.run_le("cards", "add", ok=False, payload=[card]))
        self.assertEqual(self.read("fisica-ii/cards.json")["cards"], [])

    def test_all_types_are_accepted(self):
        good = [
            {"topic": "t1", "type": "mcq", "prompt": "p", "options": ["a", "b", "c"], "answer": "b"},
            {"topic": "t1", "type": "multi", "prompt": "p", "options": ["a", "b", "c"], "answer": ["a", "c"]},
            {"topic": "t1", "type": "tf", "prompt": "p", "answer": False},
            {"topic": "t1", "type": "cloze", "prompt": "a ___ b", "answer": "x", "accepted": ["y"]},
            {"topic": "t2", "type": "short", "prompt": "p", "answer": "x"},
            {"topic": "t2", "type": "essay", "prompt": "p", "answer": "model", "rubric": ["k1", "k2"]},
            {"topic": "t2", "type": "order", "prompt": "p", "answer": ["1", "2", "3"]},
            {"topic": "t2", "type": "match", "prompt": "p", "answer": [["a", "1"], ["b", "2"]]},
        ]
        r = self.run_le("cards", "add", payload={"cards": good})
        self.assertEqual(len(r["added"]), 8)
        listed = self.run_le("cards", "list", "--topic", "t2")["cards"]
        self.assertEqual(len(listed), 4)
        self.assertNotIn("answer", listed[0])
        self.assertEqual(self.read("fisica-ii/cards.json")["cards"][0]["due"], day(1))

    def test_roadmap_merge_keeps_progress(self):
        self.run_le("record", payload={"studied_topics": ["t1"], "new_cards": [self.card(quality=5)]})
        units = [{"name": "Unit A", "topics": [{"name": "A brand new one"}, {"name": "topic 0"}, {"name": "Topic 1"}]}]
        err = self.run_le("roadmap", "set", ok=False, payload={"units": [{"name": "U", "topics": [{"name": "Other"}]}]})
        self.assertIn("Topic 0", err)
        self.run_le("roadmap", "set", payload={"units": units})
        topics = self.read("fisica-ii/exam.json")["units"][0]["topics"]
        self.assertEqual([t["id"] for t in topics], ["t5", "t1", "t2"])
        self.assertEqual(topics[1]["studied"], day(0))
        r = self.run_le("roadmap", "set", "--drop", payload={"units": [{"name": "U", "topics": [{"name": "Other"}]}]})
        self.assertEqual(r["cards_dropped"], 1)


class TestRecord(Base):
    def setUp(self):
        super().setUp()
        self.init(exam_date=day(40))
        self.roadmap(4)

    def study(self, topic=None, qualities=(5, 5, 5, 5, 5), answers=None, **extra):
        p = {"new_cards": [self.card(topic or "t1", q) for q in qualities], "minutes": 20}
        if topic:
            p["studied_topics"] = [topic]
        if answers:
            p["answers"] = answers
        p.update(extra)
        return self.run_le("record", payload=p)

    def test_first_session(self):
        nxt = self.run_le("next")
        self.assertEqual((nxt["reviews"], nxt["new_topics"]), (0, ["Topic 0"]))
        sess = json.loads(Path(nxt["session_file"]).read_text(encoding="utf-8"))
        self.assertEqual(sess["new_topics"][0]["id"], "t1")
        r = self.study("t1", qualities=(5, 4, 3, 2, 0))
        self.assertEqual((r["answers"], r["correct"], r["streak"]), (5, 3, 1))
        # 10 + 10 + 7 + 2 + 2 answers, 20 topic, 30 daily goal
        self.assertEqual(r["xp_gained"], 81)
        self.assertTrue(r["goal_met_now"])
        self.assertEqual([a["id"] for a in r["new_achievements"]], ["first_session"])
        self.assertFalse(Path(nxt["session_file"]).exists())
        log = (self.home / "fisica-ii" / "log.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(log[0])["xp"], 81)

    def test_invalid_payload_writes_nothing(self):
        before = (self.home / "fisica-ii" / "cards.json").read_text()
        for payload, msg in [
            ({"answers": [{"card": "c404", "quality": 5}]}, "unknown card"),
            ({"new_cards": [self.card(quality=9)]}, "0 to 5"),
            ({"studied_topics": ["t404"]}, "unknown topic"),
            ({"kind": "party"}, "kind"),
            ({}, "nothing to record"),
            ({"new_cards": [self.card()]}, "nothing to record"),
        ]:
            self.assertIn(msg, self.run_le("record", ok=False, payload=payload))
        self.assertEqual((self.home / "fisica-ii" / "cards.json").read_text(), before)
        self.assertEqual(self.read("profile.json")["xp"], 0)
        self.assertEqual((self.home / "fisica-ii" / "log.jsonl").read_text(), "")

    def test_reviews_come_back_and_are_rescheduled(self):
        self.study("t1", qualities=(5, 1))
        self.assertEqual(self.run_le("next")["reviews"], 0)
        self.today = day(1)
        nxt = self.run_le("next")
        self.assertEqual((nxt["reviews"], nxt["new_topics"]), (2, ["Topic 1"]))
        sess = json.loads(Path(nxt["session_file"]).read_text(encoding="utf-8"))
        self.assertEqual(sess["reviews"][0]["lapses"], 1)  # failed cards first
        r = self.run_le("record", payload={"answers": [{"card": "c1", "quality": 5}, {"card": "c2", "quality": 5}]})
        self.assertFalse(r["goal_met_now"])  # the new topic is still pending
        self.assertEqual(r["remaining_today"], {"reviews": 0, "new_topics": 1})
        cards = {c["id"]: c for c in self.read("fisica-ii/cards.json")["cards"]}
        self.assertEqual((cards["c1"]["interval"], cards["c2"]["interval"]), (6, 1))

    def test_streak_and_freeze(self):
        for i in range(7):
            self.today = day(i)
            r = self.study(qualities=(5,))
        self.assertEqual((r["streak"], r["freeze_earned"], r["freezes"]), (7, True, 1))
        self.assertIn("streak_7", [a["id"] for a in r["new_achievements"]])
        self.today = day(8)  # day 7 was missed -> the freeze covers it
        r = self.study(qualities=(5,))
        self.assertEqual((r["streak"], r["freeze_used"], r["freezes"]), (8, True, 0))
        self.today = day(10)  # day 9 missed and no freeze left
        r = self.study(qualities=(5,))
        self.assertEqual((r["streak"], r["freeze_used"]), (1, False))
        self.today = day(10)
        self.assertEqual(self.study(qualities=(5,))["streak"], 1)  # same day twice

    def test_achievements(self):
        r = self.study("t1")
        got = {a["id"] for a in r["new_achievements"]}
        self.assertEqual(got, {"first_session", "perfect_session"})
        r = self.study("t2")
        self.assertEqual({a["id"] for a in r["new_achievements"]}, {"unit_complete", "halfway"})
        r = self.study("t3")
        r = self.study("t4")
        self.assertEqual({a["id"] for a in r["new_achievements"]}, {"roadmap_complete"})
        # answers_50
        r = self.study(qualities=(4,) * 30)
        self.assertEqual({a["id"] for a in r["new_achievements"]}, {"answers_50"})
        self.assertEqual(len(self.read("profile.json")["achievements"]), 6)

    def test_mastery_and_comeback(self):
        self.study("t1", qualities=(5,))
        self.assertEqual(self.run_le("roadmap", "show")["units"][0]["topics"][0]["status"], "reviewing")
        got = set()
        for i, q in ((1, 1), (2, 1), (3, 5), (4, 5), (10, 5)):
            self.today = day(i)
            r = self.run_le("record", payload={"answers": [{"card": "c1", "quality": q}]})
            got |= {a["id"] for a in r["new_achievements"]}
            if i == 2:
                self.assertEqual(r["topic_changes"], [])
                self.assertEqual(self.run_le("roadmap", "show")["units"][0]["topics"][0]["status"], "learning")
        self.assertEqual(r["topic_changes"][0]["to"], "mastered")
        self.assertLessEqual({"topic_mastered", "comeback"}, got)

    def test_mock(self):
        self.study("t1", qualities=(5, 5, 5, 5))
        self.today = day(1)
        m = self.run_le("mock", "-n", "6", "--seed", "3")
        self.assertEqual((m["questions"], m["missing"]), (4, 2))
        sess = json.loads(Path(m["session_file"]).read_text(encoding="utf-8"))
        answers = [{"card": q["id"], "quality": 5} for q in sess["questions"]]
        r = self.run_le("record", payload={"kind": "mock", "answers": answers,
                                           "new_cards": [self.card(quality=1), self.card(quality=5)]})
        self.assertEqual(r["mock"], {"score": 5, "total": 6, "pct": 83})
        self.assertEqual({a["id"] for a in r["new_achievements"]}, {"first_mock", "mock_80"})
        r = self.run_le("record", payload={"kind": "mock", "answers": answers[:1],
                                           "mock": {"score": 4.5, "total": 10}})
        self.assertEqual(r["mock"]["pct"], 45)

    def test_review_cap_and_extra(self):
        self.study("t1", qualities=(5,) * 30)
        self.today = day(1)
        nxt = self.run_le("next")
        self.assertEqual(nxt["reviews"], 20)  # 30 minutes -> 20 reviews
        sess = json.loads(Path(nxt["session_file"]).read_text(encoding="utf-8"))
        self.run_le("record", payload={"answers": [{"card": c["id"], "quality": 5} for c in sess["reviews"]],
                                       "studied_topics": ["t2"]})
        plan = self.run_le("status")["plan"]
        self.assertEqual((plan["reviews_today_remaining"], plan["new_today_remaining"], plan["due_now"]), (0, 0, 10))
        self.assertEqual(self.run_le("next")["reviews"], 0)
        self.assertEqual(self.run_le("next", "--extra")["reviews"], 10)


class TestDashboard(Base):
    def test_builds_a_self_contained_page(self):
        self.init(exam_date=day(20), language="Español")
        self.assertEqual(self.run_le("dashboard", "--no-open")["opened"], False)  # works when empty
        self.roadmap(4)
        self.run_le("record", payload={"studied_topics": ["t1"],
                                       "new_cards": [self.card(quality=5, prompt="</script><b>x")]})
        r = self.run_le("dashboard", "--no-open")
        html = Path(r["dashboard"]).read_text(encoding="utf-8")
        self.assertNotIn("/*__LE_DATA__*/null", html)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        self.assertNotIn("https://", html)
        blob = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
        data = json.loads(blob.replace("<\\/", "</"))
        self.assertEqual(data["lang"], "es")
        self.assertEqual(data["totals"]["studied"], 1)
        self.assertEqual(len(data["activity"]), 119)
        self.assertEqual(len(data["forecast"]), 14)
        self.assertEqual(len(data["achievements"]), len(le.ACHIEVEMENTS))
        self.assertEqual(data["units"][0]["topics"][0]["accuracy"], 100)
        self.assertEqual((data["locale"], data["t"]["contentMap"], data["learner"]["education_level"]),
                         ("es-ES", "Mapa de contenidos", "University"))
        prog = {a["id"]: a.get("progress") for a in data["achievements"]}
        self.assertEqual((prog["streak_3"], prog["halfway"], prog["first_session"]), ([1, 3], [1, 2], None))
        self.assertIn("focus", data)


class TestData(Base):
    def setUp(self):
        super().setUp()
        self.init()
        self.roadmap(2)
        self.run_le("record", payload={"studied_topics": ["t1"], "new_cards": [self.card(quality=5)]})

    def test_backups_rotate(self):
        for _ in range(14):
            self.run_le("backup")
        self.assertEqual(len(list((self.home / ".backups").iterdir())), le.BACKUPS_KEPT)

    def test_export_delete_restore(self):
        z = self.run_le("export")["export"]
        with zipfile.ZipFile(z) as f:
            self.assertIn("fisica-ii/cards.json", f.namelist())
            self.assertIn("profile.json", f.namelist())
        self.assertIn("--confirm", self.run_le("delete", "fisica-ii", ok=False))
        self.assertIn("--confirm", self.run_le("delete", "fisica-ii", "--confirm", "other", ok=False))
        r = self.run_le("delete", "fisica-ii", "--confirm", "fisica-ii")
        self.assertIsNone(r["active_exam"])
        self.assertTrue(Path(r["moved_to"]).exists())
        self.assertEqual(self.read("profile.json")["xp"], 60)  # xp survives
        r = self.run_le("restore", z)
        self.assertEqual((r["restored"], r["profile_restored"]), ("fisica-ii", False))
        self.assertEqual(len(self.read("fisica-ii/cards.json")["cards"]), 1)
        self.assertIn("--force", self.run_le("restore", z, ok=False))
        self.run_le("restore", z, "--force")
        self.assertEqual(self.run_le("status")["exam"]["slug"], "fisica-ii")

    def test_restore_rejects_unsafe_zip(self):
        bad = self.files / "bad.zip"
        with zipfile.ZipFile(str(bad), "w") as z:
            z.writestr("evil/exam.json", "{}")
            z.writestr("../outside.txt", "x")
        self.assertIn("unsafe", self.run_le("restore", bad, ok=False))
        self.assertFalse((self.home.parent / "outside.txt").exists())
        notzip = self.files / "x.zip"
        notzip.write_text("nope")
        self.assertIn("not a valid export", self.run_le("restore", notzip, ok=False))

    def test_atomic_save_leaves_no_temp_files(self):
        self.assertEqual([p.name for p in self.home.rglob("*.tmp")], [])
        target = self.home / "x.json"
        le.save(target, {"a": "ñ"})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": "ñ"})

    def test_torn_log_line_is_ignored(self):
        with open(self.home / "fisica-ii" / "log.jsonl", "a") as f:
            f.write('{"date": "2026-0')
        self.assertEqual(self.run_le("status")["cards"], 1)
        self.assertEqual(self.run_le("dashboard", "--no-open")["opened"], False)


class TestI18n(unittest.TestCase):
    def test_every_language_pack_is_complete(self):
        import re as _re
        packs = le.i18n()
        self.assertLessEqual({"en", "es", "fr", "de", "it", "pt"}, set(packs))
        en = packs["en"]
        holes = lambda text: set(_re.findall(r"\{(\w+)\}", text))
        for code, pack in packs.items():
            for key in ("name", "flag", "locale", "aliases"):
                self.assertIn(key, pack, code)
            for section in ("hook", "edu", "dashboard", "achievements"):
                self.assertEqual(set(pack[section]), set(en[section]), "%s/%s" % (code, section))
            for k, text in pack["hook"].items():
                self.assertEqual(holes(text), holes(en["hook"][k]), "%s hook.%s" % (code, k))
            for aid, _ in le.ACHIEVEMENTS:
                self.assertEqual(len(pack["achievements"][aid]), 2, "%s %s" % (code, aid))

    def test_language_names_map_to_packs(self):
        for text, code in [("Español", "es"), ("🇫🇷 Français", "fr"), ("de", "de"), ("pt-BR", "pt"),
                           ("Deutsch (Österreich)", "de"), ("spanish", "es"), ("Italiano", "it"),
                           ("English", "en"), ("Klingon", "en"), ("", "en")]:
            self.assertEqual(le.lang_code(text), code, text)


class TestRecapAndHook(Base):
    def setUp(self):
        super().setUp()
        self.init(exam_date=day(30), language="Deutsch")
        self.roadmap(4)

    def test_recap_and_hook_lines(self):
        hook = self.run_le("status", "--hook")
        self.assertIn("noch 30 Tage bis zur Prüfung", hook)
        self.assertNotIn("Letzte Einheit", hook)  # nothing to recap yet
        self.run_le("record", payload={"studied_topics": ["t1"], "new_cards": [self.card("t1", 5) for _ in range(4)]})
        self.run_le("record", payload={"studied_topics": ["t2"], "new_cards": [self.card("t2", q) for q in (1, 2, 5)]})
        self.today = day(1)
        rc = self.run_le("status")["recap"]
        self.assertEqual(rc["last_session"]["topics"], ["Topic 1"])
        self.assertEqual((rc["strong"], rc["weak"], rc["next_topic"]), (["Topic 0"], ["Topic 1"], "Topic 2"))
        hook = self.run_le("status", "--hook").splitlines()
        self.assertTrue(all(line.startswith("[Learn Everything] ") for line in hook))
        text = "\n".join(hook)
        for part in ("Fortschritt: 2/4", "Letzte Einheit (gestern): Topic 1 · 1/3 richtig",
                     "Stark in: Topic 0", "Zu verbessern: Topic 1", "/learn:add"):
            self.assertIn(part, text)


class TestFindNotesPasted(Base):
    def setUp(self):
        super().setUp()
        self.init()

    def test_find_lists_recent_supported_files(self):
        sub = self.files / "Physik" / "deep"
        sub.mkdir(parents=True)
        for name in ("a.pdf", "Physik/b.docx", "Physik/deep/c.png", "skip.exe", ".hidden.pdf"):
            (self.files / name).write_text("x")
        old = self.files / "old.pdf"
        old.write_text("x")
        os.utime(str(old), (0, 0))
        self.run_le("source", "add", payload={"path": str(self.files / "a.pdf")})
        r = self.run_le("find", self.files)
        names = {Path(f["path"]).name: f["known"] for f in r["files"]}
        self.assertEqual(names, {"a.pdf": True, "b.docx": False, "c.png": False})
        self.assertEqual(len(self.run_le("find", self.files, "--depth", "1")["files"]), 2)

    def test_notes_excerpt_by_pages(self):
        self.run_le("source", "add", payload={"path": str(self.files / "x.txt"), "title": "X"}, ok=False)
        (self.files / "x.txt").write_text("x")
        sid = self.run_le("source", "add", payload={"path": str(self.files / "x.txt")})
        Path(sid["notes_path"]).write_text("\n".join([
            "# Chapter 1", "[p. 1] intro line", "## Part A", "[p. 3] alpha", "more alpha",
            "[p. 4] beta", "## Part B", "[p. 6] gamma", "# Chapter 2", "[slide 9] delta"]), encoding="utf-8")
        self.run_le("roadmap", "set", payload={"units": [{"name": "U", "topics": [
            {"name": "A", "sources": ["s1 p.3-4"]}, {"name": "B", "sources": ["s1"]},
            {"name": "C", "sources": ["s1 slide 9"]}]}]})
        out = self.run_le("notes", "t1")
        for part in ("# Chapter 1", "## Part A", "alpha", "more alpha", "beta", "pages 3-4"):
            self.assertIn(part, out)
        for part in ("intro line", "gamma", "Part B", "delta"):
            self.assertNotIn(part, out)
        self.assertIn("gamma", self.run_le("notes", "t2"))  # no pages -> whole file
        out = self.run_le("notes", "t3")
        self.assertIn("delta", out)
        self.assertNotIn("alpha", out)
        self.assertIn("unknown topic", self.run_le("notes", "t99", ok=False))
        sess = json.loads(Path(self.run_le("next")["session_file"]).read_text(encoding="utf-8"))
        self.assertEqual(sess["new_topics"][0]["notes_cmd"], "le.py notes t1")

    def test_pasted_source(self):
        self.assertIn("title", self.run_le("source", "add", ok=False, payload={"pasted": True}))
        r = self.run_le("source", "add", payload={"pasted": True, "title": "Whiteboard photo"})
        self.assertEqual(r["id"], "s1")
        src = self.read("fisica-ii/sources/index.json")["sources"][0]
        self.assertEqual((src["type"], src["sha256"], src["path"]), ("pasted", None, None))
        (self.files / "n.txt").write_text("x")
        self.assertEqual(self.run_le("source", "check", self.files)["new"], 1)

    def test_rename_exam_folder(self):
        self.run_le("source", "add", payload={"pasted": True, "title": "T"})
        r = self.run_le("init", payload={"exam": {"slug": "fisica-ii", "name": "Física III", "rename": True}})
        self.assertEqual((r["renamed_to"], r["slug"]), ("fisica-iii", "fisica-iii"))
        self.assertFalse((self.home / "fisica-ii").exists())
        self.assertEqual(self.read("profile.json")["active_exam"], "fisica-iii")
        self.assertEqual(self.read("fisica-iii/exam.json")["slug"], "fisica-iii")
        self.assertEqual(self.run_le("status")["sources"], 1)
        self.run_le("init", payload={"exam": {"name": "Other"}})
        self.assertIn("already exists", self.run_le("init", ok=False, payload={
            "exam": {"slug": "other", "name": "Física III", "rename": True}}))

    def test_roadmap_concepts(self):
        self.run_le("roadmap", "set", payload={"units": [{"name": "U", "topics": [
            {"name": "T", "concepts": ["c%d" % i for i in range(12)] + [" "]}]}]})
        self.assertEqual(len(self.read("fisica-ii/exam.json")["units"][0]["topics"][0]["concepts"]), 8)
        self.assertIn("concepts", self.run_le("roadmap", "set", ok=False, payload={
            "units": [{"name": "U", "topics": [{"name": "T", "concepts": "a, b"}]}]}))


class TestSeveralExams(Base):
    def setUp(self):
        super().setUp()
        self.init(exam_date=day(20), language="Español")
        self.roadmap(4)
        self.run_le("init", payload={"exam": {"name": "Historia", "exam_date": day(5)}})
        self.run_le("roadmap", "set", payload={"units": [{"name": "U", "topics": [{"name": "Roma"}]}]})

    def test_status_lists_every_exam_to_choose(self):
        s = self.run_le("status")
        self.assertTrue(s["choose_exam"])
        rows = {e["slug"]: e for e in s["exams"]}
        self.assertEqual(set(rows), {"fisica-ii", "historia"})
        self.assertEqual((rows["historia"]["days_left"], rows["historia"]["active"]), (5, True))
        self.assertEqual(rows["fisica-ii"]["today"]["new_topics"], 1)
        one = self.run_le("--exam", "fisica-ii", "status")
        self.assertEqual((one["exam"]["slug"], one.get("choose_exam")), ("fisica-ii", None))
        self.assertEqual(self.run_le("status", "--exam", "fisica-ii")["exam"]["slug"], "fisica-ii")

    def test_each_session_names_its_exam(self):
        """Two windows open at once: one studies Física, the other Historia. Neither moves the other."""
        self.run_le("record", "--exam", "fisica-ii", payload={"studied_topics": ["t1"]})
        self.run_le("record", "--exam", "historia", payload={"studied_topics": ["t1"]})
        self.assertEqual(self.read("profile.json")["active_exam"], "historia")
        self.assertTrue(self.read("fisica-ii/exam.json")["units"][0]["topics"][0]["studied"])
        self.assertEqual(self.read("profile.json")["xp"], 2 * le.XP_TOPIC + le.XP_GOAL)

    def test_parallel_writes_lose_nothing(self):
        env = dict(os.environ, LEARN_EVERYTHING_HOME=str(self.home), LE_TODAY=self.today)
        payload = json.dumps({"new_cards": [self.card("t1", 5)]})
        procs = [subprocess.Popen([sys.executable, str(LE), "--exam", slug, "record"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                 for slug in ("fisica-ii", "historia") * 4]
        for p in procs:
            p.communicate(payload.encode())
            self.assertEqual(p.returncode, 0)
        self.assertEqual(self.read("profile.json")["total_answers"], 8)

    def test_hook_mentions_the_other_exams(self):
        hook = self.run_le("status", "--hook")
        self.assertIn("Historia — faltan 5 días", hook)
        self.assertIn("También: Física II (20 días)", hook)

    def test_deleted_active_exam_asks_to_choose(self):
        self.run_le("delete", "historia", "--confirm", "historia")
        self.assertEqual(self.run_le("status")["exam"]["slug"], "fisica-ii")  # the other one takes over
        self.run_le("init", payload={"exam": {"name": "Arte"}})
        prof = self.read("profile.json")
        prof["active_exam"] = "gone"
        (self.home / "profile.json").write_text(json.dumps(prof), encoding="utf-8")
        self.assertEqual(self.run_le("status")["next_action"], "choose")

    def test_dashboards_link_to_each_other(self):
        r = self.run_le("dashboard", "--no-open")
        self.assertEqual(len(r["other_dashboards"]), 1)
        html = Path(r["dashboard"]).read_text(encoding="utf-8")
        data = json.loads(html.split("const DATA = ", 1)[1].split(";\n", 1)[0].replace("<\\/", "</"))
        self.assertEqual([(e["slug"], e["current"]) for e in data["exams"]], [("fisica-ii", False), ("historia", True)])
        self.assertEqual(data["exams"][0]["href"], "../fisica-ii/dashboard.html")
        self.assertTrue((self.home / "fisica-ii" / "dashboard.html").exists())
        self.assertEqual(data["units"][0]["topics"][0]["review"], "/learn:review historia t1")


class TestReview(Base):
    def setUp(self):
        super().setUp()
        self.init()
        (self.files / "a.txt").write_text("a")
        (self.files / "b.txt").write_text("b")
        self.run_le("source", "add", payload={"path": str(self.files / "a.txt"), "title": "Tema uno"})
        self.run_le("source", "add", payload={"path": str(self.files / "b.txt"), "title": "Ondas"})
        self.run_le("roadmap", "set", payload={"units": [
            {"name": "Electricidad", "topics": [{"name": "Ley de Coulomb", "sources": ["s1 p.1-3"]},
                                                {"name": "Campo eléctrico", "sources": ["s1 p.4"]}]},
            {"name": "Ondas", "topics": [{"name": "Interferencia", "sources": ["s2"]}]}]})
        self.run_le("record", payload={"studied_topics": ["t1", "t2"], "new_cards": [
            self.card("t1", 5, prompt="a"), self.card("t1", 1, prompt="b"), self.card("t1", 1, prompt="c"),
            self.card("t2", 5, prompt="d"), self.card("t2", 5, prompt="e"), self.card("t2", 4, prompt="f")]})

    def session(self, r):
        return json.loads(Path(r["session_file"]).read_text(encoding="utf-8"))

    def test_targets(self):
        for args, topics in [(["t2"], ["Campo eléctrico"]), (["u1"], ["Ley de Coulomb", "Campo eléctrico"]),
                             (["s2"], ["Interferencia"]), (["ley", "de", "coulomb"], ["Ley de Coulomb"]),
                             (["electricidad"], ["Ley de Coulomb", "Campo eléctrico"]), (["Ondas"], ["Interferencia"]),
                             ([], ["Ley de Coulomb"])]:
            self.assertEqual(self.run_le("review", *args)["topics"], topics, args)
        self.assertIn("nothing matches", self.run_le("review", "termodinámica", ok=False))
        r = self.run_le("review", "s2")
        self.assertEqual((r["questions"], r["not_studied_yet"]), (0, ["Interferencia"]))

    def test_weak_cards_first_and_recorded_as_review(self):
        r = self.run_le("review", "t1", "-n", "2")
        sess = self.session(r)
        self.assertEqual(sess["kind"], "review")
        self.assertEqual([q["prompt"] for q in sess["questions"]], ["b", "c"])  # failed ones first, even if not due
        self.assertEqual(sess["topics"][0]["notes_cmd"], "le.py notes t1")
        out = self.run_le("record", payload={"kind": "review", "answers": [{"card": "c2", "quality": 4}]})
        self.assertEqual(out["answers"], 1)
        self.assertEqual(read_log_kinds(self.home / "fisica-ii"), ["study", "review"])


def read_log_kinds(d):
    return [json.loads(l)["kind"] for l in (d / "log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


class TestLabels(Base):
    def test_translate_names_keeps_progress(self):
        self.init(language="English")
        self.run_le("source", "add", payload={"pasted": True, "title": "Apuntes de clase", "summary": "Resumen"})
        self.run_le("roadmap", "set", payload={"units": [{"name": "Fundamentos", "topics": [
            {"name": "Qué es el ML", "summary": "Definición", "concepts": ["experiencia", "tarea"]}]}]})
        self.run_le("record", payload={"studied_topics": ["t1"], "new_cards": [self.card("t1", 5)]})
        shown = self.run_le("labels", "show")
        self.assertEqual((shown["units"][0]["name"], shown["topics"][0]["concepts"], shown["sources"][0]["title"]),
                         ("Fundamentos", ["experiencia", "tarea"], "Apuntes de clase"))
        r = self.run_le("labels", "set", payload={
            "units": [{"id": "u1", "name": "Foundations"}],
            "topics": [{"id": "t1", "name": "What is ML", "summary": "Definition", "concepts": ["experience", "task"]}],
            "sources": [{"id": "s1", "title": "Class notes", "summary": "Summary"}]})
        self.assertEqual(r["changed"], 6)
        topic = self.read("fisica-ii/exam.json")["units"][0]["topics"][0]
        self.assertEqual((topic["name"], topic["concepts"], bool(topic["studied"])), ("What is ML", ["experience", "task"], True))
        self.assertEqual(self.read("fisica-ii/sources/index.json")["sources"][0]["title"], "Class notes")
        self.assertEqual(self.run_le("roadmap", "show")["units"][0]["topics"][0]["cards"], 1)
        self.assertIn("unknown id", self.run_le("labels", "set", ok=False, payload={"topics": [{"id": "t9", "name": "x"}]}))
        self.assertIn("non-empty", self.run_le("labels", "set", ok=False, payload={"units": [{"id": "u1", "name": " "}]}))


class TestPluginLayout(unittest.TestCase):
    def frontmatter(self, path):
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), path)
        head = text.split("---\n", 2)[1]
        return dict(line.split(": ", 1) for line in head.strip().splitlines() if ": " in line), text

    def test_skills(self):
        names = sorted(p.name for p in (ROOT / "skills").iterdir() if p.is_dir())
        self.assertEqual(names, ["add", "dashboard", "data", "exam", "learn", "review", "setup"])
        for name in names:
            meta, text = self.frontmatter(ROOT / "skills" / name / "SKILL.md")
            self.assertEqual(meta["name"], name)
            self.assertTrue(meta["description"].strip())
            # only the main skill is loaded into context; the others cost nothing until typed
            self.assertEqual(meta.get("disable-model-invocation") == "true", name != "learn", name)
            self.assertIn('${CLAUDE_PLUGIN_ROOT}/scripts/le.py', text, name)
            for ref in __import__("re").findall(r"`(\.\./[\w/.-]+\.md|references/[\w.-]+\.md)`", text):
                self.assertTrue((ROOT / "skills" / name / ref).resolve().exists(), "%s -> %s" % (name, ref))

    def test_versions_match(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "learn")
        self.assertEqual(market["plugins"][0]["name"], "learn")
        self.assertEqual({plugin["version"], market["plugins"][0]["version"], market["metadata"]["version"]},
                         {le.VERSION})


if __name__ == "__main__":
    unittest.main()
