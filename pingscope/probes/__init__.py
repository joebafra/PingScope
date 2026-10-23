"""Probe plugins.

Adding a new probe type:
  1. Create a module in this package with a subclass of `Probe`.
  2. Decorate it with `@register` and import the module at the bottom of this file.
It then appears in the admin panel automatically.
"""
from .base import PROBES, Probe, Result, register  # noqa: F401
from . import ping, dns, tcp  # noqa: F401,E402  (registration side effects)
