"""Deterministic, inert offline preview reports."""

from .report import build_preview_report
from .renderer import render_preview

__all__ = ["build_preview_report", "render_preview"]
