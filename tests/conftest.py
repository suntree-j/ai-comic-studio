# -*- coding: utf-8 -*-
import os
import sys

# 让 tests 能 import 到 packages.*
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
