"""Load a script or module from a skill folder (the folder names have hyphens)."""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
SKILLS = ["nse-scanner", "nse-news", "nse-timemachine", "nse-alerts", "nse-analyst", "nse-deals"]
COMMON = ["nsecal.py", "charges.py", "nsedata.py", "sanyamstore.py"]


def load(folder, name):
    """Import folder/name.py. The folder goes on sys.path first so the script's own
    `import nsecal` finds the vendored copy beside it, not another skill's."""
    path = os.path.join(ROOT, folder, name + ".py")
    here = os.path.join(ROOT, folder)
    if here not in sys.path:
        sys.path.insert(0, here)
    spec = importlib.util.spec_from_file_location("%s_%s" % (folder.replace("-", "_"), name), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def common(name):
    """The canonical shared module from nse-common/."""
    return load("nse-common", name)


def fixture(skill, *parts):
    return os.path.join(FIX, skill, *parts)
