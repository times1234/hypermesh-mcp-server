from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


APP_NAME = "hypermesh-mcp-server"
DEFAULT_HYPERMESH_DIR = Path(
    r"F:\Program Files\Altair\2020\hwdesktop\hw\bin\win64"
)
DEFAULT_HMBATCH = DEFAULT_HYPERMESH_DIR / "hmbatch.exe"
DEFAULT_HW = DEFAULT_HYPERMESH_DIR / "hw.exe"
DEFAULT_GUI_PORT = 47881
RUNS_DIR = Path(__file__).resolve().parent / "runs"

HYPERMESH_MESHING_STRATEGY = """
HyperMesh meshing strategy for this workstation:

1. Do not use solidmap for this workflow.
2. Use the existing b.hm-style size scale as the reference. For surface-deviation
   triangular surface mesh, use parameters close to: growth rate 1.23,
   minimum element size 0.5, maximum deviation 0.1, maximum feature angle 15,
   mesh type R-trias.
3. Tetra-volume parts must be meshed per object/component. Do not dump tetra
   elements from several objects into another component.
4. Flanges are tetra parts, not drag parts. A flange with bolt holes, stepped
   lips, bosses, or local cutouts must use surface-deviation R-trias followed
   by tetramesh, even if part of its outline looks circular.
5. For tetra strategy: first make 2D surface-deviation R-trias mesh, check/fix
   2D aspect > 10, then generate volume mesh with tetramesh, then check/fix
   volume skew > 0.99.
6. For simple straight tube/cylinder drag hex meshing, match logical edge seed
   counts, but do not blindly promote the whole source face to the largest outer
   edge count. If preview seed counts or edge lengths differ greatly, choose a
   balanced common count from the section scale (geometric-mean style) and use
   that one count for the mapped source face. Continue only when the source face
   is a mapped, 100% quad mesh. If that cannot be guaranteed, try a spin-hex
   strategy for suitable revolved bodies, otherwise fall back to tetra. The
   generated workflow must validate that real 3D hex elements were created; a
   leftover 2D section alone is a failure.
7. For obvious revolved bodies, prefer spin hex meshing, but never invent the
   section from guessed radii or from a side/end face. First split the solid with
   a real middle cutting plane, use only the newly created surfaces that lie on
   that cutting plane as 2D section sources, mesh those section surfaces as 100%
   quads, then spin to 3D. If the true cut section cannot be guaranteed as all
   quads, or if spin creates no valid 3D hex elements, clean up temporary shells
   and fall back to the tetra strategy.
8. Try a structured hex route before tetra when the geometry supports it:
   drag for simple constant-section extrusions, spin for clean true-section
   revolved solids, cut-section spin for stepped/recessed revolved solids.
   If the chosen hex route fails validation, fall back to tetra for that object.
   Clean bearing/ring-like revolved bodies should get a real cut-section spin
   attempt before tetra; direct surface-id spin is not enough unless the surface
   is already the true radial cross-section.
9. Component names should describe the physical object, not the mesh type.
   Examples: housing, shaft_ring, spacer_block_upper, support_flange.
10. Do not repair quality by blindly refining the whole mesh. Prefer strategy
    changes, local 3D smoothing/remesh, or sliver-tetra repair. If bad volume
    elements still cannot be fixed, keep them in the model and report them; do
    not delete unfixable quality-failed elements unless the user explicitly asks.
11. Gear, helical gear, or spline teeth are local fine-feature regions. Detect
    them only from true tooth geometry: alternating outer-radius peaks/valleys,
    repeated tooth flanks, twisted helical tooth faces, or explicit tooth/root
    surfaces. Do not treat smooth concentric bearing races, annular grooves, or
    cylindrical outer bands as gears. If exact tooth surface IDs are not known,
    auto-detect the outer gear band only after gear geometry evidence is present.
"""

GENERIC_MESHING_RULES = {
    "tetra_surface_deviation_rtrias": {
        "use_when": [
            "flanges or flange-like bodies",
            "parts with bolt holes, local holes, bosses, protrusions, ribs, grooves, cutouts, or non-sweepable topology",
            "parts whose source face cannot be proven as 100% quads with matched edge seeds",
            "fallback for ambiguous geometry",
        ],
        "required_checks": [
            "2D surface mesh aspect cleanup before tetramesh",
            "per-component tetramesh; do not mix several solids into one component",
            "3D volume quality check and local repair/report",
        ],
    },
    "drag_hex_guarded": {
        "use_when": [
            "simple straight extrusion or tube with constant section",
            "a real source face exists at one end of the extrusion",
            "all logical source-face edge groups can be forced to matched seed counts",
            "the source face meshes as 100% quads",
        ],
        "seed_policy": (
            "Match logical source-face counts, but when preview counts or edge "
            "lengths are highly different, choose a balanced common count rather "
            "than forcing inner edges up to the outer-edge count."
        ),
        "fallback": "tetra_surface_deviation_rtrias",
    },
    "spin_hex_guarded": {
        "use_when": [
            "clean revolved solid",
            "the selected source surface is already a true cross-section",
            "the source section meshes as 100% quads",
        ],
        "fallback": "cutsection_spin_hex for stepped/recessed revolved solids; otherwise tetra_surface_deviation_rtrias",
    },
    "cutsection_spin_hex": {
        "use_when": [
            "stepped, recessed, or ambiguous revolved solid",
            "no existing face can be trusted as the spin section",
            "a middle cutting plane through the rotation axis can be defined",
        ],
        "method": [
            "split the actual solid with body_splitmerge_with_plane",
            "detect newly created surfaces that lie on the cutting plane",
            "accept only all-quad section meshes on that plane",
            "spin the accepted 2D section into 3D hex elements",
        ],
        "fallback": "tetra_surface_deviation_rtrias",
    },
    "gear_aware_tetra": {
        "use_when": [
            "gear, helical gear, pinion, spline, or many repeated radial/oblique teeth are present",
            "external tooth evidence is present: alternating outer-radius peaks/valleys, repeated flanks, or twisted tooth faces",
            "not a smooth bearing/ring with only concentric races or annular grooves",
            "tooth surfaces need a smaller local 2D size than shaft/hub surfaces",
            "the whole part is not safely sweepable as one structured hex block",
        ],
        "method": [
            "identify repeated tooth/flank/root surfaces as the gear region, or auto-detect the outer gear band",
            "surface mesh shaft/hub surfaces with the base size",
            "surface mesh tooth-region surfaces with a smaller gear size",
            "tetra mesh the solid from the mixed-size surface shell mesh",
        ],
        "fallback": "tetra_surface_deviation_rtrias with uniform base size",
    },
}

SPECIAL_WORKFLOWS = {
    "visible_gui_mode": {
        "recommended": True,
        "listener_port": DEFAULT_GUI_PORT,
        "summary": (
            "Use create_gui_listener_tcl, manually source the generated Tcl in an "
            "already opened HyperMesh Tcl console when auto-launch does not work, "
            "then run execute_tcl_gui so the user can watch the model load, split, "
            "mesh, spin, and save in the visible GUI."
        ),
    },
    "cutsection_spin_hex": {
        "tool": "generate_cutsection_spin_hex_tcl",
        "method": [
            "Use this for stepped/recessed/ambiguous revolved solids where an existing face is not a trustworthy spin section.",
            "Split the target solid with body_splitmerge_with_plane using a user-provided middle plane.",
            "Detect the real section surfaces by meshing each new surface temporarily and checking node distance to the split plane.",
            "Accept only all-quad section meshes that lie on the split plane, then spin them 360 degrees about the x axis.",
            "Delete only the temporary 2D seed shell elements after spin; keep generated 3D hex elements.",
        ],
        "required_inputs": [
            "solid_id",
            "component_name",
            "split plane normal and point",
            "spin axis and axis point",
            "element size and spin density",
        ],
        "why": (
            "Direct surface-id spin is only safe when the selected surface is a "
            "true cross-section. For recessed or stepped rings, a real solid "
            "split is the reliable way to obtain that cross-section."
        ),
    },
    "quality_policy": {
        "policy": (
            "Try smoothing/sliver repair, but leave unfixable bad volume elements "
            "in the model and log their IDs. Do not delete them automatically."
        ),
    },
}

mcp = FastMCP(APP_NAME)


def _normalize_path(path: str | os.PathLike[str] | None) -> Path | None:
    if path is None or str(path).strip() == "":
        return None
    return Path(str(path).strip().strip('"')).expanduser()


def _candidate_hmbatch_paths() -> list[Path]:
    candidates: list[Path] = []

    env_path = _normalize_path(os.environ.get("HYPERMESH_BATCH_EXE"))
    if env_path:
        candidates.append(env_path)

    candidates.extend(
        [
            DEFAULT_HMBATCH,
            Path(r"F:\Program Files\Altair\2020\hwdesktop\hm\bin\win64\hmbatch.exe"),
            Path(r"C:\Program Files\Altair\2020\hwdesktop\hw\bin\win64\hmbatch.exe"),
            Path(r"C:\Program Files\Altair\2020\hwdesktop\hm\bin\win64\hmbatch.exe"),
        ]
    )

    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _candidate_hypermesh_gui_paths() -> list[Path]:
    candidates: list[Path] = []

    env_path = _normalize_path(os.environ.get("HYPERMESH_GUI_EXE"))
    if env_path:
        candidates.append(env_path)

    candidates.extend(
        [
            DEFAULT_HW,
            Path(r"F:\Program Files\Altair\2020\hwdesktop\hwx\bin\win64\hwx.exe"),
            Path(r"C:\Program Files\Altair\2020\hwdesktop\hw\bin\win64\hw.exe"),
            Path(r"C:\Program Files\Altair\2020\hwdesktop\hwx\bin\win64\hwx.exe"),
        ]
    )

    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _resolve_hmbatch(hmbatch_path: str | None = None) -> Path:
    explicit = _normalize_path(hmbatch_path)
    if explicit:
        if explicit.exists():
            return explicit
        raise FileNotFoundError(f"hmbatch.exe was not found: {explicit}")

    for candidate in _candidate_hmbatch_paths():
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Could not find hmbatch.exe. Set HYPERMESH_BATCH_EXE or pass hmbatch_path."
    )


def _resolve_hypermesh_gui(gui_path: str | None = None) -> Path:
    explicit = _normalize_path(gui_path)
    if explicit:
        if explicit.exists():
            return explicit
        raise FileNotFoundError(f"HyperMesh GUI executable was not found: {explicit}")

    for candidate in _candidate_hypermesh_gui_paths():
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Could not find hw.exe/hwx.exe. Set HYPERMESH_GUI_EXE or pass gui_path."
    )


def _quote_tcl_path(path: str | os.PathLike[str]) -> str:
    return str(Path(path)).replace("\\", "/").replace('"', '\\"')


def _balanced_seed_density(
    *,
    element_size: float,
    target_density: int | None,
    preview_edge_seed_counts: list[int] | None,
    source_edge_lengths: list[float] | None,
    ratio_threshold: float,
) -> tuple[int | None, str]:
    counts: list[int] = []
    if preview_edge_seed_counts:
        counts.extend(max(1, int(count)) for count in preview_edge_seed_counts)
    elif source_edge_lengths:
        counts.extend(
            max(1, round(float(length) / float(element_size)))
            for length in source_edge_lengths
        )

    if not counts:
        return target_density, "explicit" if target_density else "bbox_estimate"

    low = min(counts)
    high = max(counts)
    ratio = high / max(low, 1)
    if ratio >= ratio_threshold:
        balanced = round((low * high) ** 0.5)
        source = f"balanced_from_range_{low}_{high}"
    elif target_density:
        balanced = int(target_density)
        source = "explicit"
    else:
        balanced = round(sum(counts) / len(counts))
        source = f"average_from_preview_{low}_{high}"

    return max(4, min(120, int(balanced))), source


