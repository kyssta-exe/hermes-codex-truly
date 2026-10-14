"""Bootstrap the flat, hyphenated plugin directory for pytest.

``codex-truly`` is not a legal Python identifier, so the plugin cannot be
imported by name. This mirrors Hermes' own capability probe: load ``__init__.py``
as a package with ``submodule_search_locations`` pointing at the plugin dir, and
register it in ``sys.modules`` before anything imports a sibling module. Without
it both pytest and a plain ``import`` fail on the hyphenated directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "codex_truly_plugin"


def bootstrap() -> None:
    if PACKAGE_NAME in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME, PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)


bootstrap()

# pytest walks up from tests/ and, finding __init__.py at the plugin root, builds
# a Package node for it. It resolves that file's module name to a bare "__init__"
# (the directory name is hyphenated, so no dotted name is valid) and imports it --
# executing `from . import cli` with no parent package. Pre-seeding sys.modules
# under that exact name makes pytest reuse the module bootstrapped above instead
# of re-importing the plugin root as a top-level module.
sys.modules.setdefault("__init__", sys.modules[PACKAGE_NAME])
