"""
CLI that replays local fixture files against a running service.

Each fixture's ``golden_output.input`` is sent as a POST body.  The
``x-sim-fixture-name`` header carries the fixture stem so the service
middleware can activate the correct ReplayContext for that request.

Usage::

    sim-replay --fixture-dir ./fixtures/quote \\
               --port 8080 \\
               [--host localhost] \\
               [--path /quote] \\
               [--run-id <uuid>] \\
               [--output-dir ./replay_results] \\
               [--timeout 30] \\
               [--verbose]

Exit codes::

    0  — all fixture requests completed (sent + response captured)
    1  — one or more fixtures failed (load error or network error)
    2  — no *.json files found in --fixture-dir

Exit code reflects *completion* (did the replay run?), not *correctness*
(did outputs match?).  The verifier owns correctness.

Zone 2 compliant — stdlib only:
    argparse, json, logging, pathlib, sys, urllib.error,
    urllib.parse, urllib.request, uuid
"""

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_fixture(path: Path) -> Optional[Dict]:
    """Return parsed fixture JSON or None on any read/parse failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        _log.error("Invalid JSON in fixture %s: %s", path, exc)
        return None
    except OSError as exc:
        _log.error("Cannot read fixture %s: %s", path, exc)
        return None


def _try_parse_json(data: bytes) -> object:
    """Return parsed JSON object, or None if the bytes are not valid JSON."""
    try:
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _write_result(
    output_dir: Path,
    fixture_name: str,
    status_code: int,
    body_bytes: bytes,
    headers: Dict,
) -> None:
    """Write the captured response envelope to ``output_dir/<fixture_name>.json``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "fixture_name": fixture_name,
        "status_code": status_code,
        "headers": headers,
        "body": _try_parse_json(body_bytes),
        "raw_body": body_bytes.decode("utf-8", errors="replace"),
    }
    out_path = output_dir / f"{fixture_name}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    _log.info("Wrote result: %s", out_path)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _build_url(host: str, port: int, path: str) -> str:
    path = path if path.startswith("/") else f"/{path}"
    return f"http://{host}:{port}{path}"


def _send_fixture(
    *,
    fixture_name: str,
    body: Dict,
    url: str,
    run_id: str,
    timeout: int,
) -> Tuple[int, bytes, Dict]:
    """POST ``body`` to ``url`` with sim replay headers.

    Returns ``(status_code, response_bytes, response_headers)``.

    Raises:
        urllib.error.URLError: On connection-level failures.
        OSError: On socket-level failures.
    """
    payload = json.dumps(body, default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-sim-fixture-name": fixture_name,
        "x-sim-run-id": run_id,
    }
    req = urllib.request.Request(
        url, data=payload, headers=headers, method="POST"
    )
    # urllib raises HTTPError (a subclass of URLError) for 4xx/5xx responses
    # when using urlopen; we want to capture those responses, not treat them
    # as failures, so we catch HTTPError separately to still read the body.
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        # 4xx / 5xx from the service — still "sent", capture the body.
        body_bytes = exc.read() if exc.fp is not None else b""
        return exc.code, body_bytes, dict(exc.headers)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sim-replay",
        description=(
            "Replay local fixture files against a running service. "
            "Exit code reflects completion, not correctness."
        ),
    )
    parser.add_argument(
        "--fixture-dir",
        required=True,
        metavar="DIR",
        help="Directory containing fixture JSON files.",
    )
    parser.add_argument(
        "--port",
        required=True,
        type=int,
        metavar="PORT",
        help="Service port.",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        metavar="HOST",
        help="Service host (default: localhost).",
    )
    parser.add_argument(
        "--path",
        default="/",
        metavar="PATH",
        help="HTTP endpoint path to POST to (default: /).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        metavar="UUID",
        help="Run ID for this replay session (default: auto-generated UUID).",
    )
    parser.add_argument(
        "--output-dir",
        default="./replay_results",
        metavar="DIR",
        help="Directory to write captured responses (default: ./replay_results).",
    )
    parser.add_argument(
        "--timeout",
        default=30,
        type=int,
        metavar="SECS",
        help="Per-request HTTP timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        stream=sys.stderr,
    )

    run_id: str = args.run_id or str(uuid.uuid4())
    fixture_dir = Path(args.fixture_dir)
    output_dir = Path(args.output_dir)
    url = _build_url(args.host, args.port, args.path)

    # ---- discover fixtures --------------------------------------------------
    fixture_paths = sorted(fixture_dir.glob("*.json"))
    if not fixture_paths:
        _log.error("No fixture JSON files found in: %s", fixture_dir)
        sys.exit(2)

    _log.info(
        "run_id=%s fixtures=%d url=%s output_dir=%s",
        run_id,
        len(fixture_paths),
        url,
        output_dir,
    )

    # ---- replay loop --------------------------------------------------------
    any_failed = False

    for fixture_path in fixture_paths:
        fixture_name = fixture_path.stem
        _log.info("Replaying fixture: %s", fixture_name)

        data = _load_fixture(fixture_path)
        if data is None:
            any_failed = True
            continue

        golden_output = data.get("golden_output")
        if golden_output is None:
            _log.error(
                "Fixture %s missing 'golden_output' — skipping", fixture_name
            )
            any_failed = True
            continue

        request_body: Dict = golden_output.get("input", {})

        try:
            status_code, body_bytes, resp_headers = _send_fixture(
                fixture_name=fixture_name,
                body=request_body,
                url=url,
                run_id=run_id,
                timeout=args.timeout,
            )
        except (urllib.error.URLError, OSError) as exc:
            _log.error(
                "Network error for fixture %s: %s", fixture_name, exc
            )
            any_failed = True
            continue

        _log.info(
            "fixture=%s status=%d bytes=%d",
            fixture_name,
            status_code,
            len(body_bytes),
        )
        _write_result(output_dir, fixture_name, status_code, body_bytes, resp_headers)

    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