def _meshing_rule_violation(script: str) -> dict[str, Any] | None:
    """Reject raw meshing Tcl that bypasses MCP strategy generators."""
    lowered = script.lower()
    allowed_markers = (
        "mcp guarded drag hex",
        "mcp guarded spin hex",
        "mcp cut-section spin hex",
        "mcp gear-aware tetra",
        "mcp surface deviation r-trias",
        "mcp surface automesh",
    )
    if any(marker in lowered for marker in allowed_markers):
        return None

    has_drag = "*meshdragelements" in lowered
    has_spin = "*meshspinelements" in lowered
    has_tetra = "*tetmesh" in lowered
    has_surface_growth = "*defaultmeshsurf_growth" in lowered
    has_direct_seed = "*set_meshedgeparams" in lowered

    if has_drag or has_direct_seed:
        return {
            "success": False,
            "policy_violation": True,
            "blocked_command": "drag_or_seed",
            "required_tool": "generate_guarded_drag_hex_tcl",
            "message": (
                "Raw Tcl drag/seed commands are blocked because they bypass the "
                "MCP balanced seed policy. Use generate_guarded_drag_hex_tcl and "
                "pass preview_edge_seed_counts or source_edge_lengths so large "
                "inner/outer seed-count gaps are balanced instead of forcing all "
                "edges to the largest count."
            ),
        }

    if has_spin:
        return {
            "success": False,
            "policy_violation": True,
            "blocked_command": "spin",
            "required_tool": "generate_guarded_spin_hex_tcl or generate_cutsection_spin_hex_tcl",
            "message": (
                "Raw Tcl spin commands are blocked because they bypass MCP section "
                "validation. Use generate_guarded_spin_hex_tcl for a proven true "
                "section or generate_cutsection_spin_hex_tcl to cut the solid and "
                "spin the validated all-quad section."
            ),
        }

    if has_tetra or has_surface_growth:
        return {
            "success": False,
            "policy_violation": True,
            "blocked_command": "tetra_or_surface_growth",
            "required_tool": "generate_gear_aware_tetra_tcl or generate_surface_deviation_rtrias_tcl",
            "message": (
                "Raw Tcl tetra/surface-growth meshing commands are blocked because "
                "they bypass MCP geometry rules. If geometry inspection shows gear "
                "features, use generate_gear_aware_tetra_tcl so only tooth/flank/root "
                "or auto-detected outer gear-band faces are refined and the rest of "
                "the object keeps the base size. For non-gear geometry, use "
                "generate_surface_deviation_rtrias_tcl."
            ),
        }

    return None


def _ensure_runs_dir() -> Path:
    RUNS_DIR.mkdir(exist_ok=True)
    return RUNS_DIR


def _write_run_script(script: str) -> Path:
    run_dir = _ensure_runs_dir()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    script_path = run_dir / f"hypermesh_mcp_{timestamp}_{os.getpid()}.tcl"
    script_path.write_text(script, encoding="utf-8")
    return script_path


def _gui_listener_script(host: str = "127.0.0.1", port: int = DEFAULT_GUI_PORT) -> str:
    return f"""
# HyperMesh MCP GUI listener.
# Source this file inside a visible HyperMesh session, or launch HyperMesh with it.
set ::mcp_hm_host "{host}"
set ::mcp_hm_port {int(port)}

proc ::mcp_hm_accept {{chan addr client_port}} {{
    fconfigure $chan -blocking 1 -translation binary -encoding utf-8
    set script [read $chan]
    if {{[string trim $script] eq ""}} {{
        puts $chan "ERROR\\nempty Tcl script"
        close $chan
        return
    }}

    set code [catch {{uplevel #0 $script}} result options]
    if {{$code == 0 || $code == 2}} {{
        puts $chan "OK"
        if {{$result ne ""}} {{
            puts $chan $result
        }}
    }} else {{
        puts $chan "ERROR"
        puts $chan $result
        if {{[dict exists $options -errorinfo]}} {{
            puts $chan [dict get $options -errorinfo]
        }}
    }}
    flush $chan
    close $chan
}}

if {{[info exists ::mcp_hm_server]}} {{
    catch {{close $::mcp_hm_server}}
}}
set ::mcp_hm_server [socket -server ::mcp_hm_accept -myaddr $::mcp_hm_host $::mcp_hm_port]
puts "MCP HyperMesh GUI listener is ready on $::mcp_hm_host:$::mcp_hm_port"
""".lstrip()


