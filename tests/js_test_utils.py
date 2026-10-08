"""Optional Node support for fictional JavaScript behavior tests."""
import shutil

import pytest


def require_node():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable; optional fictional JavaScript behavior test')
    return node
