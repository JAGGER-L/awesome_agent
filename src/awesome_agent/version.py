from __future__ import annotations

from importlib.metadata import version

_DISTRIBUTION_NAME = "awesome-agent"


def installed_product_version() -> str:
    return version(_DISTRIBUTION_NAME)


PRODUCT_VERSION = installed_product_version()


__all__ = ["PRODUCT_VERSION", "installed_product_version"]
