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
6. For simple straight tube/cylinder drag hex meshing, first impose the same
   seed count on every logical edge of the selected 2D source face before
   automesh. Example: if one source-face edge previews as 51 and another as 58,
   compute one common target from the actual face size and target element size,
   then force source-face edge indices 0/1/2/3 to that target before automesh. Do
   not hard-code 51 or 58 unless the geometry/mesh-size calculation actually
   chooses it. Continue only when the source face is a mapped, 100% quad mesh
   created with uniform per-face edge seeding. If that cannot be guaranteed,
   try a spin-hex strategy for suitable revolved bodies, otherwise fall back to
   tetra.
7. For obvious revolved bodies, prefer spin hex meshing, but never invent the
   section from guessed radii or from a side/end face. First split the solid with
   a real middle cutting plane, use only the newly created surfaces that lie on
   that cutting plane as 2D section sources, mesh those section surfaces as 100%
   quads, then spin to 3D. If the true cut section cannot be guaranteed as all
   quads, fall back to the tetra strategy.
8. Do not overuse drag. Use spin where the geometry is a clean revolved part;
   use tetra when drag/spin would force bad topology.
9. Component names should describe the physical object, not the mesh type.
   Examples: flange, bearing_6903_left, cutout_body, spacer_block_left_upper.
10. Do not repair quality by blindly refining the whole mesh. Prefer strategy
   changes, local 3D smoothing/remesh, or sliver-tetra repair. If bad volume
   elements still cannot be fixed, keep them in the model and report them; do
   not delete unfixable quality-failed elements unless the user explicitly asks.
