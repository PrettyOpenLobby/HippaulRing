"""Imports shared by the world modules, and the services directory on sys.path."""
import os as _os
import sys as _sys

#: The facade's path (feworld.py beside this package): what `__file__` meant
#: in the flat file.
FACADE_FILE = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                            "feworld.py")


def facade():
    """The feworld module object: what `sys.modules[__name__]` meant in the flat file."""
    return _sys.modules["feworld"]


import argparse
import json
import math
import os
import queue
import re
import select
import socket
import struct
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(FACADE_FILE))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import feident  # noqa: E402  -- WHOSE character this is; see its docstring
import fedevtool  # noqa: E402  -- the world-building panel (--devtool-port)
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
import fenet  # noqa: E402
import random as _random_mod                               # noqa: E402
