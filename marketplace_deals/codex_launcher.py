import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

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


def _validate_organized_output(path: Path) -> tuple[bool, int, str]:
    if not path.exists():
        return False, 0, f"missing file: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, 0, f"invalid JSON in {path}: {exc}"
    if not isinstance(payload, list):
        return False, 0, f"JSON root is not an array in {path}"
    return True, len(payload), ""


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


def _open_status_cmd_window(root: Path, live_log_file: Path, done_signal_file: Path) -> bool:
    title = "Marketplace Codex Organizer"

    def _ps_quote(text: str) -> str:
        return text.replace("'", "''")

    ps_root = _ps_quote(str(root))
    ps_log = _ps_quote(str(live_log_file))
    ps_done = _ps_quote(str(done_signal_file))
    ps_title = _ps_quote(title)
    ps_command = (
        f"$Host.UI.RawUI.WindowTitle = '{ps_title}'; "
        f"Set-Location -LiteralPath '{ps_root}'; "
        "Write-Host 'Codex organizer started.'; "
        "Write-Host 'Streaming organizer output below (window closes automatically when done).'; "
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


def run_codex_organizer(project_root: Path, timeout_seconds: int = 3600) -> Dict[str, Any]:
    root = project_root.resolve()
    output_dir = root / "output"
    output_dir.mkdir(exist_ok=True)
    raw_file = output_dir / "raw_facebook_list.json"
    last_message_file = output_dir / "codex_organizer_last_message.txt"
    organized_file = output_dir / "organized_facebook_list.json"
    legacy_log_file = output_dir / "codex_organizer_exec.log"
    codex_executable = _resolve_codex_executable()

    if not raw_file.exists():
        raise RuntimeError(f"missing input file: {raw_file}")

    if last_message_file.exists():
        try:
            last_message_file.unlink()
        except Exception:
            pass
    if legacy_log_file.exists():
        try:
            legacy_log_file.unlink()
        except Exception:
            pass

    runtime_dir = Path(tempfile.gettempdir()) / "marketplace_codex_organizer"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stale_before = time.time() - (24 * 60 * 60)
    for stale_file in runtime_dir.glob("*"):
        try:
            if stale_file.is_file() and stale_file.stat().st_mtime < stale_before:
                stale_file.unlink()
        except Exception:
            continue
    run_id = f"{int(time.time() * 1000)}-{os.getpid()}"
    live_log_file = runtime_dir / f"{run_id}.log"
    done_signal_file = runtime_dir / f"{run_id}.done"

    if done_signal_file.exists():
        try:
            done_signal_file.unlink()
        except Exception:
            pass

    # Ensure the live viewer has a file to stream immediately.
    live_log_file.write_text("", encoding="utf-8")
    cmd_window_launched = _open_status_cmd_window(root, live_log_file, done_signal_file)

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
        raise RuntimeError(f"failed to start codex organizer process: {exc}") from exc

    output_chunks: list[str] = []
    result_returncode: int | None = None
    try:
        try:
            if process.stdin is not None:
                try:
                    process.stdin.write(PROMPT_EVERY_RUN)
                except BrokenPipeError:
                    pass
                finally:
                    process.stdin.close()

            start_time = time.monotonic()
            if process.stdout is None:
                raise RuntimeError("codex organizer stdout pipe unavailable")
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
            raise RuntimeError(f"codex organizer timed out after {timeout_seconds} seconds") from exc

        stdout_text = "".join(output_chunks)

        final_message = ""
        if last_message_file.exists():
            try:
                final_message = last_message_file.read_text(encoding="utf-8").strip()
            except Exception:
                final_message = ""

        is_valid_output, organized_count, validation_error = _validate_organized_output(organized_file)
        if result_returncode is None:
            raise RuntimeError("codex organizer process did not return an exit code")
        if result_returncode != 0 and not is_valid_output:
            stdout_tail = (stdout_text or "").strip()[-1200:]
            raise RuntimeError(
                "codex exec failed with exit code "
                f"{result_returncode}. {validation_error or 'organized output missing.'}"
                + (f" Log tail: STDOUT tail: {stdout_tail}" if stdout_tail else "")
            )
        if not is_valid_output:
            raise RuntimeError(f"codex organizer output invalid: {validation_error}")

        return {
            "launched_cmd_window": cmd_window_launched,
            "return_code": int(result_returncode),
            "final_message": final_message,
            "strict_done_met": final_message == "DONE",
            "organized_count": organized_count,
            "organized_path": str(organized_file.resolve()),
        }
    finally:
        try:
            done_signal_file.write_text("done", encoding="utf-8")
        except Exception:
            pass
        try:
            if last_message_file.exists():
                last_message_file.unlink()
        except Exception:
            pass
