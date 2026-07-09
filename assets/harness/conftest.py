import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "agent")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
