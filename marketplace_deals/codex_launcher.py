import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

PROMPT_EVERY_RUN = """From the project root, read and analyze `output/raw_facebook_list.json` carefully and accurately. Create/overwrite `output/organized_facebook_list.json` containing a JSON array with one object per listing.

For each listing, output these normalized fields exactly:
`brand`, `model`, `variant`, `color`, `storage_gb`, `ram_gb`, `dual_sim`, `battery_health_percent`, `accessories_included`, `condition`, `grade`, `carrier`, `price`, `location`, `recency`, `image`,
`fb_link`, `description`.

Rules:
- If a value is missing or unclear, set it to `null`. Do not omit any keys.
- Inference policy: infer only high-confidence values (for example, infer brand = "Apple" if the listing clearly indicates iPhone/Apple). Do NOT guess critical specs (model, variant, storage, ram, color,
carrier, battery health). If not explicitly stated, set them to `null`.
- `price` must be a number when possible (prefer an existing numeric value from the source if available; otherwise parse from text). If not parseable, set `null`.
- `storage_gb` and `ram_gb` must be numbers (e.g., 128, 8) only when explicitly stated; otherwise `null`.
- `dual_sim` must be one of: `"yes"`, `"no"`, or `null`. Only set `"yes"` or `"no"` if explicitly indicated.
- `battery_health_percent` must be a number between 0–100 only when explicitly stated; otherwise `null`.
- `accessories_included` must be a boolean (`true`, `false`) only when explicitly indicated; otherwise `null`.
- Keep `location`, `recency`, `image`, and `fb_link` aligned exactly with the corresponding source fields for the same listing. Do not fabricate or modify them.

Grade field rule (`grade`):
- Allowed values are only: `"A"`, `"B"`, `"C"`, or `null`.
- First priority: if title/description explicitly states grade wording, map using:
  - Grade A aliases: `A`, `A+`, `Mint`, `Pristine`, `Excellent`, `Like New`.
  - Grade B aliases: `B`, `B+`, `Very Good`, `Good Condition`.
  - Grade C aliases: `C`, `C+`, `Fair`, `Acceptable`.
- If no explicit grade/alias exists, infer from condition text only when high confidence:
  - Grade A: almost no visible marks, no screen scratches, only very light micro marks, fully functional.
  - Grade B: light (not deep) screen scratches, small scuffs/marks on body, no cracks, fully functional.
  - Grade C: visible scratches (possibly fingernail-feel), dents/heavier scuffing, small paint chips, fully working.
- If conflicting signals exist, choose the lower (worse) grade.

Description field rule:
- The `description` field must contain any relevant information mentioned in the original title or description that is NOT already captured by the structured fields above.
- Do not duplicate information that has already been normalized into other fields.
- Keep the text concise but complete.

Write valid, properly formatted, pretty-printed JSON.

After finishing file creation, respond with exactly:
DONE"""


FILTER_PROMPT_TEMPLATE = """From the project root, read `output/organized_facebook_list.json`and create `output/filtered_facebook_list.json`.

Apply strict filtering using these exact runtime values:
- ProductName: "__PRODUCT_NAME__"
- Price Min: __PRICE_MIN__
- Price Max: __PRICE_MAX__
- Date Listed: "__DATE_LISTED__"
- Filtering Description: "__FILTERING_DESCRIPTION__"


FILTERING RULES
1. A listing passes ONLY if it satisfies ALL provided filters.
2. If a listing fails even one filter, do not move that listing to the new file..
3. Do NOT modify any listing fields or values.
4. Move passed listing objects exactly as they appear in the input.


GROUPING RULES
After filtering, group the remaining listings by EXACT equality of:

- brand
- model
- variant
- storage_gb
- ram_gb
- grade

Listings must match all six fields exactly to be in the same group.


GROUP TITLE FORMAT
Format:
brand + model + variant + storage_gb + "GB, " + grade

Examples:
- Apple iPhone 15 256GB, A
- Apple iPhone 15 Pro 128GB, A

Title defaults:
- If storage_gb is null → use 256 in title only
- If grade is null → use B in title only

IMPORTANT:
- Do NOT modify the listing objects.
- Defaults apply ONLY to group_title.


OUTPUT STRUCTURE
The output must be a JSON array of objects in this format:

[
  {
    "group_title": "Apple iPhone 15 256GB, A",
    "listings": [
      { full original listing object },
      { full original listing object },
    ]
  }
]

Do not include any extra fields.
Do not include explanations.
Do not include logs.
Do not include comments.

After writing the file, respond with exactly: DONE"""


