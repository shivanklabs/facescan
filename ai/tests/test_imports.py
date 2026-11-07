import importlib
import pkgutil

import facescan_ai


def test_all_modules_import() -> None:
    for mod in pkgutil.walk_packages(facescan_ai.__path__, "facescan_ai."):
        importlib.import_module(mod.name)