"""

A_HM_MESHING_PLAN = {
    "tetra_surface_deviation_rtrias": [
        "flange_outer_fillet",
        "cutout_body",
        "rounded_flange_fillet",
        "ring_spacer",
        "boss_body",
        "boss_cutout",
        "bearing_1730_cover",
        "main_flange",
        "moved_face_housing",
        "chamfered_flange_edge",
        "bearing_6903_inner_ring_left",
        "bearing_6903_outer_ring_left",
        "bearing_6903_inner_ring_right",
        "bearing_6903_outer_ring_right",
    ],
    "drag_hex_guarded": [
        "extruded_plate_left",
        "extruded_plate_right",
        "thin_extruded_plate",
        "bearing_1730_ring_left",
        "bearing_1730_ring_right",
        "spacer_block_left_upper",
        "spacer_block_left_lower",
        "spacer_block_right_upper",
        "spacer_block_right_lower",
    ],
    "spin_hex_guarded": [
        "bearing_6903_left",
        "bearing_6903_right",
    ],
}

A_HM_SPECIAL_WORKFLOWS = {
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
    "bearing_6903_cutsection_spin": {
        "script": r"F:\mcp\mesh_a_strategy_v17_cutsection_spin.tcl",
        "input": r"F:\a_meshed_strategy_v12.hm",
        "output": r"F:\a_meshed_strategy_v17_cutsection_spin.hm",
        "report": r"F:\a_meshed_strategy_v17_cutsection_spin_report.txt",
        "components": ["bearing_6903_left", "bearing_6903_right"],
        "method": [
            "Start from the v12 model rather than the earlier incorrect spin attempts.",
            "Delete only the old elements in bearing_6903_left and bearing_6903_right before replacement.",
            "Split each bearing solid with body_splitmerge_with_plane using a middle plane.",
            "Detect the real section surfaces by meshing each new surface temporarily and checking node distance to the split plane.",
            "Accept only all-quad section meshes that lie on the split plane, then spin them 360 degrees about the x axis.",
            "Delete only the temporary 2D seed shell elements after spin; keep generated 3D hex elements.",
        ],
        "validated_result": {
            "bearing_6903_left": "17280 hex8 elements",
            "bearing_6903_right": "17280 hex8 elements",
        },
        "recorded_user_split_reference": {
            "command_file": r"C:\Users\qcyti\Documents\command1.tcl",
            "right_solid_command": (
                "*createmark solids 1 23; "
                "*createplane 1 0 -0.831039379 -0.556213583 "
                "-55.8785515 8.02783775 1035.87476; "
                "*body_splitmerge_with_plane solids 1 1"
            ),
        },
    },
    "quality_policy": {
        "repair_script": r"F:\mcp\repair_v12_bad_vol.tcl",
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
    if {{$code == 0}} {{
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
        "a_hm_plan": A_HM_MESHING_PLAN,
        "special_workflows": A_HM_SPECIAL_WORKFLOWS,
        "default_hmbatch": str(DEFAULT_HMBATCH),
    }


@mcp.tool()
def get_a_hm_meshing_plan() -> dict[str, Any]:
    """Return the latest model-specific meshing plan for F:\\a.hm."""
    return {
        "success": True,
        "input_model": r"F:\a.hm",
        "latest_output": r"F:\a_meshed_strategy_v17_cutsection_spin.hm",
        "latest_script": r"F:\mcp\mesh_a_strategy_v17_cutsection_spin.tcl",
        "latest_report": r"F:\a_meshed_strategy_v17_cutsection_spin_report.txt",
        "baseline_model_for_v17": r"F:\a_meshed_strategy_v12.hm",
        "plan": A_HM_MESHING_PLAN,
        "special_workflows": A_HM_SPECIAL_WORKFLOWS,
        "notes": [
            "cutout_body and main_flange are treated as flange/complex tetra parts, not drag parts.",
            "drag_hex_guarded entries still require 100% quad source-face verification before drag.",
            "bearing_6903_left and bearing_6903_right must use the real cut-section spin workflow, not a guessed section.",
            "quality cleanup should prefer strategy changes and local repair; do not blindly refine or delete unfixable bad elements.",
            "visible GUI mode changes where the Tcl runs; meshing logic and input/output paths remain explicit.",
        ],
    }


@mcp.tool()
def get_a_hm_cutsection_spin_workflow() -> dict[str, Any]:
    """Return the validated cut-section spin workflow for F:\\a.hm bearing_6903 solids."""
    return {
        "success": True,
        "workflow": A_HM_SPECIAL_WORKFLOWS["bearing_6903_cutsection_spin"],
        "gui_mode": A_HM_SPECIAL_WORKFLOWS["visible_gui_mode"],
        "quality_policy": A_HM_SPECIAL_WORKFLOWS["quality_policy"],
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
    source_faces_can_be_all_quads: bool = False,
    matched_inner_outer_seed_counts: bool = False,
) -> dict[str, Any]:
    """Classify a part into tetra, drag-hex, or spin-hex strategy."""
    text = f"{part_name} {description}".lower()
    flange_words = ("flange", "法兰")
    bolt_words = ("bolt", "hole", "孔", "螺栓", "螺孔")

    looks_like_flange = is_flange or any(word in text for word in flange_words)
    looks_like_bolted = has_bolt_holes or any(word in text for word in bolt_words)

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
            "strategy": "tetra_surface_deviation_rtrias",
            "reason": (
                "Revolved body did not prove all-quad source section; fall back "
                "to tetra strategy."
            ),
            "required_checks": [
                "2D aspect <= 10 after cleanup",
                "3D vol skew <= 0.99 after repair",
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
def generate_guarded_drag_hex_tcl(
    source_surface_id: int,
    drag_distance: float,
    element_size: float,
    component_name: str,
    axis: str = "z",
    layer_count: int | None = None,
    matched_edge_groups: list[list[int]] | None = None,
    target_density: int | None = None,
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
    group_lines: list[str] = []
    if matched_edge_groups:
        group_text = " ".join(
            "{" + " ".join(str(int(edge)) for edge in group) + "}"
            for group in matched_edge_groups
        )
        group_lines.extend(
            [
                f"set matched_edge_groups {{{group_text}}}",
                f"set target_density {int(target_density) if target_density else 0}",
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
                "}",
                'puts "MCP guarded drag uniform source-face target_density=$target_density"',
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
        "# Precondition: all logical edges of the drag source face must share",
        "# one target_density computed from object size / target element size.",
        "# Example: if the source face previews edge seeds 51 and 58, pass all",
        "# logical edge indices 0/1/2/3 with the same computed target_density.",
        "# If the source face is not mapped 100% quads after uniform seeding, skip drag.",
        f'set drag_component "{comp}"',
        f"set source_surface {int(source_surface_id)}",
        f"set elem_size {float(element_size)}",
        f"set drag_distance {float(drag_distance)}",
        f"set drag_layers {int(layers)}",
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
        "} else {",
        f"    *createvector 1 {vx} {vy} {vz}",
        "    *meshdragelements2 1 1 $drag_distance $drag_layers 0 0.0 0",
        "    eval *createmark elems 1 $source_shells",
        "    catch {*deletemark elems 1}",
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
            "force corresponding edge groups to the same target_density (for "
            "example 51/58 -> both 58), then require a 100% quad source face. "
            "Never use it for flanges with bolt holes."
        ),
    }


@mcp.tool()
def generate_guarded_spin_hex_tcl(
    source_surface_id: int,
    element_size: float,
    component_name: str,
    axis: str = "z",
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
    lines = [
        "# HyperMesh MCP generated guarded spin-hex script",
        "# Use for clean revolved bodies. Do not use for flanges with bolt holes or protrusions.",
        "# Precondition: the selected source section should have matched edge seeds and be all quads.",
        f'set spin_component "{comp}"',
        f"set source_surface {int(source_surface_id)}",
        f"set elem_size {float(element_size)}",
        f"set spin_angle {float(angle_degrees)}",
        f"set spin_density {int(density)}",
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
        "} else {",
        f"    *createplane 1 {nx} {ny} {nz} $cx $cy $cz",
        "    *meshspinelements2 1 1 $spin_angle $spin_density 1 0.0 0",
        "    eval *createmark elems 1 $source_shells",
        "    catch {*deletemark elems 1}",
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
            "or any non-quad section."
        ),
    }


@mcp.tool()
def execute_tcl(
    script: str,
    hmbatch_path: str | None = None,
    model_path: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Execute a raw HyperMesh Tcl script with hmbatch."""
    if not script.strip():
        raise ValueError("script cannot be empty.")
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
) -> dict[str, Any]:
    """Execute Tcl inside an already visible HyperMesh GUI listener session."""
    if not script.strip():
        raise ValueError("script cannot be empty.")

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