def _validate_json_output(path: Path, require_array_root: bool = True) -> tuple[bool, int, str]:
    if not path.exists():
        return False, 0, f"missing file: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, 0, f"invalid JSON in {path}: {exc}"
    if require_array_root:
        if not isinstance(payload, list):
            return False, 0, f"JSON root is not an array in {path}"
        return True, len(payload), ""

    if isinstance(payload, list):
        return True, len(payload), ""
    if isinstance(payload, dict):
        filtered_listings = payload.get("filtered_listings")
        if isinstance(filtered_listings, list):
            return True, len(filtered_listings), ""
        listings = payload.get("listings")
        if isinstance(listings, list):
            return True, len(listings), ""
        return True, 0, ""

    return False, 0, f"JSON root is neither object nor array in {path}"


def _resolve_codex_executable() -> str:
    direct = shutil.which("codex")
    if direct:
        return direct

    cmd_variant = shutil.which("codex.cmd")
    if cmd_variant:
        return cmd_variant

    nvm_symlink = os.environ.get("NVM_SYMLINK", "").strip()
    if nvm_symlink:
        candidate = Path(nvm_symlink) / "codex.cmd"
        if candidate.exists():
            return str(candidate)

    common_candidate = Path(r"C:\nvm4w\nodejs\codex.cmd")
    if common_candidate.exists():
        return str(common_candidate)

    raise RuntimeError(
        "codex executable not found in PATH (or common NVM locations). "
        "Ensure Codex CLI is installed and available to the API process."
    )


