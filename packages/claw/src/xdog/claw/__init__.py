"""Claw persistent-assistant application.

Use the ``xdog-claw`` CLI to run the gateway or attach a terminal client.
Python modules under this package are application internals, not a public SDK.
Reusable Agent APIs belong to :mod:`xdog.agent`.

Importing this package does not initialize the application. Runtime bootstrap
explicitly imports :mod:`xdog.claw.core` to register Claw tools.
"""
