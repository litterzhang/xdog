"""Compatibility imports for the standalone details panel."""
from xdog.tui.components.details_panel import DetailProvider, DetailRecord, DetailsPanel, streaming_preview

BoundedDetails = DetailsPanel
__all__ = ["BoundedDetails", "DetailProvider", "DetailRecord", "streaming_preview"]
