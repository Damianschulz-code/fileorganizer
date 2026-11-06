import tempfile
import unittest
from pathlib import Path

import organizer


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, name, content="x"):
        p = self.dir / name
        p.write_text(content)
        return p

    def run_cli(self, *args):
        return organizer.main([str(self.dir), "--min-age", "0", *args])

    def test_sorts_by_category(self):
        self.make("a.jpg", "1"); self.make("b.pdf", "22"); self.make("c.xyz", "333")
        self.run_cli()
        self.assertTrue((self.dir / "Изображения" / "a.jpg").exists())
        self.assertTrue((self.dir / "Документы" / "b.pdf").exists())
        self.assertTrue((self.dir / "Другое" / "c.xyz").exists())

    def test_dry_run_changes_nothing(self):
        self.make("a.jpg")
        self.run_cli("--dry-run")
        self.assertTrue((self.dir / "a.jpg").exists())
        self.assertFalse((self.dir / "Изображения").exists())

    def test_duplicates_go_to_separate_folder(self):
        self.make("one.txt", "same"); self.make("two.txt", "same"); self.make("three.txt", "different")
        self.run_cli()
        self.assertEqual(len(list((self.dir / "Дубликаты").iterdir())), 1)
        self.assertEqual(len(list((self.dir / "Документы").iterdir())), 2)

    def test_name_conflict_gets_suffix(self):
        (self.dir / "Документы").mkdir()
        (self.dir / "Документы" / "a.txt").write_text("old")
        self.make("a.txt", "new")
        self.run_cli()
        self.assertTrue((self.dir / "Документы" / "a (1).txt").exists())

    def test_skips_temp_and_hidden_files(self):
        self.make("movie.crdownload"); self.make(".secret.jpg")
        self.run_cli()
        self.assertTrue((self.dir / "movie.crdownload").exists())
        self.assertTrue((self.dir / ".secret.jpg").exists())

    def test_recent_files_are_skipped(self):
        self.make("fresh.jpg")
        organizer.main([str(self.dir), "--min-age", "3600"])
        self.assertTrue((self.dir / "fresh.jpg").exists())

    def test_undo_restores_files_and_removes_empty_folders(self):
        self.make("a.jpg", "1"); self.make("b.pdf", "22")
        self.run_cli()
        self.assertEqual(organizer.main([str(self.dir), "--undo"]), 0)
        self.assertTrue((self.dir / "a.jpg").exists())
        self.assertTrue((self.dir / "b.pdf").exists())
        self.assertFalse((self.dir / "Изображения").exists())

    def test_report_is_written(self):
        self.make("a.jpg")
        report = self.dir.parent / "report-test.md"
        self.run_cli("--report", str(report))
        self.assertIn("Изображения", report.read_text(encoding="utf-8"))
        report.unlink()


if __name__ == "__main__":
    unittest.main()
