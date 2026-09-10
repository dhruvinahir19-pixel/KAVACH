import os
import sys

# scanner/ on sys.path so `kcore` imports cleanly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