def _run_hypermesh_gui_script(
    *,
    script: str,
    host: str = "127.0.0.1",
    port: int = DEFAULT_GUI_PORT,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    if not script.strip():
        raise ValueError("script cannot be empty.")

    with socket.create_connection((host, int(port)), timeout=max(1, int(timeout_seconds))) as sock:
        sock.settimeout(max(1, int(timeout_seconds)))
        sock.sendall(script.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)

    response = b"".join(chunks).decode("utf-8", errors="replace")
    return {
        "success": response.startswith("OK"),
        "host": host,
        "port": int(port),
        "response": response,
    }


def _run_hmbatch(
    *,
    hmbatch_path: str | None,
    script: str,
    model_path: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    exe = _resolve_hmbatch(hmbatch_path)
    script_path = _write_run_script(script)
    command = [str(exe), "-noexit", "-tcl", str(script_path)]

    model = _normalize_path(model_path)
    if model:
        if not model.exists():
            raise FileNotFoundError(f"Model file was not found: {model}")
        command.append(str(model))

    env = os.environ.copy()
    env.setdefault("ALTAIR_HOME", str(DEFAULT_HYPERMESH_DIR.parents[4]))

    try:
        completed = subprocess.run(
            command,
            cwd=str(script_path.parent),
            env=env,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
        )
        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "command": command,
            "script_path": str(script_path),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "timeout": True,
            "command": command,
            "script_path": str(script_path),
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "message": f"hmbatch did not finish within {timeout_seconds} seconds.",
        }


@mcp.tool()
def get_hypermesh_meshing_strategy() -> dict[str, Any]:
    """Return the local HyperMesh meshing strategy requested by the user."""
    return {
        "success": True,
        "strategy": HYPERMESH_MESHING_STRATEGY.strip(),
        "generic_rules": GENERIC_MESHING_RULES,
        "special_workflows": SPECIAL_WORKFLOWS,
        "default_hmbatch": str(DEFAULT_HMBATCH),
    }


@mcp.tool()
def get_meshing_rules() -> dict[str, Any]:
    """Return generic HyperMesh meshing rules without hard-coded component names."""
    return {
        "success": True,
        "generic_rules": GENERIC_MESHING_RULES,
        "special_workflows": SPECIAL_WORKFLOWS,
        "notes": [
            "Do not decide tetra/drag/spin by component name.",
            "Classify by geometry: holes/flanges/bosses/cutouts -> tetra; simple constant extrusions with matched quad source face -> drag; clean true cross-section revolved bodies -> spin.",
            "For stepped or recessed revolved solids, use the generic cut-section spin workflow rather than guessed surface-id spin.",
            "quality cleanup should prefer strategy changes and local repair; do not blindly refine or delete unfixable bad elements.",
            "visible GUI mode changes where the Tcl runs; meshing logic and input/output paths remain explicit.",
        ],
    }


@mcp.tool()
def get_cutsection_spin_workflow() -> dict[str, Any]:
    """Return the generic cut-section spin workflow for stepped/recessed revolved solids."""
    return {
        "success": True,
        "workflow": SPECIAL_WORKFLOWS["cutsection_spin_hex"],
        "gui_mode": SPECIAL_WORKFLOWS["visible_gui_mode"],
        "quality_policy": SPECIAL_WORKFLOWS["quality_policy"],
    }


@mcp.tool()
def classify_hypermesh_part_strategy(
    part_name: str = "",
    description: str = "",
    is_flange: bool = False,
    has_bolt_holes: bool = False,
    has_boss_or_protrusion: bool = False,
    is_simple_straight_tube: bool = False,
    is_constant_section_extrusion: bool = False,
    is_clean_revolved_section: bool = False,
    is_stepped_or_recessed_revolved: bool = False,
    has_gear_teeth: bool = False,
    has_helical_teeth: bool = False,
    has_twisted_tooth_faces: bool = False,
    has_many_repeated_radial_teeth: bool = False,
    has_periodic_outer_radius_variation: bool = False,
    has_outer_tooth_band: bool = False,
    has_repeated_tooth_flanks: bool = False,
    has_alternating_tooth_peaks_and_roots: bool = False,
    is_smooth_concentric_ring: bool = False,
    has_bearing_race_grooves: bool = False,
    has_annular_grooves_only: bool = False,
    tooth_count: int | None = None,
    outer_radius_variation_ratio: float | None = None,
    name_hint_indicates_gear: bool = False,
    source_faces_can_be_all_quads: bool = False,
    matched_inner_outer_seed_counts: bool = False,
) -> dict[str, Any]:
    """Classify a part into tetra, drag-hex, or spin-hex strategy."""
    text = f"{part_name} {description}".lower()
    flange_words = ("flange", "法兰")
    bolt_words = ("bolt", "hole", "孔", "螺栓", "螺孔")

    looks_like_flange = is_flange or any(word in text for word in flange_words)
    looks_like_bolted = has_bolt_holes or any(word in text for word in bolt_words)
    positive_gear_evidence_count = sum(
        1
        for flag in (
            has_gear_teeth,
            has_helical_teeth,
            has_twisted_tooth_faces,
            has_many_repeated_radial_teeth,
            has_periodic_outer_radius_variation,
            has_repeated_tooth_flanks,
            has_alternating_tooth_peaks_and_roots,
            tooth_count is not None and tooth_count >= 8,
            outer_radius_variation_ratio is not None and outer_radius_variation_ratio >= 0.04,
        )
        if flag
    )
    negative_bearing_evidence = (
        is_smooth_concentric_ring
        or has_bearing_race_grooves
        or has_annular_grooves_only
    )
    geometry_gear_evidence = (
        has_gear_teeth
        or has_helical_teeth
        or has_twisted_tooth_faces
        or has_many_repeated_radial_teeth
        or has_periodic_outer_radius_variation
        or has_repeated_tooth_flanks
        or has_alternating_tooth_peaks_and_roots
        or (tooth_count is not None and tooth_count >= 8)
        or (
            outer_radius_variation_ratio is not None
            and outer_radius_variation_ratio >= 0.04
            and (tooth_count is None or tooth_count >= 6)
        )
        or (has_outer_tooth_band and positive_gear_evidence_count >= 1)
    )
    looks_like_gear = not negative_bearing_evidence and (
        geometry_gear_evidence or name_hint_indicates_gear
    )
    stepped_tokens = (
        "step",
        "stepped",
        "recess",
        "recessed",
        "groove",
        "grooved",
        "凹",
        "台阶",
        "槽",
    )
    looks_stepped_revolved = is_stepped_or_recessed_revolved or (
        is_clean_revolved_section and any(word in text for word in stepped_tokens)
    )

    if looks_like_gear:
        return {
            "success": True,
            "strategy": "gear_aware_tetra",
            "reason": (
                "Repeated radial or helical teeth/spline features need local fine "
                "surface mesh on tooth faces while shaft and hub faces can keep "
                "the base size."
            ),
            "required_checks": [
                "identify tooth/flank/root surfaces from geometry: periodic outer-radius peaks, repeated flanks, or twisted helical faces",
                "do not classify gear regions from component names or natural-language labels",
                "if exact tooth surfaces are unknown, auto-detect the outer gear band from surface radii",
                "mesh gear-region surfaces with a smaller local element size",
                "mesh shaft/hub surfaces with the normal base element size",
                "tet elements remain in the part's own component",
                "3D vol skew <= 0.99 after repair/report",
            ],
            "name_hint_policy": (
                "A gear-like name is only a low-priority hint to inspect geometry. "
                "It must not classify the part as gear without tooth geometry evidence."
            ),
        }

    if looks_like_flange or looks_like_bolted:
        return {
            "success": True,
            "strategy": "tetra_surface_deviation_rtrias",
            "reason": (
                "Flange or bolted/holed part: use surface-deviation R-trias 2D "
                "mesh followed by per-component tetramesh. Do not drag this part."
            ),
            "required_checks": [
                "2D aspect <= 10 after cleanup",
                "3D vol skew <= 0.99 after repair",
                "tet elements remain in the part's own component",
            ],
        }

    if is_simple_straight_tube or is_constant_section_extrusion:
        if source_faces_can_be_all_quads and matched_inner_outer_seed_counts:
            return {
                "success": True,
                "strategy": "drag_hex",
                "reason": (
                    "Simple straight tube/extrusion with matched edge seeds and "
                    "100% quad source face."
                ),
                "required_checks": [
                    "inner and outer circumference seed counts match",
                    "source face contains only quads before drag",
                    "drag result contains hex elements only",
                    "if no valid 3D hex elements are created, clean up and fall back to tetra",
                    "3D vol skew <= 0.99",
                ],
            }
        return {
            "success": True,
            "strategy": "tetra_surface_deviation_rtrias",
            "reason": (
                "Straight tube/extrusion did not prove matched seeds plus 100% "
                "quad source face; fall back to tetra strategy."
            ),
            "required_checks": [
                "2D aspect <= 10 after cleanup",
                "3D vol skew <= 0.99 after repair",
            ],
        }

    if looks_stepped_revolved and not has_boss_or_protrusion:
        return {
            "success": True,
            "strategy": "cutsection_spin_hex",
            "reason": (
                "Stepped/recessed revolved body: split the actual solid with a "
                "middle plane, mesh the true cut section as all quads, then spin."
            ),
            "required_checks": [
                "cut plane passes through the intended rotation axis",
                "accepted section shell nodes lie on the cut plane",
                "accepted section contains only quads before spin",
                "spin result contains hex elements only",
                "a point on the actual spin axis must be supplied separately from the cut-plane point",
                "3D vol skew <= 0.99, or report remaining failures without deleting",
            ],
        }

    if is_clean_revolved_section and not has_boss_or_protrusion:
        if source_faces_can_be_all_quads and matched_inner_outer_seed_counts:
            return {
                "success": True,
                "strategy": "spin_hex",
                "reason": "Clean revolved section with all-quad source section.",
                "required_checks": [
                    "source section contains only quads before spin",
                    "spin result contains hex elements only",
                    "3D vol skew <= 0.99",
                ],
            }
        return {
            "success": True,
            "strategy": "cutsection_spin_hex",
            "reason": (
                "Clean revolved body but no trusted all-quad source section was "
                "proven. Split the real solid through the rotation axis and try "
                "cut-section spin before tetra fallback."
            ),
            "required_checks": [
                "cut plane passes through the intended rotation axis",
                "accepted section shell nodes lie on the cut plane",
                "accepted section contains only quads before spin",
                "spin result contains hex elements only",
                "if cut-section spin fails validation, fall back to tetra",
            ],
        }

    if has_boss_or_protrusion:
        return {
            "success": True,
            "strategy": "tetra_surface_deviation_rtrias",
            "reason": (
                "Boss/protrusion breaks simple drag topology. Use tetra unless a "
                "clean spin section is explicitly proven."
            ),
            "required_checks": [
                "2D aspect <= 10 after cleanup",
                "3D vol skew <= 0.99 after repair",
            ],
        }

    return {
        "success": True,
        "strategy": "tetra_surface_deviation_rtrias",
        "reason": "Default conservative strategy for unclassified complex geometry.",
        "required_checks": [
            "2D aspect <= 10 after cleanup",
            "3D vol skew <= 0.99 after repair",
        ],
    }


@mcp.tool()
def locate_hypermesh() -> dict[str, Any]:
    """Locate candidate HyperMesh batch and visible-GUI executables."""
    batch_found = [str(path) for path in _candidate_hmbatch_paths() if path.exists()]
    gui_found = [str(path) for path in _candidate_hypermesh_gui_paths() if path.exists()]
    selected = batch_found[0] if batch_found else None
    selected_gui = gui_found[0] if gui_found else None
    return {
        "success": selected is not None or selected_gui is not None,
        "selected": selected,
        "selected_gui": selected_gui,
        "found": batch_found,
        "found_gui": gui_found,
        "hint": (
            "Set HYPERMESH_BATCH_EXE for background batch mode, or "
            "HYPERMESH_GUI_EXE for visible GUI mode."
        ),
    }


@mcp.tool()
def create_gui_listener_tcl(
    host: str = "127.0.0.1",
    port: int = DEFAULT_GUI_PORT,
) -> dict[str, Any]:
    """Create the Tcl listener script used by a visible HyperMesh GUI session."""
    script_path = _write_run_script(_gui_listener_script(host=host, port=port))
    return {
        "success": True,
        "script_path": str(script_path),
        "host": host,
        "port": int(port),
        "how_to_use": (
            "Open HyperMesh visibly, then source this Tcl file in the Tcl command "
            "window. After that, execute_tcl_gui can send Tcl into the visible session."
        ),
    }


@mcp.tool()
def start_hypermesh_gui_listener(
    gui_path: str | None = None,
    model_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_GUI_PORT,
) -> dict[str, Any]:
    """Start visible HyperMesh and ask it to source the MCP GUI listener Tcl."""
    exe = _resolve_hypermesh_gui(gui_path)
    script_path = _write_run_script(_gui_listener_script(host=host, port=port))
    command = [str(exe), "-tcl", str(script_path)]

    model = _normalize_path(model_path)
    if model:
        if not model.exists():
            raise FileNotFoundError(f"Model file was not found: {model}")
        command.append(str(model))

    env = os.environ.copy()
    env.setdefault("ALTAIR_HOME", str(DEFAULT_HYPERMESH_DIR.parents[4]))
    process = subprocess.Popen(
        command,
        cwd=str(script_path.parent),
        env=env,
        close_fds=True,
    )
    return {
        "success": True,
        "pid": process.pid,
        "command": command,
        "script_path": str(script_path),
        "host": host,
        "port": int(port),
        "note": (
            "HyperMesh should open visibly. If this HyperMesh version ignores "
            "-tcl for GUI startup, open HyperMesh manually and source script_path."
        ),
    }


@mcp.tool()
def check_hypermesh_connection(hmbatch_path: str | None = None) -> dict[str, Any]:
    """Check whether hmbatch.exe can be found and started."""
    exe = _resolve_hmbatch(hmbatch_path)
    command = [str(exe), "-help"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return {
            "success": True,
            "executable": str(exe),
            "returncode": completed.returncode,
            "stdout": completed.stdout[:4000],
            "stderr": completed.stderr[:4000],
        }
    except subprocess.TimeoutExpired:
        return {
            "success": True,
            "executable": str(exe),
            "warning": (
                "Process started but did not exit within 20 seconds. "
                "This can happen when license or GUI startup blocks batch probing."
            ),
        }


@mcp.tool()
def generate_surface_automesh_tcl(
    element_size: float,
    surface_ids: list[int] | None = None,
    output_hm_path: str | None = None,
) -> dict[str, Any]:
    """Generate Tcl for a simple 2D surface automesh on existing HyperMesh surfaces."""
    if element_size <= 0:
        raise ValueError("element_size must be greater than 0.")

    if surface_ids:
        ids = " ".join(str(int(value)) for value in surface_ids)
        mark_line = f"*createmark surfaces 1 {ids}"
    else:
        mark_line = '*createmark surfaces 1 "all"'

    lines = [
        "# HyperMesh MCP generated surface automesh script",
        "# Review recorded local commands if your solver profile needs custom options.",
        'catch {*beginhistorystate "MCP surface automesh"}',
        mark_line,
        f"set elem_size {float(element_size)}",
        "*interactiveremeshsurf 1 $elem_size 2 2 2 1 1",
        "*automesh 0 2 2",
        "*storemeshtodatabase 1",
        "*ameshclearsurface",
        'catch {*endhistorystate "MCP surface automesh"}',
    ]
    if output_hm_path:
        lines.append(f'*writefile "{_quote_tcl_path(output_hm_path)}" 1')

    return {"success": True, "script": "\n".join(lines) + "\n"}


@mcp.tool()
def generate_surface_deviation_rtrias_tcl(
    element_size: float,
    surface_ids: list[int] | None = None,
    output_hm_path: str | None = None,
    min_element_size: float = 0.5,
    max_deviation: float = 0.1,
    max_feature_angle: float = 15.0,
    growth_rate: float = 1.23,
) -> dict[str, Any]:
    """Generate Tcl for HyperMesh surface-deviation R-trias meshing."""
    if element_size <= 0:
        raise ValueError("element_size must be greater than 0.")
    if min_element_size <= 0:
        raise ValueError("min_element_size must be greater than 0.")
    if max_deviation < 0:
        raise ValueError("max_deviation must be non-negative.")

    if surface_ids:
        ids = " ".join(str(int(value)) for value in surface_ids)
        mark_line = f"*createmark surfs 1 {ids}"
    else:
        mark_line = '*createmark surfs 1 "all"'

    max_element_size = max(float(element_size) * 1.8, float(min_element_size))
    lines = [
        "# HyperMesh MCP generated surface-deviation R-trias script",
        "# Strategy: surface deviation, R-trias, b.hm-style growth/deviation controls.",
        'catch {*beginhistorystate "MCP surface deviation R-trias"}',
        "*elementorder 1",
        mark_line,
        "*createarray 3 0 0 0",
        (
            "*defaultmeshsurf_growth 1 "
            f"{float(element_size)} 3 3 2 1 1 1 35 0 "
            f"{float(min_element_size)} {max_element_size} "
            f"{float(max_deviation)} {float(max_feature_angle)} "
            f"{float(growth_rate)} 1 3 1 0"
        ),
        'catch {*endhistorystate "MCP surface deviation R-trias"}',
    ]
    if output_hm_path:
        lines.append(f'*writefile "{_quote_tcl_path(output_hm_path)}" 1')

    return {
        "success": True,
        "script": "\n".join(lines) + "\n",
        "strategy": HYPERMESH_MESHING_STRATEGY.strip(),
    }


@mcp.tool()
def generate_gear_aware_tetra_tcl(
    solid_id: int,
    component_name: str,
    base_element_size: float,
    gear_surface_ids: list[int] | None = None,
    gear_element_size: float | None = None,
    gear_size_factor: float = 0.45,
    gear_axis: str = "z",
    auto_detect_gear_surfaces: bool = True,
    geometry_confirms_gear_teeth: bool = False,
    name_hint_indicates_gear: bool = False,
    gear_outer_band_fraction: float = 0.72,
    output_hm_path: str | None = None,
    min_element_size: float = 0.25,
    max_deviation: float = 0.1,
    base_feature_angle: float = 15.0,
    gear_feature_angle: float = 8.0,
    growth_rate: float = 1.23,
) -> dict[str, Any]:
    """Generate gear-aware tetra Tcl with local fine surface mesh on tooth faces."""
    if solid_id <= 0:
        raise ValueError("solid_id must be greater than 0.")
    if base_element_size <= 0:
        raise ValueError("base_element_size must be greater than 0.")
    if gear_size_factor <= 0:
        raise ValueError("gear_size_factor must be greater than 0.")
    if min_element_size <= 0:
        raise ValueError("min_element_size must be greater than 0.")
    if not 0.0 < gear_outer_band_fraction < 1.0:
        raise ValueError("gear_outer_band_fraction must be between 0 and 1.")
    if not component_name.strip():
        raise ValueError("component_name cannot be empty.")
    axis_key = gear_axis.strip().lower()
    if axis_key not in {"x", "y", "z"}:
        raise ValueError("gear_axis must be one of: x, y, z.")

    comp = component_name.replace('"', '\\"')
    auto_detect = "1" if auto_detect_gear_surfaces else "0"
    gear_size = float(gear_element_size) if gear_element_size else float(base_element_size) * float(gear_size_factor)
    gear_size = max(float(min_element_size), gear_size)
    base_max_size = max(float(base_element_size) * 1.8, float(min_element_size))
    gear_max_size = max(gear_size * 1.6, float(min_element_size))
    gear_ids = " ".join(str(int(value)) for value in (gear_surface_ids or []))
    gear_id_count = len(gear_surface_ids or [])
    lines = [
        "# HyperMesh MCP generated gear-aware tetra script",
        "# Use for gear/helical-gear/pinion/spline shafts: tooth/flank/root surfaces get local fine mesh; shaft/hub surfaces keep base size.",
        "# Pass gear_surface_ids when known. If omitted, this can auto-detect the outer gear band and refine it.",
        f'set target_component "{comp}"',
        f"set target_solid {int(solid_id)}",
        f"set base_size {float(base_element_size)}",
        f"set gear_size {gear_size}",
        f"set min_size {float(min_element_size)}",
        f"set base_max_size {base_max_size}",
        f"set gear_max_size {gear_max_size}",
        f"set max_dev {float(max_deviation)}",
        f"set base_feature_angle {float(base_feature_angle)}",
        f"set gear_feature_angle {float(gear_feature_angle)}",
        f"set growth_rate {float(growth_rate)}",
        f'set gear_axis "{axis_key}"',
        f"set auto_detect_gear_surfaces {auto_detect}",
        f"set geometry_confirms_gear_teeth {1 if geometry_confirms_gear_teeth else 0}",
        f"set name_hint_indicates_gear {1 if name_hint_indicates_gear else 0}",
        f"set gear_outer_band_fraction {float(gear_outer_band_fraction)}",
        f"set gear_surfs {{{gear_ids}}}",
        f"set gear_surface_count {gear_id_count}",
        "proc mcp_all_elems {} {",
        '    *createmark elems 1 "all"',
        "    return [hm_getmark elems 1]",
        "}",
        "proc mcp_list_subtract {a b} {",
        "    array set seen {}",
        "    foreach x $b {set seen($x) 1}",
        "    set out {}",
        "    foreach x $a {if {![info exists seen($x)]} {lappend out $x}}",
        "    return $out",
        "}",
        "proc mcp_mark_count {entity mark_id} {",
        "    if {[catch {hm_marklength $entity $mark_id} n]} {return 0}",
        "    return $n",
        "}",
        "proc mcp_ensure_component {comp} {",
        '    *createmark components 1 "by name" $comp',
        '    if {[mcp_mark_count components 1] == 0} {catch {*createentity comps name="$comp"}}',
        "    *currentcollector components $comp",
        "}",
        "proc mcp_delete_elems {elems} {",
        "    if {[llength $elems] == 0} {return}",
        "    eval *createmark elems 1 $elems",
        "    catch {*deletemark elems 1}",
        "}",
        "proc mcp_count_tetra4 {elems} {",
        "    set count 0",
        "    foreach eid $elems {",
        "        if {[catch {hm_getvalue elems id=$eid dataname=config} cfg]} {continue}",
        "        if {$cfg == 204} {incr count}",
        "    }",
        "    return $count",
        "}",
        "proc mcp_mesh_marked_surfs {size max_size feature_angle} {",
        "    if {[mcp_mark_count surfs 1] == 0} {return}",
        "    *createarray 3 0 0 0",
        "    *defaultmeshsurf_growth 1 $size 3 3 2 1 1 1 35 0 $::mcp_min_size $max_size $::mcp_max_dev $feature_angle $::mcp_growth_rate 1 3 1 0",
        "}",
        "proc mcp_radial_from_axis {axis x y z cx cy cz} {",
        '    if {$axis eq "x"} {return [expr {sqrt(($y-$cy)*($y-$cy) + ($z-$cz)*($z-$cz))}]}',
        '    if {$axis eq "y"} {return [expr {sqrt(($x-$cx)*($x-$cx) + ($z-$cz)*($z-$cz))}]}',
        "    return [expr {sqrt(($x-$cx)*($x-$cx) + ($y-$cy)*($y-$cy))}]",
        "}",
        "proc mcp_auto_gear_surfaces {surfs solid_id axis outer_fraction} {",
        "    *createmark solids 2 $solid_id",
        "    if {[catch {hm_getboundingbox solids 2 0 0 0} sbb]} {return {}}",
        "    set cx [expr {([lindex $sbb 0] + [lindex $sbb 3]) / 2.0}]",
        "    set cy [expr {([lindex $sbb 1] + [lindex $sbb 4]) / 2.0}]",
        "    set cz [expr {([lindex $sbb 2] + [lindex $sbb 5]) / 2.0}]",
        "    set corners [list \\",
        "        [list [lindex $sbb 0] [lindex $sbb 1] [lindex $sbb 2]] \\",
        "        [list [lindex $sbb 0] [lindex $sbb 1] [lindex $sbb 5]] \\",
        "        [list [lindex $sbb 0] [lindex $sbb 4] [lindex $sbb 2]] \\",
        "        [list [lindex $sbb 0] [lindex $sbb 4] [lindex $sbb 5]] \\",
        "        [list [lindex $sbb 3] [lindex $sbb 1] [lindex $sbb 2]] \\",
        "        [list [lindex $sbb 3] [lindex $sbb 1] [lindex $sbb 5]] \\",
        "        [list [lindex $sbb 3] [lindex $sbb 4] [lindex $sbb 2]] \\",
        "        [list [lindex $sbb 3] [lindex $sbb 4] [lindex $sbb 5]]]",
        "    set solid_rmax 0.0",
        "    foreach p $corners {",
        "        set r [mcp_radial_from_axis $axis [lindex $p 0] [lindex $p 1] [lindex $p 2] $cx $cy $cz]",
        "        if {$r > $solid_rmax} {set solid_rmax $r}",
        "    }",
        "    set threshold [expr {$solid_rmax * $outer_fraction}]",
        "    set out {}",
        "    foreach sid $surfs {",
        "        *createmark surfs 2 $sid",
        "        if {[catch {hm_getboundingbox surfs 2 0 0 0} bb]} {continue}",
        "        set surf_cx [expr {([lindex $bb 0] + [lindex $bb 3]) / 2.0}]",
        "        set surf_cy [expr {([lindex $bb 1] + [lindex $bb 4]) / 2.0}]",
        "        set surf_cz [expr {([lindex $bb 2] + [lindex $bb 5]) / 2.0}]",
        "        set surf_r [mcp_radial_from_axis $axis $surf_cx $surf_cy $surf_cz $cx $cy $cz]",
        "        set dx [expr {abs([lindex $bb 3] - [lindex $bb 0])}]",
        "        set dy [expr {abs([lindex $bb 4] - [lindex $bb 1])}]",
        "        set dz [expr {abs([lindex $bb 5] - [lindex $bb 2])}]",
        "        set span_r [expr {sqrt($dx*$dx + $dy*$dy + $dz*$dz) / 2.0}]",
        "        if {[expr {$surf_r + $span_r}] >= $threshold} {lappend out $sid}",
        "    }",
        "    return [lsort -integer -unique $out]",
        "}",
        'catch {*beginhistorystate "MCP gear-aware tetra"}',
        "mcp_ensure_component $target_component",
        "set ::mcp_min_size $min_size",
        "set ::mcp_max_dev $max_dev",
        "set ::mcp_growth_rate $growth_rate",
        "set before_shell_mesh [mcp_all_elems]",
        "*createmark solids 1 $target_solid",
        "if {[mcp_mark_count solids 1] == 0} {",
        '    puts "MCP gear-aware tetra skipped: solid is missing."',
        "} else {",
        "    *createmark surfs 2 \"by solids\" $target_solid",
        "    set all_surfs [hm_getmark surfs 2]",
        "    if {$gear_surface_count == 0 && $auto_detect_gear_surfaces && $geometry_confirms_gear_teeth} {",
        "        set gear_surfs [mcp_auto_gear_surfaces $all_surfs $target_solid $gear_axis $gear_outer_band_fraction]",
        "        set gear_surface_count [llength $gear_surfs]",
        '        puts "MCP gear-aware tetra auto-detected gear_surfs=$gear_surfs count=$gear_surface_count axis=$gear_axis outer_fraction=$gear_outer_band_fraction"',
        "    }",
        "    if {$gear_surface_count == 0 && $auto_detect_gear_surfaces && !$geometry_confirms_gear_teeth && $name_hint_indicates_gear} {",
        '        puts "MCP gear-aware tetra: name hint requests gear inspection, but geometry_confirms_gear_teeth is false; running cautious outer-band detection."',
        "        set gear_surfs [mcp_auto_gear_surfaces $all_surfs $target_solid $gear_axis $gear_outer_band_fraction]",
        "        set gear_surface_count [llength $gear_surfs]",
        "    }",
        "    if {$gear_surface_count == 0 && $auto_detect_gear_surfaces && !$geometry_confirms_gear_teeth && !$name_hint_indicates_gear} {",
        '        puts "MCP gear-aware tetra: auto-detect skipped because geometry_confirms_gear_teeth is false; avoiding false gear refinement on smooth bearing/ring geometry."',
        "    }",
        "    if {$gear_surface_count > 0} {",
        "        set base_surfs [mcp_list_subtract $all_surfs $gear_surfs]",
        "    } else {",
        '        puts "MCP gear-aware tetra: no gear_surface_ids supplied; using uniform base-size surface mesh."',
        "        set base_surfs $all_surfs",
        "    }",
        "    if {[llength $base_surfs] > 0} {",
        "        eval *createmark surfs 1 $base_surfs",
        "        mcp_mesh_marked_surfs $base_size $base_max_size $base_feature_angle",
        "    }",
        "    if {$gear_surface_count > 0} {",
        "        eval *createmark surfs 1 $gear_surfs",
        "        mcp_mesh_marked_surfs $gear_size $gear_max_size $gear_feature_angle",
        "    }",
        "    set shell_ids [mcp_list_subtract [mcp_all_elems] $before_shell_mesh]",
        "    if {[llength $shell_ids] == 0} {",
        '        puts "MCP gear-aware tetra failed: no surface shells created."',
        "    } else {",
        "        eval *createmark elems 1 $shell_ids",
        "        catch {*triangle_clean_up elems 1 \"aspect=10.0 height=0.2\"}",
        "        set tet_max [expr {max($base_size * 1.9, $gear_size * 2.2)}]",
        "        *createstringarray 2 \\",
        "            \"tet: 547 1.2 2 $tet_max 0.8 $min_size 0\" \\",
        "            \"pars: pre_cln=1 post_cln=1 shell_validation=1 use_optimizer=1 skip_aflr3=1 feature_angle=30 niter=30 fix_comp_bdr=1 fix_top_bdr=1 shell_swap=1 shell_remesh=1 upd_shell=1 shell_dev=0.0,0.0 vol_skew='0.99,0.95,0.90,1'\"",
        "        if {[catch {*tetmesh elements 1 1 elements 0 -1 1 2} tet_err]} {",
        '            puts "MCP gear-aware tetra volume mesh failed: $tet_err"',
        "            set failed_after_cleanup [mcp_list_subtract [mcp_all_elems] $before_shell_mesh]",
        "            mcp_delete_elems $failed_after_cleanup",
        "        } else {",
        "            set new_after_tetmesh [mcp_list_subtract [mcp_all_elems] $before_shell_mesh]",
        "            set tet_count [mcp_count_tetra4 $new_after_tetmesh]",
        "            if {$tet_count == 0} {",
        '                puts "MCP gear-aware tetra failed: tetmesh returned but no tetra elements were created."',
        "                mcp_delete_elems $new_after_tetmesh",
        "            } else {",
        "                mcp_delete_elems $shell_ids",
        '                puts "MCP gear-aware tetra completed: tetra=$tet_count base_size=$base_size gear_size=$gear_size gear_surfaces=$gear_surface_count"',
        "            }",
        "        }",
        "    }",
        "}",
        'catch {*endhistorystate "MCP gear-aware tetra"}',
    ]
    if output_hm_path:
        lines.append(f'*writefile "{_quote_tcl_path(output_hm_path)}" 1')

    return {
        "success": True,
        "script": "\n".join(lines) + "\n",
        "strategy": (
            "Use for gear-like shafts, including helical gears. Identify repeated "
            "tooth/flank/root surfaces as gear_surface_ids when possible; otherwise "
            "auto-detect the outer gear band, mesh it with gear_size, and keep "
            "shaft/hub surfaces at base_size before tetra volume meshing."
        ),
    }


@mcp.tool()
def generate_guarded_drag_hex_tcl(
    source_surface_id: int,
    drag_distance: float,
    element_size: float,
    component_name: str,
    axis: str = "z",
    solid_id: int | None = None,
    fit_tolerance_ratio: float = 0.05,
    retry_count: int = 1,
    fallback_to_tetra: bool = True,
    layer_count: int | None = None,
    matched_edge_groups: list[list[int]] | None = None,
    target_density: int | None = None,
    preview_edge_seed_counts: list[int] | None = None,
    source_edge_lengths: list[float] | None = None,
    seed_balance_ratio_threshold: float = 1.6,
    output_hm_path: str | None = None,
) -> dict[str, Any]:
    """Generate guarded drag-hex Tcl: match edge seeds, then all-quad source face or no drag."""
    if drag_distance <= 0:
        raise ValueError("drag_distance must be greater than 0.")
    if element_size <= 0:
        raise ValueError("element_size must be greater than 0.")
    if not component_name.strip():
        raise ValueError("component_name cannot be empty.")
    if target_density is not None and target_density <= 0:
        raise ValueError("target_density must be greater than 0.")
    if solid_id is not None and solid_id <= 0:
        raise ValueError("solid_id must be greater than 0 when supplied.")
    if fit_tolerance_ratio <= 0:
        raise ValueError("fit_tolerance_ratio must be greater than 0.")
    if retry_count < 0:
        raise ValueError("retry_count cannot be negative.")
    if seed_balance_ratio_threshold <= 1.0:
        raise ValueError("seed_balance_ratio_threshold must be greater than 1.0.")
    if preview_edge_seed_counts and any(int(count) <= 0 for count in preview_edge_seed_counts):
        raise ValueError("preview_edge_seed_counts must contain positive integers.")
    if source_edge_lengths and any(float(length) <= 0 for length in source_edge_lengths):
        raise ValueError("source_edge_lengths must contain positive values.")

    axis_key = axis.strip().lower()
    vectors = {
        "x": (1, 0, 0),
        "y": (0, 1, 0),
        "z": (0, 0, 1),
    }
    if axis_key not in vectors:
        raise ValueError("axis must be one of: x, y, z.")

    vx, vy, vz = vectors[axis_key]
    layers = layer_count if layer_count and layer_count > 0 else max(
        1, round(float(drag_distance) / float(element_size))
    )
    comp = component_name.replace('"', '\\"')
    fallback_enabled = "1" if fallback_to_tetra else "0"
    balanced_density, density_source = _balanced_seed_density(
        element_size=float(element_size),
        target_density=target_density,
        preview_edge_seed_counts=preview_edge_seed_counts,
        source_edge_lengths=source_edge_lengths,
        ratio_threshold=float(seed_balance_ratio_threshold),
    )
    group_lines: list[str] = []
    if matched_edge_groups:
        group_text = " ".join(
            "{" + " ".join(str(int(edge)) for edge in group) + "}"
            for group in matched_edge_groups
        )
        group_lines.extend(
            [
                f"set matched_edge_groups {{{group_text}}}",
                f"set target_density {int(balanced_density) if balanced_density else 0}",
                f'set target_density_source "{density_source}"',
                f"set seed_balance_ratio_threshold {float(seed_balance_ratio_threshold)}",
                "if {$target_density <= 0} {",
                "    *createmark surfaces 2 $source_surface",
                "    set bb [hm_getboundingbox surfaces 2 0 0 0]",
                "    set dx [expr {abs([lindex $bb 3] - [lindex $bb 0])}]",
                "    set dy [expr {abs([lindex $bb 4] - [lindex $bb 1])}]",
                "    set dz [expr {abs([lindex $bb 5] - [lindex $bb 2])}]",
                "    set dims [lsort -real [list $dx $dy $dz]]",
                "    set major [lindex $dims 2]",
                "    set target_density [expr {int(round($major / $elem_size))}]",
                "    if {$target_density < 4} { set target_density 4 }",
                "    if {$target_density > 120} { set target_density 120 }",
                '    set target_density_source "bbox_estimate"',
                "}",
                'puts "MCP guarded drag source-face target_density=$target_density source=$target_density_source balance_ratio_threshold=$seed_balance_ratio_threshold"',
                "foreach edge_group $matched_edge_groups {",
                "    foreach edge_index $edge_group {",
                "        # edge_index is 0-based in the order shown by HyperMesh automesh.",
                "        catch {*set_meshedgeparams $edge_index $target_density 1 0 0 0 $elem_size 0 0}",
                "    }",
                "}",
            ]
        )
    else:
        group_lines.extend(
            [
                "# No explicit matched_edge_groups were supplied.",
                "# *setedgedensitylink 1 is still enabled, but exact source-face uniform",
                "# seeding requires passing all logical edge indices and target_density.",
            ]
        )
    lines = [
        "# HyperMesh MCP generated guarded drag-hex script",
        "# Precondition: all logical edge groups of the drag source face must share",
        "# one compatible target_density, but it should be balanced when inner/outer",
        "# preview counts or edge lengths differ greatly; do not blindly use the largest",
        "# outer-edge count for the whole section.",
        "# If the source face is not mapped 100% quads after uniform seeding, skip drag.",
        f'set drag_component "{comp}"',
        f"set source_surface {int(source_surface_id)}",
        f"set target_solid {int(solid_id) if solid_id is not None else 0}",
        f"set elem_size {float(element_size)}",
        f"set drag_distance {float(drag_distance)}",
        f"set drag_layers {int(layers)}",
        f"set fit_tol_ratio {float(fit_tolerance_ratio)}",
        f"set retry_count {int(retry_count)}",
        f"set fallback_to_tetra {fallback_enabled}",
        "proc mcp_all_elems {} {",
        '    *createmark elems 1 "all"',
        "    return [hm_getmark elems 1]",
        "}",
        "proc mcp_list_subtract {a b} {",
        "    array set seen {}",
        "    foreach x $b {set seen($x) 1}",
        "    set out {}",
        "    foreach x $a {if {![info exists seen($x)]} {lappend out $x}}",
        "    return $out",
        "}",
        "proc mcp_count_hex8 {elems} {",
        "    set hexes 0",
        "    foreach eid $elems {",
        "        if {[catch {hm_getvalue elems id=$eid dataname=config} cfg]} {continue}",
        "        if {$cfg == 208} {incr hexes}",
        "    }",
        "    return $hexes",
        "}",
        "proc mcp_bbox_fit_ok {elems solid_id fit_ratio elem_size} {",
        "    if {$solid_id <= 0} {return 1}",
        "    if {[llength $elems] == 0} {return 0}",
        "    eval *createmark elems 2 $elems",
        "    *createmark solids 2 $solid_id",
        "    if {[catch {hm_getboundingbox elems 2 0 0 0} ebb]} {return 0}",
        "    if {[catch {hm_getboundingbox solids 2 0 0 0} sbb]} {return 0}",
        "    set sx [expr {abs([lindex $sbb 3] - [lindex $sbb 0])}]",
        "    set sy [expr {abs([lindex $sbb 4] - [lindex $sbb 1])}]",
        "    set sz [expr {abs([lindex $sbb 5] - [lindex $sbb 2])}]",
        "    set diag [expr {sqrt($sx*$sx + $sy*$sy + $sz*$sz)}]",
        "    set tol [expr {max($elem_size * 1.5, $diag * $fit_ratio)}]",
        "    for {set i 0} {$i < 6} {incr i} {",
        "        if {abs([lindex $ebb $i] - [lindex $sbb $i]) > $tol} {return 0}",
        "    }",
        "    return 1",
        "}",
        "proc mcp_tetra_fallback {solid_id comp elem_size} {",
        "    if {$solid_id <= 0} {return 0}",
        '    puts "MCP fallback tetra started for solid=$solid_id comp=$comp"',
        "    *currentcollector components $comp",
        "    *createmark solids 2 $solid_id",
        "    *createmark surfs 1 \"by solids\" $solid_id",
        "    if {[catch {hm_marklength surfs 1} sc] || $sc == 0} {return 0}",
        "    set before_shell_mesh [mcp_all_elems]",
        "    set max_size [expr {max($elem_size * 1.8, 0.75)}]",
        "    set min_size 0.50",
        "    if {$elem_size < 0.55} {set min_size [expr {$elem_size * 0.60}]}",
        "    *createarray 3 0 0 0",
        "    if {[catch {*defaultmeshsurf_growth 1 $elem_size 3 3 2 1 1 1 35 0 $min_size $max_size 0.1 15 1.23 1 3 1 0} surf_err]} {return 0}",
        "    set shell_ids [mcp_list_subtract [mcp_all_elems] $before_shell_mesh]",
        "    if {[llength $shell_ids] == 0} {return 0}",
        "    eval *createmark elems 1 $shell_ids",
        "    set tet_max [expr {max($elem_size * 1.9, 0.85)}]",
        "    set tet_min 0.50",
        "    if {$elem_size < 0.55} {set tet_min [expr {$elem_size * 0.60}]}",
        "    *createstringarray 2 \\",
        "        \"tet: 547 1.2 2 $tet_max 0.8 $tet_min 0\" \\",
        "        \"pars: pre_cln=1 post_cln=1 shell_validation=1 use_optimizer=1 skip_aflr3=1 feature_angle=30 niter=30 fix_comp_bdr=1 fix_top_bdr=1 shell_swap=1 shell_remesh=1 upd_shell=1 shell_dev=0.0,0.0 vol_skew='0.99,0.95,0.90,1'\"",
        "    if {[catch {*tetmesh elements 1 1 elements 0 -1 1 2} tet_err]} {",
        "        eval *createmark elems 1 $shell_ids",
        "        catch {*deletemark elems 1}",
        "        return 0",
        "    }",
        "    eval *createmark elems 1 $shell_ids",
        "    catch {*deletemark elems 1}",
        '    puts "MCP fallback tetra completed."',
        "    return 1",
        "}",
        'catch {*beginhistorystate "MCP guarded drag hex"}',
        '*currentcollector components "$drag_component"',
        "catch {*setedgedensitylinkwithaspectratio -1}",
        "*setedgedensitylink 1",
        "*createmark surfaces 1 $source_surface",
        "*interactiveremeshsurf 1 $elem_size 1 1 2 1 1",
        "*set_meshfaceparams 0 5 1 0 0 1 0.5 1 1",
        *group_lines,
        "*automesh 0 5 1",
        "*storemeshtodatabase 1",
        "*ameshclearsurface",
        '*createmark elems 1 "by surface" $source_surface',
        "set source_shells [hm_getmark elems 1]",
        "set quad_count 0",
        "foreach eid $source_shells {",
        "    set cfg [hm_getvalue elems id=$eid dataname=config]",
        "    if {$cfg == 104 || $cfg == 108} { incr quad_count }",
        "}",
        "if {[llength $source_shells] == 0 || $quad_count != [llength $source_shells]} {",
        '    puts "MCP guarded drag skipped: source face is not all quads."',
        "    if {[llength $source_shells] > 0} { eval *createmark elems 1 $source_shells; catch {*deletemark elems 1} }",
        "    if {$fallback_to_tetra} {mcp_tetra_fallback $target_solid $drag_component $elem_size}",
        "} else {",
        "    set hex_success 0",
        "    set attempt 0",
        "    while {$attempt <= $retry_count && !$hex_success} {",
        '        puts "MCP guarded drag attempt=$attempt elem_size=$elem_size"',
        "        set before_elems [mcp_all_elems]",
        f"        *createvector 1 {vx} {vy} {vz}",
        "        *meshdragelements2 1 1 $drag_distance $drag_layers 0 0.0 0",
        "        set new_elems [mcp_list_subtract [mcp_all_elems] $before_elems]",
        "        set hex_count [mcp_count_hex8 $new_elems]",
        "        set fit_ok [mcp_bbox_fit_ok $new_elems $target_solid $fit_tol_ratio $elem_size]",
        "        if {[llength $new_elems] > 0 && $hex_count == [llength $new_elems] && $fit_ok} {",
        "            set hex_success 1",
        '            puts "MCP guarded drag completed: hex8=$hex_count fit_ok=$fit_ok"',
        "        } else {",
        '            puts "MCP guarded drag invalid: new_elements=[llength $new_elems] hex8=$hex_count fit_ok=$fit_ok; cleaning and retrying/falling back."',
        "            if {[llength $new_elems] > 0} { eval *createmark elems 1 $new_elems; catch {*deletemark elems 1} }",
        "        }",
        "        incr attempt",
        "    }",
        "    eval *createmark elems 1 $source_shells",
        "    catch {*deletemark elems 1}",
        "    if {!$hex_success && $fallback_to_tetra} {mcp_tetra_fallback $target_solid $drag_component $elem_size}",
        "}",
        'catch {*endhistorystate "MCP guarded drag hex"}',
    ]
    if output_hm_path:
        lines.append(f'*writefile "{_quote_tcl_path(output_hm_path)}" 1')

    return {
        "success": True,
        "script": "\n".join(lines) + "\n",
        "strategy": (
            "Use this only for simple straight tubes/extrusions. Before drag, "
            "force corresponding edge groups to a compatible target_density. "
            "When preview counts or edge lengths are highly different, this "
            "uses a balanced common count instead of promoting every edge to "
            "the largest outer count. Never use it for flanges with bolt holes."
        ),
    }


@mcp.tool()
def generate_guarded_spin_hex_tcl(
    source_surface_id: int,
    element_size: float,
    component_name: str,
    axis: str = "z",
    solid_id: int | None = None,
    fit_tolerance_ratio: float = 0.05,
    retry_count: int = 1,
    fallback_to_tetra: bool = True,
    angle_degrees: float = 360.0,
    density: int = 96,
    output_hm_path: str | None = None,
) -> dict[str, Any]:
    """Generate guarded spin-hex Tcl: all-quad section or no spin."""
    if element_size <= 0:
        raise ValueError("element_size must be greater than 0.")
    if density <= 0:
        raise ValueError("density must be greater than 0.")
    if not component_name.strip():
        raise ValueError("component_name cannot be empty.")
    if solid_id is not None and solid_id <= 0:
        raise ValueError("solid_id must be greater than 0 when supplied.")
    if fit_tolerance_ratio <= 0:
        raise ValueError("fit_tolerance_ratio must be greater than 0.")
    if retry_count < 0:
        raise ValueError("retry_count cannot be negative.")

    axis_key = axis.strip().lower()
    normals = {
        "x": (1, 0, 0),
        "y": (0, 1, 0),
        "z": (0, 0, 1),
    }
    if axis_key not in normals:
        raise ValueError("axis must be one of: x, y, z.")

    nx, ny, nz = normals[axis_key]
    comp = component_name.replace('"', '\\"')
    fallback_enabled = "1" if fallback_to_tetra else "0"
    lines = [
        "# HyperMesh MCP generated guarded spin-hex script",
        "# Use for clean revolved bodies. Do not use for flanges with bolt holes or protrusions.",
        "# Precondition: the selected source section should have matched edge seeds and be all quads.",
        f'set spin_component "{comp}"',
        f"set source_surface {int(source_surface_id)}",
        f"set target_solid {int(solid_id) if solid_id is not None else 0}",
        f"set elem_size {float(element_size)}",
        f"set spin_angle {float(angle_degrees)}",
        f"set spin_density {int(density)}",
        f"set fit_tol_ratio {float(fit_tolerance_ratio)}",
        f"set retry_count {int(retry_count)}",
        f"set fallback_to_tetra {fallback_enabled}",
        "proc mcp_all_elems {} {",
        '    *createmark elems 1 "all"',
        "    return [hm_getmark elems 1]",
        "}",
        "proc mcp_list_subtract {a b} {",
        "    array set seen {}",
        "    foreach x $b {set seen($x) 1}",
        "    set out {}",
        "    foreach x $a {if {![info exists seen($x)]} {lappend out $x}}",
        "    return $out",
        "}",
        "proc mcp_count_hex8 {elems} {",
        "    set hexes 0",
        "    foreach eid $elems {",
        "        if {[catch {hm_getvalue elems id=$eid dataname=config} cfg]} {continue}",
        "        if {$cfg == 208} {incr hexes}",
        "    }",
        "    return $hexes",
        "}",
        "proc mcp_bbox_fit_ok {elems solid_id fit_ratio elem_size} {",
        "    if {$solid_id <= 0} {return 1}",
        "    if {[llength $elems] == 0} {return 0}",
        "    eval *createmark elems 2 $elems",
        "    *createmark solids 2 $solid_id",
        "    if {[catch {hm_getboundingbox elems 2 0 0 0} ebb]} {return 0}",
        "    if {[catch {hm_getboundingbox solids 2 0 0 0} sbb]} {return 0}",
        "    set sx [expr {abs([lindex $sbb 3] - [lindex $sbb 0])}]",
        "    set sy [expr {abs([lindex $sbb 4] - [lindex $sbb 1])}]",
        "    set sz [expr {abs([lindex $sbb 5] - [lindex $sbb 2])}]",
        "    set diag [expr {sqrt($sx*$sx + $sy*$sy + $sz*$sz)}]",
        "    set tol [expr {max($elem_size * 1.5, $diag * $fit_ratio)}]",
        "    for {set i 0} {$i < 6} {incr i} {",
        "        if {abs([lindex $ebb $i] - [lindex $sbb $i]) > $tol} {return 0}",
        "    }",
        "    return 1",
        "}",
        "proc mcp_tetra_fallback {solid_id comp elem_size} {",
        "    if {$solid_id <= 0} {return 0}",
        '    puts "MCP fallback tetra started for solid=$solid_id comp=$comp"',
        "    *currentcollector components $comp",
        "    *createmark solids 2 $solid_id",
        "    *createmark surfs 1 \"by solids\" $solid_id",
        "    if {[catch {hm_marklength surfs 1} sc] || $sc == 0} {return 0}",
        "    set before_shell_mesh [mcp_all_elems]",
        "    set max_size [expr {max($elem_size * 1.8, 0.75)}]",
        "    set min_size 0.50",
        "    if {$elem_size < 0.55} {set min_size [expr {$elem_size * 0.60}]}",
        "    *createarray 3 0 0 0",
        "    if {[catch {*defaultmeshsurf_growth 1 $elem_size 3 3 2 1 1 1 35 0 $min_size $max_size 0.1 15 1.23 1 3 1 0} surf_err]} {return 0}",
        "    set shell_ids [mcp_list_subtract [mcp_all_elems] $before_shell_mesh]",
        "    if {[llength $shell_ids] == 0} {return 0}",
        "    eval *createmark elems 1 $shell_ids",
        "    set tet_max [expr {max($elem_size * 1.9, 0.85)}]",
        "    set tet_min 0.50",
        "    if {$elem_size < 0.55} {set tet_min [expr {$elem_size * 0.60}]}",
        "    *createstringarray 2 \\",
        "        \"tet: 547 1.2 2 $tet_max 0.8 $tet_min 0\" \\",
        "        \"pars: pre_cln=1 post_cln=1 shell_validation=1 use_optimizer=1 skip_aflr3=1 feature_angle=30 niter=30 fix_comp_bdr=1 fix_top_bdr=1 shell_swap=1 shell_remesh=1 upd_shell=1 shell_dev=0.0,0.0 vol_skew='0.99,0.95,0.90,1'\"",
        "    if {[catch {*tetmesh elements 1 1 elements 0 -1 1 2} tet_err]} {",
        "        eval *createmark elems 1 $shell_ids",
        "        catch {*deletemark elems 1}",
        "        return 0",
        "    }",
        "    eval *createmark elems 1 $shell_ids",
        "    catch {*deletemark elems 1}",
        '    puts "MCP fallback tetra completed."',
        "    return 1",
        "}",
        'catch {*beginhistorystate "MCP guarded spin hex"}',
        '*currentcollector components "$spin_component"',
        "*createmark surfaces 1 $source_surface",
        "*createmark surfaces 2 $source_surface",
        "set bb [hm_getboundingbox surfaces 2 0 0 0]",
        "set cx [expr {([lindex $bb 0] + [lindex $bb 3]) / 2.0}]",
        "set cy [expr {([lindex $bb 1] + [lindex $bb 4]) / 2.0}]",
        "set cz [expr {([lindex $bb 2] + [lindex $bb 5]) / 2.0}]",
        "*interactiveremeshsurf 1 $elem_size 4 4 2 1 1",
        "*set_meshfaceparams 0 4 1 0 0 1 0.5 1 1",
        "*automesh 0 4 1",
        "*storemeshtodatabase 1",
        "*ameshclearsurface",
        '*createmark elems 1 "by surface" $source_surface',
        "set source_shells [hm_getmark elems 1]",
        "set quad_count 0",
        "foreach eid $source_shells {",
        "    set cfg [hm_getvalue elems id=$eid dataname=config]",
        "    if {$cfg == 104 || $cfg == 108} { incr quad_count }",
        "}",
        "if {[llength $source_shells] == 0 || $quad_count != [llength $source_shells]} {",
        '    puts "MCP guarded spin skipped: source section is not all quads."',
        "    if {[llength $source_shells] > 0} { eval *createmark elems 1 $source_shells; catch {*deletemark elems 1} }",
        "    if {$fallback_to_tetra} {mcp_tetra_fallback $target_solid $spin_component $elem_size}",
        "} else {",
        "    set hex_success 0",
        "    set attempt 0",
        "    while {$attempt <= $retry_count && !$hex_success} {",
        '        puts "MCP guarded spin attempt=$attempt elem_size=$elem_size"',
        "        set before_elems [mcp_all_elems]",
        f"        *createplane 1 {nx} {ny} {nz} $cx $cy $cz",
        "        *meshspinelements2 1 1 $spin_angle $spin_density 1 0.0 0",
        "        set new_elems [mcp_list_subtract [mcp_all_elems] $before_elems]",
        "        set hex_count [mcp_count_hex8 $new_elems]",
        "        set fit_ok [mcp_bbox_fit_ok $new_elems $target_solid $fit_tol_ratio $elem_size]",
        "        if {[llength $new_elems] > 0 && $hex_count == [llength $new_elems] && $fit_ok} {",
        "            set hex_success 1",
        '            puts "MCP guarded spin completed: hex8=$hex_count fit_ok=$fit_ok"',
        "        } else {",
        '            puts "MCP guarded spin invalid: new_elements=[llength $new_elems] hex8=$hex_count fit_ok=$fit_ok; cleaning and retrying/falling back."',
        "            if {[llength $new_elems] > 0} { eval *createmark elems 1 $new_elems; catch {*deletemark elems 1} }",
        "        }",
        "        incr attempt",
        "    }",
        "    eval *createmark elems 1 $source_shells",
        "    catch {*deletemark elems 1}",
        "    if {!$hex_success && $fallback_to_tetra} {mcp_tetra_fallback $target_solid $spin_component $elem_size}",
        "}",
        'catch {*endhistorystate "MCP guarded spin hex"}',
    ]
    if output_hm_path:
        lines.append(f'*writefile "{_quote_tcl_path(output_hm_path)}" 1')

    return {
        "success": True,
        "script": "\n".join(lines) + "\n",
        "strategy": (
            "Use spin for clean revolved bodies when the source section can be "
            "meshed as 100% quads. Fall back to tetra for flanges, protrusions, "
            "or any non-quad section. If the selected surface is not a true "
            "cross-section of the solid, use generate_cutsection_spin_hex_tcl."
        ),
    }


@mcp.tool()
def generate_cutsection_spin_hex_tcl(
    solid_id: int,
    component_name: str,
    split_plane_normal: list[float],
    split_plane_point: list[float],
    spin_axis: str = "x",
    spin_axis_point: list[float] | None = None,
    element_size: float = 0.7,
    density: int = 96,
    plane_tolerance: float = 0.02,
    fit_tolerance_ratio: float = 0.05,
    retry_count: int = 1,
    include_existing_section_surfaces: bool = True,
    allow_quad_only_fallback: bool = True,
    delete_existing_component_elements: bool = True,
    fallback_to_tetra: bool = True,
    output_hm_path: str | None = None,
) -> dict[str, Any]:
    """Generate generic real cut-section spin-hex Tcl for a stepped/recessed revolved solid."""
    if solid_id <= 0:
        raise ValueError("solid_id must be greater than 0.")
    if element_size <= 0:
        raise ValueError("element_size must be greater than 0.")
    if density <= 0:
        raise ValueError("density must be greater than 0.")
    if plane_tolerance <= 0:
        raise ValueError("plane_tolerance must be greater than 0.")
    if fit_tolerance_ratio <= 0:
        raise ValueError("fit_tolerance_ratio must be greater than 0.")
    if retry_count < 0:
        raise ValueError("retry_count cannot be negative.")
    if not component_name.strip():
        raise ValueError("component_name cannot be empty.")
    if len(split_plane_normal) != 3 or len(split_plane_point) != 3:
        raise ValueError("split_plane_normal and split_plane_point must contain 3 numbers.")

    axis_key = spin_axis.strip().lower()
    axis_normals = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
    if axis_key not in axis_normals:
        raise ValueError("spin_axis must be one of: x, y, z.")

    nx, ny, nz = [float(v) for v in split_plane_normal]
    px, py, pz = [float(v) for v in split_plane_point]
    normal_length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if normal_length <= 0:
        raise ValueError("split_plane_normal cannot be the zero vector.")
    if spin_axis_point is None:
        raise ValueError(
            "spin_axis_point is required and must be a point on the real rotation axis. "
            "Do not reuse split_plane_point unless it is also on that axis."
        )
    if len(spin_axis_point) != 3:
        raise ValueError("spin_axis_point must contain 3 numbers.")
    ax, ay, az = [float(v) for v in spin_axis_point]
    snx, sny, snz = axis_normals[axis_key]
    axis_dot = abs(nx * snx + ny * sny + nz * snz) / normal_length
    if axis_dot > 0.05:
        raise ValueError(
            "For cut-section spin, the split plane must contain the spin axis, "
            "so split_plane_normal must be nearly perpendicular to spin_axis. "
            "If the cut plane is perpendicular to the axis, use drag for a "
            "constant-section body or tetra fallback for complex topology."
        )
    comp = component_name.replace('"', '\\"')

    delete_existing = "1" if delete_existing_component_elements else "0"
    fallback_enabled = "1" if fallback_to_tetra else "0"
    include_existing = "1" if include_existing_section_surfaces else "0"
    quad_fallback = "1" if allow_quad_only_fallback else "0"
    lines = [
        "# HyperMesh MCP generated cut-section spin-hex script",
        "# Use for stepped/recessed revolved solids where an existing face is not a reliable section.",
        "# The spin axis point must lie on the true rotation axis; the split-plane point alone is not enough.",
        "# If cut-section spin does not create valid 3D hex elements, this script falls back to tetra.",
        f'set target_component "{comp}"',
        f"set target_solid {int(solid_id)}",
        f"set elem_size {float(element_size)}",
        f"set spin_density {int(density)}",
        f"set plane_tol {float(plane_tolerance)}",
        f"set fit_tol_ratio {float(fit_tolerance_ratio)}",
        f"set retry_count {int(retry_count)}",
        f"set include_existing_section_surfaces {include_existing}",
        f"set allow_quad_only_fallback {quad_fallback}",
        f"set delete_existing_component_elements {delete_existing}",
        f"set fallback_to_tetra {fallback_enabled}",
        f"set split_nx {nx}",
        f"set split_ny {ny}",
        f"set split_nz {nz}",
        f"set split_px {px}",
        f"set split_py {py}",
        f"set split_pz {pz}",
        f"set axis_px {ax}",
        f"set axis_py {ay}",
        f"set axis_pz {az}",
        "proc mcp_mark_count {entity mark_id} {",
        "    if {[catch {hm_marklength $entity $mark_id} n]} {return 0}",
        "    return $n",
        "}",
        "proc mcp_all_surfs {} {",
        '    *createmark surfs 1 "all"',
        "    return [hm_getmark surfs 1]",
        "}",
        "proc mcp_all_elems {} {",
        '    *createmark elems 1 "all"',
        "    return [hm_getmark elems 1]",
        "}",
        "proc mcp_list_subtract {a b} {",
        "    array set seen {}",
        "    foreach x $b {set seen($x) 1}",
        "    set out {}",
        "    foreach x $a {if {![info exists seen($x)]} {lappend out $x}}",
        "    return $out",
        "}",
        "proc mcp_unique_append {base additions} {",
        "    array set seen {}",
        "    set out {}",
        "    foreach x $base {",
        "        if {![info exists seen($x)]} {set seen($x) 1; lappend out $x}",
        "    }",
        "    foreach x $additions {",
        "        if {![info exists seen($x)]} {set seen($x) 1; lappend out $x}",
        "    }",
        "    return $out",
        "}",
        "proc mcp_delete_elems {elems} {",
        "    if {[llength $elems] == 0} {return}",
        "    eval *createmark elems 1 $elems",
        "    catch {*deletemark elems 1}",
        "}",
        "proc mcp_hex8_count {elems} {",
        "    set hexes 0",
        "    foreach eid $elems {",
        "        if {[catch {hm_getvalue elems id=$eid dataname=config} cfg]} {continue}",
        "        if {$cfg == 208} {incr hexes}",
        "    }",
        "    return $hexes",
        "}",
        "proc mcp_bbox_fit_ok {elems solid_id fit_ratio elem_size} {",
        "    if {[llength $elems] == 0} {return 0}",
        "    eval *createmark elems 2 $elems",
        "    *createmark solids 2 $solid_id",
        "    if {[mcp_mark_count elems 2] == 0 || [mcp_mark_count solids 2] == 0} {return 0}",
        "    if {[catch {hm_getboundingbox elems 2 0 0 0} ebb]} {return 0}",
        "    if {[catch {hm_getboundingbox solids 2 0 0 0} sbb]} {return 0}",
        "    set sx [expr {abs([lindex $sbb 3] - [lindex $sbb 0])}]",
        "    set sy [expr {abs([lindex $sbb 4] - [lindex $sbb 1])}]",
        "    set sz [expr {abs([lindex $sbb 5] - [lindex $sbb 2])}]",
        "    set diag [expr {sqrt($sx*$sx + $sy*$sy + $sz*$sz)}]",
        "    set tol [expr {max($elem_size * 1.5, $diag * $fit_ratio)}]",
        "    for {set i 0} {$i < 6} {incr i} {",
        "        set diff [expr {abs([lindex $ebb $i] - [lindex $sbb $i])}]",
        "        if {$diff > $tol} {",
        '            puts "MCP mesh-solid fit failed: bbox_index=$i mesh=[lindex $ebb $i] solid=[lindex $sbb $i] diff=$diff tol=$tol"',
        "            return 0",
        "        }",
        "    }",
        "    return 1",
        "}",
        "proc mcp_tetra_fallback {solid_id comp elem_size} {",
        '    puts "MCP fallback tetra started for solid=$solid_id comp=$comp"',
        "    *currentcollector components $comp",
        "    *createmark solids 2 $solid_id",
        "    if {[mcp_mark_count solids 2] == 0} {",
        '        puts "MCP fallback tetra failed: solid is missing."',
        "        return 0",
        "    }",
        "    *createmark surfs 1 \"by solids\" $solid_id",
        "    if {[mcp_mark_count surfs 1] == 0} {",
        '        puts "MCP fallback tetra failed: no surfaces found for solid."',
        "        return 0",
        "    }",
        "    set before_shell_mesh [mcp_all_elems]",
        "    set max_size [expr {max($elem_size * 1.8, 0.75)}]",
        "    set min_size 0.50",
        "    if {$elem_size < 0.55} {set min_size [expr {$elem_size * 0.60}]}",
        "    *createarray 3 0 0 0",
        "    if {[catch {*defaultmeshsurf_growth 1 $elem_size 3 3 2 1 1 1 35 0 $min_size $max_size 0.1 15 1.23 1 3 1 0} surf_err]} {",
        '        puts "MCP fallback tetra surface mesh failed: $surf_err"',
        "        return 0",
        "    }",
        "    set shell_ids [mcp_list_subtract [mcp_all_elems] $before_shell_mesh]",
        "    if {[llength $shell_ids] == 0} {",
        '        puts "MCP fallback tetra failed: no surface shells created."',
        "        return 0",
        "    }",
        "    eval *createmark elems 1 $shell_ids",
        "    catch {*triangle_clean_up elems 1 \"aspect=10.0 height=0.2\"}",
        "    set tet_max [expr {max($elem_size * 1.9, 0.85)}]",
        "    set tet_min 0.50",
        "    if {$elem_size < 0.55} {set tet_min [expr {$elem_size * 0.60}]}",
        "    *createstringarray 2 \\",
        "        \"tet: 547 1.2 2 $tet_max 0.8 $tet_min 0\" \\",
        "        \"pars: pre_cln=1 post_cln=1 shell_validation=1 use_optimizer=1 skip_aflr3=1 feature_angle=30 niter=30 fix_comp_bdr=1 fix_top_bdr=1 shell_swap=1 shell_remesh=1 upd_shell=1 shell_dev=0.0,0.0 vol_skew='0.99,0.95,0.90,1'\"",
        "    if {[catch {*tetmesh elements 1 1 elements 0 -1 1 2} tet_err]} {",
        '        puts "MCP fallback tetra volume mesh failed: $tet_err"',
        "        mcp_delete_elems $shell_ids",
        "        return 0",
        "    }",
        "    mcp_delete_elems $shell_ids",
        '    puts "MCP fallback tetra completed."',
        "    return 1",
        "}",
        "proc mcp_node_plane_dist {nid nx ny nz px py pz} {",
        "    set x [hm_getvalue nodes id=$nid dataname=x]",
        "    set y [hm_getvalue nodes id=$nid dataname=y]",
        "    set z [hm_getvalue nodes id=$nid dataname=z]",
        "    set d [expr {$nx * ($x - $px) + $ny * ($y - $py) + $nz * ($z - $pz)}]",
        "    if {$d < 0} {set d [expr {-$d}]}",
        "    return $d",
        "}",
        "proc mcp_mesh_true_section {sid elem_size nx ny nz px py pz plane_tol} {",
        "    set mesh_modes {{1 5}}",
        "    if {$::mcp_allow_quad_only_fallback} {lappend mesh_modes {4 4}}",
        "    foreach mode_pair $mesh_modes {",
        "        set interactive_mode [lindex $mode_pair 0]",
        "        set face_mode [lindex $mode_pair 1]",
        "        *createmark surfaces 1 $sid",
        "        catch {*setedgedensitylinkwithaspectratio -1}",
        "        *setedgedensitylink 1",
        "        *interactiveremeshsurf 1 $elem_size $interactive_mode $face_mode 2 1 1",
        "        *set_meshfaceparams 0 $face_mode 1 0 0 1 0.5 1 1",
        "        *automesh 0 $face_mode 1",
        "        *storemeshtodatabase 1",
        "        *ameshclearsurface",
        '        *createmark elems 1 "by surface" $sid',
        "        set shells [hm_getmark elems 1]",
        "        if {[llength $shells] == 0} {continue}",
        "        set quads 0",
        "        set maxdist 0.0",
        "        foreach eid $shells {",
        "            set cfg [hm_getvalue elems id=$eid dataname=config]",
        "            if {$cfg == 104 || $cfg == 108} {incr quads}",
        "            foreach nid [hm_getvalue elems id=$eid dataname=nodes] {",
        "                set d [mcp_node_plane_dist $nid $nx $ny $nz $px $py $pz]",
        "                if {$d > $maxdist} {set maxdist $d}",
        "            }",
        "        }",
        "        if {$quads == [llength $shells] && $maxdist <= $plane_tol} {",
        '            puts "MCP accepted true section surface=$sid mesh_mode=$face_mode shells=[llength $shells] maxdist=$maxdist plane_tol=$plane_tol"',
        "            return $shells",
        "        }",
        "        mcp_delete_elems $shells",
        "    }",
        "    return {}",
        "}",
        'catch {*beginhistorystate "MCP cut-section spin hex"}',
        '*currentcollector components "$target_component"',
        "set ::mcp_allow_quad_only_fallback $allow_quad_only_fallback",
        "set hex_success 0",
        "if {$delete_existing_component_elements} {",
        '    *createmark elems 1 "by comp name" $target_component',
        "    if {[mcp_mark_count elems 1] > 0} {catch {*deletemark elems 1}}",
        "}",
        "set before_surfs [mcp_all_surfs]",
        "*createmark solids 1 $target_solid",
        "if {[mcp_mark_count solids 1] == 0} {",
        '    puts "MCP cut-section spin skipped: solid is missing."',
        "} else {",
        "    *createplane 1 $split_nx $split_ny $split_nz $split_px $split_py $split_pz",
        "    if {[catch {*body_splitmerge_with_plane solids 1 1} split_err]} {",
        '        puts "MCP cut-section split failed: $split_err"',
        "    } else {",
        "        set new_surfs [lsort -integer [mcp_list_subtract [mcp_all_surfs] $before_surfs]]",
        '        puts "MCP cut-section new_surfs=$new_surfs"',
        "        set candidate_surfs $new_surfs",
        "        if {$include_existing_section_surfaces} {",
        "            *createmark surfs 2 \"by solids\" $target_solid",
        "            set solid_surfs [hm_getmark surfs 2]",
        "            set candidate_surfs [mcp_unique_append $candidate_surfs $solid_surfs]",
        "        }",
        '        puts "MCP cut-section candidate_surfs=$candidate_surfs"',
        "        set attempt 0",
        "        while {$attempt <= $retry_count && !$hex_success} {",
        "            set attempt_size $elem_size",
        "            set effective_plane_tol [expr {max($plane_tol, $attempt_size * 0.05)}]",
        '            puts "MCP cut-section spin attempt=$attempt elem_size=$attempt_size"',
        "            set seed_shells {}",
        "            foreach sid $candidate_surfs {",
        "                set shells [mcp_mesh_true_section $sid $attempt_size $split_nx $split_ny $split_nz $split_px $split_py $split_pz $effective_plane_tol]",
        "                foreach e $shells {lappend seed_shells $e}",
        "            }",
        "            if {[llength $seed_shells] == 0} {",
        '                puts "MCP cut-section spin attempt failed: no true all-quad section surfaces were found."',
        "                incr attempt",
        "                continue",
        "            }",
        "            set before_elems [mcp_all_elems]",
        "            eval *createmark elems 1 $seed_shells",
        f"            *createplane 1 {snx} {sny} {snz} $axis_px $axis_py $axis_pz",
        "            if {[catch {*meshspinelements2 1 1 360 $spin_density 1 0.0 0} spin_err]} {",
        '                puts "MCP cut-section spin attempt failed: $spin_err"',
        "            } else {",
        "                set new_elems [mcp_list_subtract [mcp_all_elems] $before_elems]",
        "                set hex_count [mcp_hex8_count $new_elems]",
        "                set fit_ok [mcp_bbox_fit_ok $new_elems $target_solid $fit_tol_ratio $attempt_size]",
        "                if {[llength $new_elems] > 0 && $hex_count == [llength $new_elems] && $fit_ok} {",
        "                    eval *createmark elems 1 $new_elems",
        "                    catch {*movemark elems 1 $target_component}",
        "                    set hex_success 1",
        '                    puts "MCP cut-section spin completed: hex8=$hex_count fit_ok=$fit_ok"',
        "                } else {",
        '                    puts "MCP cut-section spin invalid: new_elements=[llength $new_elems] hex8=$hex_count fit_ok=$fit_ok; cleaning and retrying/falling back."',
        "                    mcp_delete_elems $new_elems",
        "                }",
        "            }",
        "            mcp_delete_elems $seed_shells",
        "            incr attempt",
        "        }",
        "    }",
        "}",
        "if {!$hex_success && $fallback_to_tetra} {",
        "    mcp_tetra_fallback $target_solid $target_component $elem_size",
        "}",
        'catch {*endhistorystate "MCP cut-section spin hex"}',
    ]
    if output_hm_path:
        lines.append(f'*writefile "{_quote_tcl_path(output_hm_path)}" 1')

    return {
        "success": True,
        "script": "\n".join(lines) + "\n",
        "strategy": (
            "Generic cut-section spin: split the actual solid first, accept only "
            "new all-quad surfaces that lie on that cut plane, then spin those "
            "2D shells. This is intended for stepped/recessed revolved solids."
        ),
    }


@mcp.tool()
def execute_tcl(
    script: str,
    hmbatch_path: str | None = None,
    model_path: str | None = None,
    timeout_seconds: int = 120,
    enforce_meshing_rules: bool = True,
) -> dict[str, Any]:
    """Execute a raw HyperMesh Tcl script with hmbatch."""
    if not script.strip():
        raise ValueError("script cannot be empty.")
    if enforce_meshing_rules:
        violation = _meshing_rule_violation(script)
        if violation:
            violation["execution_mode"] = "batch"
            return violation
    return _run_hmbatch(
        hmbatch_path=hmbatch_path,
        model_path=model_path,
        script=script,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
def execute_tcl_gui(
    script: str,
    host: str = "127.0.0.1",
    port: int = DEFAULT_GUI_PORT,
    model_path: str | None = None,
    output_hm_path: str | None = None,
    timeout_seconds: int = 120,
    enforce_meshing_rules: bool = True,
) -> dict[str, Any]:
    """Execute Tcl inside an already visible HyperMesh GUI listener session."""
    if not script.strip():
        raise ValueError("script cannot be empty.")
    if enforce_meshing_rules:
        violation = _meshing_rule_violation(script)
        if violation:
            violation["execution_mode"] = "visible_gui"
            return violation

    prefix: list[str] = []
    model = _normalize_path(model_path)
    if model:
        if not model.exists():
            raise FileNotFoundError(f"Model file was not found: {model}")
        prefix.append(f'*readfile "{_quote_tcl_path(model)}"')

    suffix: list[str] = []
    if output_hm_path:
        suffix.append(f'*writefile "{_quote_tcl_path(output_hm_path)}" 1')

    gui_script = "\n".join(prefix + [script] + suffix)
    if not gui_script.endswith("\n"):
        gui_script += "\n"

    try:
        return _run_hypermesh_gui_script(
            script=gui_script,
            host=host,
            port=port,
            timeout_seconds=timeout_seconds,
        )
    except OSError as exc:
        return {
            "success": False,
            "host": host,
            "port": int(port),
            "message": (
                "Could not connect to the visible HyperMesh GUI listener. "
                "Run start_hypermesh_gui_listener, or open HyperMesh and source "
                "the Tcl file returned by create_gui_listener_tcl."
            ),
            "error": str(exc),
        }


@mcp.tool()
def make_recorded_tcl_wrapper(
    recorded_tcl_path: str,
    replacements: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Load a HyperMesh-recorded Tcl command file and optionally apply replacements."""
    path = _normalize_path(recorded_tcl_path)
    if not path or not path.exists():
        raise FileNotFoundError(f"Recorded Tcl file was not found: {recorded_tcl_path}")

    script = path.read_text(encoding="utf-8", errors="replace")
    for old, new in (replacements or {}).items():
        script = script.replace(str(old), str(new))
    return {"success": True, "script": script}


@mcp.tool()
def automesh_surfaces(
    input_hm_path: str,
    output_hm_path: str,
    element_size: float,
    surface_ids: list[int] | None = None,
    hmbatch_path: str | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Run a simple surface automesh on an existing .hm model and save a new .hm file."""
    generated = generate_surface_automesh_tcl(
        element_size=element_size,
        surface_ids=surface_ids,
        output_hm_path=output_hm_path,
    )
    result = _run_hmbatch(
        hmbatch_path=hmbatch_path,
        model_path=input_hm_path,
        script=generated["script"],
        timeout_seconds=timeout_seconds,
    )
    result["output_hm_path"] = output_hm_path
    return result


@mcp.tool()
def automesh_surfaces_gui(
    input_hm_path: str,
    output_hm_path: str,
    element_size: float,
    surface_ids: list[int] | None = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_GUI_PORT,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Run surface automesh inside the visible HyperMesh GUI and save a new file."""
    generated = generate_surface_automesh_tcl(
        element_size=element_size,
        surface_ids=surface_ids,
    )
    result = execute_tcl_gui(
        script=generated["script"],
        host=host,
        port=port,
        model_path=input_hm_path,
        output_hm_path=output_hm_path,
        timeout_seconds=timeout_seconds,
    )
    result["output_hm_path"] = output_hm_path
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HyperMesh MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="Transport mode: stdio (default, for Codex) or sse (for Cowork HTTP)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HOST", "127.0.0.1"),
        help="Host for SSE mode (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_PORT", "8742")),
        help="Port for SSE mode (default: 8742)",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        print(f"Starting HyperMesh MCP server in SSE mode on {args.host}:{args.port}")
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run()