def _open_status_cmd_window(
    root: Path,
    live_log_file: Path,
    done_signal_file: Path,
    title: str,
    stage_name: str,
) -> bool:
    def _ps_quote(text: str) -> str:
        return text.replace("'", "''")

    ps_root = _ps_quote(str(root))
    ps_log = _ps_quote(str(live_log_file))
    ps_done = _ps_quote(str(done_signal_file))
    ps_title = _ps_quote(title)
    ps_command = (
        f"$Host.UI.RawUI.WindowTitle = '{ps_title}'; "
        f"Set-Location -LiteralPath '{ps_root}'; "
        f"Write-Host 'Codex {stage_name} started.'; "
        f"Write-Host 'Streaming {stage_name} output below (window closes automatically when done).'; "
        "Write-Host ''; "
        f"$logPath = '{ps_log}'; "
        f"$donePath = '{ps_done}'; "
        "$offset = 0; "
        "while ($true) { "
        "  if (Test-Path -LiteralPath $logPath) { "
        "    $content = Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue; "
        "    if ($null -ne $content -and $content.Length -gt $offset) { "
        "      $chunk = $content.Substring($offset); "
        "      Write-Host -NoNewline $chunk; "
        "      $offset = $content.Length; "
        "    } "
        "  } "
        "  if (Test-Path -LiteralPath $donePath) { break } "
        "  Start-Sleep -Milliseconds 200; "
        "} "
        "if (Test-Path -LiteralPath $logPath) { "
        "  $content = Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue; "
        "  if ($null -ne $content -and $content.Length -gt $offset) { "
        "    $chunk = $content.Substring($offset); "
        "    Write-Host -NoNewline $chunk; "
        "  } "
        "}"
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-Command", ps_command],
            cwd=str(root),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return True
    except Exception:
        # Visibility window is optional and must never break the pipeline.
        return False


def _format_prompt_number(value: Optional[float]) -> str:
    if value is None:
        return "null"
    return json.dumps(value)


def _format_prompt_text(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")


def _render_filter_prompt(
    product_name: str,
    price_min: Optional[float],
    price_max: Optional[float],
    date_listed: str,
    filtering_description: str,
) -> str:
    return (
        FILTER_PROMPT_TEMPLATE
        .replace("__PRODUCT_NAME__", _format_prompt_text(product_name))
        .replace("__PRICE_MIN__", _format_prompt_number(price_min))
        .replace("__PRICE_MAX__", _format_prompt_number(price_max))
        .replace("__DATE_LISTED__", _format_prompt_text(date_listed))
        .replace("__FILTERING_DESCRIPTION__", _format_prompt_text(filtering_description))
    )


def _run_codex_stage(
    root: Path,
    codex_executable: str,
    prompt_text: str,
    expected_output_file: Path,
    require_array_root: bool,
    window_title: str,
    stage_name: str,
    timeout_seconds: int,
) -> Dict[str, Any]:
    runtime_dir = Path(tempfile.gettempdir()) / "marketplace_codex_organizer"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stale_before = time.time() - (24 * 60 * 60)
    for stale_file in runtime_dir.glob("*"):
        try:
            if stale_file.is_file() and stale_file.stat().st_mtime < stale_before:
                stale_file.unlink()
        except Exception:
            continue

    run_id = f"{int(time.time() * 1000)}-{os.getpid()}-{stage_name}"
    live_log_file = runtime_dir / f"{run_id}.log"
    done_signal_file = runtime_dir / f"{run_id}.done"
    last_message_file = runtime_dir / f"{run_id}.lastmsg"

    for path in (done_signal_file, last_message_file):
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass

    live_log_file.write_text("", encoding="utf-8")
    cmd_window_launched = _open_status_cmd_window(
        root=root,
        live_log_file=live_log_file,
        done_signal_file=done_signal_file,
        title=window_title,
        stage_name=stage_name,
    )

    command = [
        codex_executable,
        "exec",
        "-C",
        str(root),
        "--skip-git-repo-check",
        "--output-last-message",
        str(last_message_file),
        "-",
    ]
    timeout_limit = max(60, int(timeout_seconds))
    try:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except Exception as exc:
        raise RuntimeError(f"failed to start codex {stage_name} process: {exc}") from exc

    output_chunks: list[str] = []
    result_returncode: int | None = None
    try:
        try:
            if process.stdin is not None:
                try:
                    process.stdin.write(prompt_text)
                except BrokenPipeError:
                    pass
                finally:
                    process.stdin.close()

            start_time = time.monotonic()
            if process.stdout is None:
                raise RuntimeError(f"codex {stage_name} stdout pipe unavailable")
            with live_log_file.open("a", encoding="utf-8", errors="ignore") as log_handle:
                while True:
                    if time.monotonic() - start_time > timeout_limit:
                        process.kill()
                        raise subprocess.TimeoutExpired(command, timeout_limit)

                    line = process.stdout.readline()
                    if line:
                        output_chunks.append(line)
                        log_handle.write(line)
                        log_handle.flush()
                        continue

                    if process.poll() is not None:
                        break
                    time.sleep(0.05)

                remainder = process.stdout.read()
                if remainder:
                    output_chunks.append(remainder)
                    log_handle.write(remainder)
                    log_handle.flush()
            result_returncode = process.wait()
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"codex {stage_name} timed out after {timeout_seconds} seconds") from exc

        stdout_text = "".join(output_chunks)

        final_message = ""
        if last_message_file.exists():
            try:
                final_message = last_message_file.read_text(encoding="utf-8").strip()
            except Exception:
                final_message = ""

        is_valid_output, output_count, validation_error = _validate_json_output(
            expected_output_file,
            require_array_root=require_array_root,
        )
        if result_returncode is None:
            raise RuntimeError(f"codex {stage_name} process did not return an exit code")
        if result_returncode != 0 and not is_valid_output:
            stdout_tail = (stdout_text or "").strip()[-1200:]
            raise RuntimeError(
                f"codex {stage_name} failed with exit code "
                f"{result_returncode}. {validation_error or 'output missing.'}"
                + (f" Log tail: STDOUT tail: {stdout_tail}" if stdout_tail else "")
            )
        if not is_valid_output:
            raise RuntimeError(f"codex {stage_name} output invalid: {validation_error}")

        return {
            "launched_cmd_window": cmd_window_launched,
            "return_code": int(result_returncode),
            "final_message": final_message,
            "strict_done_met": final_message == "DONE",
            "output_count": output_count,
            "output_path": str(expected_output_file.resolve()),
        }
    finally:
        try:
            done_signal_file.write_text("done", encoding="utf-8")
        except Exception:
            pass
        for path in (live_log_file, done_signal_file, last_message_file):
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass


def run_codex_organizer(
    project_root: Path,
    product_name: str,
    price_min: Optional[float],
    price_max: Optional[float],
    date_listed: str,
    filtering_description: str,
    timeout_seconds: int = 3600,
) -> Dict[str, Any]:
    root = project_root.resolve()
    output_dir = root / "output"
    output_dir.mkdir(exist_ok=True)
    raw_file = output_dir / "raw_facebook_list.json"
    organized_file = output_dir / "organized_facebook_list.json"
    filtered_file = output_dir / "filtered_facebook_list.json"
    legacy_log_file = output_dir / "codex_organizer_exec.log"
    codex_executable = _resolve_codex_executable()

    if not raw_file.exists():
        raise RuntimeError(f"missing input file: {raw_file}")

    if legacy_log_file.exists():
        try:
            legacy_log_file.unlink()
        except Exception:
            pass

    organizer_meta = _run_codex_stage(
        root=root,
        codex_executable=codex_executable,
        prompt_text=PROMPT_EVERY_RUN,
        expected_output_file=organized_file,
        require_array_root=True,
        window_title="Marketplace Codex Organizer",
        stage_name="organizer",
        timeout_seconds=timeout_seconds,
    )

    filter_prompt = _render_filter_prompt(
        product_name=product_name,
        price_min=price_min,
        price_max=price_max,
        date_listed=date_listed,
        filtering_description=filtering_description,
    )
    filter_meta = _run_codex_stage(
        root=root,
        codex_executable=codex_executable,
        prompt_text=filter_prompt,
        expected_output_file=filtered_file,
        require_array_root=True,
        window_title="Marketplace Codex Filter",
        stage_name="filter",
        timeout_seconds=timeout_seconds,
    )

    return {
        "launched_cmd_window": bool(organizer_meta.get("launched_cmd_window") or filter_meta.get("launched_cmd_window")),
        "launched_cmd_window_organizer": bool(organizer_meta.get("launched_cmd_window")),
        "launched_cmd_window_filter": bool(filter_meta.get("launched_cmd_window")),
        "return_code": int(filter_meta.get("return_code", 0)),
        "final_message": str(filter_meta.get("final_message", "")),
        "strict_done_met": bool(
            organizer_meta.get("strict_done_met", False) and filter_meta.get("strict_done_met", False)
        ),
        "strict_done_met_organizer": bool(organizer_meta.get("strict_done_met", False)),
        "strict_done_met_filter": bool(filter_meta.get("strict_done_met", False)),
        "organized_count": int(organizer_meta.get("output_count", 0)),
        "organized_path": str(organizer_meta.get("output_path", str(organized_file.resolve()))),
        "filtered_count": int(filter_meta.get("output_count", 0)),
        "filtered_path": str(filter_meta.get("output_path", str(filtered_file.resolve()))),
    }
