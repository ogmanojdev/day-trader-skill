import os
import subprocess
import sys
import unittest

from _load import COMMON, ROOT, SKILLS, common, load


class Vendoring(unittest.TestCase):
    def test_every_skill_carries_every_shared_module(self):
        for skill in SKILLS:
            for mod in COMMON:
                self.assertTrue(os.path.exists(os.path.join(ROOT, skill, mod)),
                                "%s/%s is missing" % (skill, mod))

    def test_no_vendored_copy_has_drifted(self):
        """A skill folder must stand alone, so the shared code is copied into each.

        The US original did the same by hand and two of its nine clock copies had
        already diverged. tools/vendor.py --check is the thing that stops that here,
        so the suite runs it.
        """
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "vendor.py"),
                              "--check"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_vendored_copy_is_the_canonical_body(self):
        for skill in SKILLS:
            for mod in COMMON:
                with open(os.path.join(ROOT, "nse-common", mod), encoding="utf-8") as fh:
                    canon = fh.read()
                with open(os.path.join(ROOT, skill, mod), encoding="utf-8") as fh:
                    copy = fh.read()
                self.assertTrue(copy.endswith(canon), "%s/%s" % (skill, mod))
                banner = "\n".join(copy.split("\n")[:2])
                self.assertIn("Do not edit here", banner)
                self.assertIn("nse-common/" + mod, banner)

    def test_each_skill_imports_its_own_copy_not_a_siblings(self):
        # The loader puts the skill folder first on sys.path for this reason.
        scan = load("nse-scanner", "scan")
        news = load("nse-news", "news")
        self.assertEqual(scan.nsecal.IST_OFFSET, news.nsecal.IST_OFFSET)


class Python38(unittest.TestCase):
    def test_no_module_that_arrived_after_3_8(self):
        """The tools promise Python 3.8. zoneinfo is 3.9, so the IST clock is plain
        timedelta arithmetic, which India's lack of daylight saving makes exact."""
        banned = ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(",
                  "datetime.UTC", "dt.UTC")
        for skill in SKILLS + ["nse-common"]:
            folder = os.path.join(ROOT, skill)
            for f in sorted(os.listdir(folder)):
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    src = fh.read()
                for bad in banned:
                    self.assertNotIn(bad, src, "%s/%s uses %s" % (skill, f, bad))

    def test_no_builtin_generic_annotations(self):
        import re
        for skill in SKILLS + ["nse-common"]:
            folder = os.path.join(ROOT, skill)
            for f in sorted(os.listdir(folder)):
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    src = fh.read()
                for bad in ("list[", "dict[", "tuple[", "set["):
                    self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad),
                                        "%s/%s uses %s" % (skill, f, bad))


class SharedWithSanyam(unittest.TestCase):
    def test_the_shared_modules_import_with_no_dependencies(self):
        """nse-common/ is meant to be importable by Sanyam as well, which is the whole
        reason the cost model lives there rather than inside one skill."""
        for mod in COMMON:
            m = common(mod[:-3])
            self.assertTrue(m.__doc__)

    def test_the_cost_model_answers_the_question_sanyam_cannot(self):
        ch = common("charges")
        rr = ch.net_reward_risk(1324.40, 1318.50, 1330.30, 100)
        self.assertLess(float(rr["net_rr"]), float(rr["gross_rr"]))


if __name__ == "__main__":
    unittest.main()
