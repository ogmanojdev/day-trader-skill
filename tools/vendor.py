#!/usr/bin/env python3
"""Copy the canonical shared modules into every skill folder.

Each skill folder has to stand on its own: the install story is "copy the folder
into ~/.claude/skills/", so a skill cannot import a sibling. The US original
solved this by pasting its clock into nine files, where the copies then drifted
(two of the nine already differ). Here one canonical copy lives in nse-common/,
this script fans it out, and tests/test_common.py fails the suite if any copy
drifts. Edit nse-common/, never a vendored copy.

    python3 tools/vendor.py          # write the copies
    python3 tools/vendor.py --check  # exit 1 if any copy is stale
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "nse-common")
MODULES = ["nsecal.py", "charges.py", "nsedata.py", "sanyamstore.py"]
SKILLS = ["nse-scanner", "nse-news", "nse-timemachine", "nse-alerts", "nse-analyst", "nse-deals"]
BANNER = ("# Vendored from nse-common/%s by tools/vendor.py. Do not edit here:\n"
          "# edit nse-common/%s and re-run the script, or the suite will fail.\n")


def copies():
    for skill in SKILLS:
        for mod in MODULES:
            yield skill, mod, os.path.join(SRC, mod), os.path.join(ROOT, skill, mod)


def body(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def wanted(mod, src):
    return BANNER % (mod, mod) + body(src)


def main(argv):
    check = "--check" in argv
    stale = []
    for skill, mod, src, dst in copies():
        want = wanted(mod, src)
        have = body(dst) if os.path.exists(dst) else None
        if have == want:
            continue
        stale.append(os.path.relpath(dst, ROOT))
        if not check:
            if not os.path.isdir(os.path.dirname(dst)):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(want)
    if check:
        if stale:
            print("stale vendored copies (run python3 tools/vendor.py):", file=sys.stderr)
            for s in stale:
                print("  " + s, file=sys.stderr)
            return 1
        print("all %d vendored copies match nse-common/." % (len(SKILLS) * len(MODULES)))
        return 0
    print("wrote %d copies%s." % (len(stale), (": " + ", ".join(stale)) if stale else " (all were current)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
