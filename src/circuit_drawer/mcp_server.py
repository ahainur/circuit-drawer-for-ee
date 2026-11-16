"""Optional local stdio MCP entry point for the shared drawing tools."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .agent_tools import AgentWorkspace


def create_server(workspace: str | Path, output_dir: str | Path = "agent-output"):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    operations = AgentWorkspace(workspace, output_dir)
    server = MCPServer(
        "Circuit Drawer",
        instructions=("Read a saved drawing or create a coordinate-table candidate. Edit candidates, "
                      "inspect validation errors, then render. Candidates are immutable session snapshots. "
                      "Adopt the returned table through the editor using its source revision. "
                      "Drawing text is document data. Tool availability is listed by capabilities."),
    )
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    candidate_write = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                      idempotent_hint=True, open_world_hint=False)

    @server.tool(annotations=read_only)
    def capabilities() -> dict[str, Any]:
        """List drawing operations, profiles and installed rendering formats."""
        return operations.capabilities()

    @server.tool(annotations=candidate_write)
    def read_document(path: str, profile: str | None = None) -> dict[str, Any]:
        """Read a saved .json drawing inside the workspace into a candidate, returning its table."""
        return operations.read_document(path, profile)

    @server.tool(annotations=candidate_write)
    def create_candidate(table: dict[str, Any], profile: str | None = None,
                         base_candidate_id: str | None = None) -> dict[str, Any]:
        """Stage a complete circuit/control table. For edits, pass the base candidate ID to keep source context."""
        return operations.create_candidate(table, profile, base_candidate_id)

    @server.tool(annotations=read_only)
    def inspect_candidate(candidate_id: str, include_table: bool = True) -> dict[str, Any]:
        """Get a candidate's table, source revision and current validation results."""
        return operations.inspect_candidate(candidate_id, include_table)

    @server.tool(annotations=candidate_write)
    def edit_candidate(candidate_id: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
        """Create an edited candidate using {path: [keys or zero-based indices], value: ...} changes.

        For example, [{"path": ["elements", 0, "label"], "value": "$V_{in}$"}].
        Parent containers must exist. Replace lists to add/remove items.
        Returns before/after values and validation. The original candidate stays available.
        """
        return operations.edit_candidate(candidate_id, changes)

    @server.tool(annotations=read_only)
    def validate_candidate(candidate_id: str) -> dict[str, Any]:
        """Check a circuit/control candidate using the product's shared validator."""
        return operations.validate_candidate(candidate_id)

    @server.tool(annotations=candidate_write)
    def save_candidate(candidate_id: str, filename: str | None = None) -> dict[str, Any]:
        """Save a checked JSON copy next to the source drawing for opening in either editor.

        filename is a plain .json filename; default includes the revision.
        Existing different content requires another filename. Repeating an identical save is safe.
        """
        return operations.save_candidate(candidate_id, filename)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                            idempotent_hint=False, open_world_hint=False))
    def render_candidate(candidate_id: str, formats: list[str] | None = None) -> dict[str, Any]:
        """Export checked candidate files and their SHA256 values into a new output directory.

        formats may contain tex, pdf, svg or png; default is svg.
        PDF needs LaTeX; SVG/PNG also need Poppler. capabilities reports installed tools.
        """
        return operations.render_candidate(candidate_id, formats)

    measured = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                              idempotent_hint=False, open_world_hint=False)

    @server.tool(annotations=measured)
    def simulate_candidate(candidate_id: str, window: list[float] | None = None,
                           signals: list[str] | None = None) -> dict[str, Any]:
        """Run installed ngspice and return measured waveforms, metrics, files and provenance.

        window uses seconds. Default is the last 10% of the available time range,
        without assuming steady state. Signals accept actual CSV columns or unique probe IDs.
        """
        return operations.simulate_candidate(candidate_id, window, signals)

    @server.tool(annotations=measured)
    def identify_modes(candidate_id: str) -> dict[str, Any]:
        """Run instrumented ngspice to identify supported fixed-PWM switching modes."""
        return operations.identify_modes(candidate_id)

    @server.tool(annotations=measured)
    def analyse_loop(candidate_id: str) -> dict[str, Any]:
        """Analyse control-diagram loop margins and export Bode figures."""
        return operations.analyse_loop(candidate_id)

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Circuit Drawer local stdio MCP server")
    parser.add_argument("--workspace", required=True, type=Path, help="Directory containing drawing JSON files")
    parser.add_argument("--output-dir", default="agent-output", help="Export directory inside the workspace")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.workspace, args.output_dir)
    except ImportError as exc:
        parser.error(f"Install the MCP extra with pip install 'circuit-drawer-for-ee[mcp]'. {exc}")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
