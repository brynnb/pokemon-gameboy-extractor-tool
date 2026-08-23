"""Console wrappers that make checkout-relative defaults explicit.

The extractors operate on a pret/pokered source checkout.  Installed commands
therefore treat the current directory (or ``POKEMON_EXTRACTOR_WORKSPACE``) as
the workspace and configure every generated output before importing the legacy
top-level script modules.
"""

from __future__ import annotations

import os
from pathlib import Path


def configure_workspace(workspace: Path | None = None) -> Path:
    """Configure portable defaults for an installed command invocation."""
    configured = os.environ.get("POKEMON_EXTRACTOR_WORKSPACE")
    root = Path(workspace or configured or Path.cwd()).expanduser().resolve()
    public = root / "pokemon-phaser" / "public"

    defaults = {
        "POKEMON_EXTRACTOR_PROJECT_ROOT": root,
        "POKEMON_EXTRACTOR_GAME_DATA_ROOT": root / "pokemon-game-data",
        "POKEMON_EXTRACTOR_DB": root / "pokemon.db",
        "POKEMON_EXTRACTOR_TILE_IMAGE_DIR": root / "export_scripts" / "tile_images",
        "POKEMON_EXTRACTOR_VIEWER_PUBLIC_DIR": public,
        "POKEMON_EXTRACTOR_VIEWER_DATA_DIR": public / "viewer-data",
        "POKEMON_EXTRACTOR_VIEWER_ASSET_DIR": public / "viewer-assets",
        "POKEMON_EXTRACTOR_SCRIPT_EVENT_CANDIDATES": root / "script_event_candidates.json",
        "POKEMON_EXTRACTOR_SCRIPT_EVENT_IR": root / "script_event_ir.json",
        "POKEMON_EXTRACTOR_SCRIPT_EVENT_DIAGNOSTICS": root / "script_event_diagnostics.json",
        "POKEMON_EXTRACTOR_SCRIPT_EVENT_TRADES": root / "script_event_in_game_trades.json",
        "POKEMON_EXTRACTOR_SCRIPT_EVENT_TILE_OVERRIDES": root / "script_event_tile_overrides.json",
        "POKEMON_EXTRACTOR_SCRIPT_EVENT_BOULDER_TARGETS": root / "script_event_boulder_targets.json",
        "POKEMON_EXTRACTOR_SCRIPT_EVENT_OBJECT_VISIBILITY": root / "script_event_object_visibility.json",
        "POKEMON_EXTRACTOR_SCRIPT_EVENT_CONDITIONAL_DIALOGUE": root / "script_event_conditional_dialogue.json",
        "POKEMON_EXTRACTOR_AUDIO_MANIFEST": root / "audio_manifest.json",
        "POKEMON_EXTRACTOR_GRAPHICS_DIR": root / "build" / "graphics",
        "POKEMON_EXTRACTOR_AUDIO_DIR": root / "build" / "audio",
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, str(value))
    return root


def extract() -> None:
    """Run the complete metadata, database, and graphics extraction pipeline."""
    configure_workspace()
    from reprocess import main

    main()


def render_audio() -> None:
    """Run the audio renderer, forwarding its normal command-line options."""
    configure_workspace()
    from render_audio_assets import main

    main()


def catalogue_graphics() -> None:
    """Run the graphics catalogue exporter and renderer."""
    configure_workspace()
    from export_graphics import main

    main()
