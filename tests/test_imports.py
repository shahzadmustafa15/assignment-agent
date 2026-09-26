"""Ensure the scaffold imports without external services or credentials."""

from importlib import import_module


def test_project_imports():
    for name in (
        "app", "app.main", "app.config", "app.gmail_monitor",
        "app.assignment_parser", "app.database", "app.notifier",
        "app.scheduler", "app.models",
    ):
        assert import_module(name) is not None
