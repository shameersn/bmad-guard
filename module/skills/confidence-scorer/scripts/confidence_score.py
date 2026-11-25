# /// script
# requires-python = ">=3.10"
# ///
"""Compute a deterministic 0-100 code-confidence score for a single file.

Combines four signals into one weighted score:
  1. test pass/fail rate for tests touching the file
  2. lint/static-analysis error and warning counts
  3. lines changed relative to file size (diff-size ratio)
  4. revision cycle count for the run (pulled from approval-state.json
     when --state-file/--doc-type are given instead of an explicit
     --revision-cycles)

All raw signal counts (tests passed/failed, lint errors/warnings, lines
changed) are supplied by the caller after running the project's actual
test runner / linter / `git diff --stat` -- this script does no static
analysis of its own and makes no judgment call about the numbers; it only
turns already-measured counts into a score, a label, and a rationale that
spells out every signal's score, weight, and contribution to the final
number (not just the weakest one), so the full "why" is reconstructable
from the logged row alone. Every weight, penalty, and bucket cutoff is a
CLI argument with a sensible default -- none of them are hardcoded past
the argparse defaults, so a team can retune scoring without touching this
file.

Prints one JSON object to stdout (or --output) with the final score, a
fully detailed confidence_rationale, plus the raw per-component breakdown
to audit how it was reached. Also records the score as a guard event
(guard "confidence-scorer", reason CODE_SCORED) through Ledger's queue.

Exit codes: 0 scored; 2 bad input (negative counts or weights, all weights zero).
"""

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
VERSION_FIELD_RE = re.compile(r"^version\s*:\s*(.*)$", re.MULTILINE)

DEFAULT_DIFF_RATIO_CUTOFFS = [[0.1, 100], [0.3, 80], [0.6, 60], [1.0, 40], [999999, 20]]
DEFAULT_REVISION_CYCLE_CUTOFFS = [[0, 100], [1, 80], [2, 60], [3, 40], [999999, 20]]
DEFAULT_LABEL_CUTOFFS = [[80, "High"], [50, "Medium"], [0, "Low"]]


def read_version(doc_path: Path) -> int:
    if not doc_path.exists():
        return 0
    try:
        text = doc_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    match = FRONTMATTER_RE.match(text)
    if not match:
        return 0
    existing = VERSION_FIELD_RE.search(match.group(1))
    if not existing:
        return 0
    try:
        return int(str(existing.group(1)).strip())
    except ValueError:
        return 0


def project_root_for(path: Path) -> Path:
    """Git top level of the folder holding `path`; the folder itself when it is not in git."""
    folder = path if path.is_dir() else path.parent
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, text=True)
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else folder


def resolve_from_state(state_file: str, doc_type: str) -> tuple:
    """Return (revision_cycles, doc_status) derived from approval-state.json.

    Entries written by Warden store doc_path relative to the project root; older entries may be absolute.
    """
    path = Path(state_file)
    if not path.parent.exists():
        return 0, "unapproved"
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    if not isinstance(state, dict):
        state = {}

    entry = state.get(doc_type)
    if not isinstance(entry, dict):
        return 0, "unapproved"

    doc_status = "draft" if entry.get("superseded") else "approved"
    revision_cycles = 0
    if entry.get("doc_path"):
        doc = Path(entry["doc_path"])
        if not doc.is_absolute():
            doc = project_root_for(path) / doc
        revision_cycles = read_version(doc)
    return revision_cycles, doc_status


def bucket_score(value: float, cutoffs: list) -> float:
    """First cutoff (ascending max-threshold, score) where value <= threshold."""
    for threshold, score in sorted(cutoffs, key=lambda c: c[0]):
        if value <= threshold:
            return score
    return cutoffs[-1][1]


def bucket_label(score: float, cutoffs: list) -> str:
    """First cutoff (descending min-threshold, label) where score >= threshold."""
    for threshold, label in sorted(cutoffs, key=lambda c: -c[0]):
        if score >= threshold:
            return label
    return sorted(cutoffs, key=lambda c: c[0])[0][1]


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def compute(args) -> dict:
    tests_total = args.tests_passed + args.tests_failed

    if tests_total == 0:
        test_score = args.no_tests_score
        test_phrase = "no tests touch this file"
    elif args.tests_failed > 0:
        test_score = 100 * args.tests_passed / tests_total
        test_phrase = plural(args.tests_failed, "test failure")
    else:
        test_score = 100
        test_phrase = f"{args.tests_passed}/{tests_total} tests passed"

    lint_score = max(0, 100 - args.lint_errors * args.error_penalty - args.lint_warnings * args.warning_penalty)
    lint_parts = []
    if args.lint_errors > 0:
        lint_parts.append(plural(args.lint_errors, "lint error"))
    if args.lint_warnings > 0:
        lint_parts.append(plural(args.lint_warnings, "lint warning"))
    lint_phrase = ", ".join(lint_parts) if lint_parts else "no lint issues"

    file_size = args.file_size
    if file_size is None:
        file_path = Path(args.file_path)
        if file_path.exists():
            try:
                file_size = sum(1 for _ in file_path.open("r", encoding="utf-8", errors="ignore"))
            except OSError:
                file_size = 0
        else:
            file_size = 0

    ratio = args.lines_changed / max(file_size, 1)
    diff_score = bucket_score(ratio, args.diff_ratio_cutoffs)
    diff_phrase = f"{args.lines_changed} lines changed of {file_size} ({ratio:.0%})"

    if args.revision_cycles is not None:
        revision_cycles = args.revision_cycles
    elif args.state_file and args.doc_type:
        revision_cycles, resolved_doc_status = resolve_from_state(args.state_file, args.doc_type)
        if args.doc_status is None:
            args.doc_status = resolved_doc_status
    else:
        revision_cycles = 0

    revision_score = bucket_score(revision_cycles, args.revision_cycle_cutoffs)
    revision_phrase = plural(revision_cycles, "revision cycle") if revision_cycles > 0 else "no revision cycles"

    doc_status = args.doc_status or ""

    weights = {
        "test": args.test_weight,
        "lint": args.lint_weight,
        "diff": args.diff_weight,
        "revision": args.revision_weight,
    }
    total_weight = sum(weights.values()) or 1

    components = {
        "test": {"score": test_score, "weight": weights["test"], "phrase": test_phrase},
        "lint": {"score": lint_score, "weight": weights["lint"], "phrase": lint_phrase},
        "diff": {"score": diff_score, "weight": weights["diff"], "phrase": diff_phrase},
        "revision": {"score": revision_score, "weight": weights["revision"], "phrase": revision_phrase},
    }

    confidence = round(sum(c["score"] * c["weight"] for c in components.values()) / total_weight)
    confidence = max(0, min(100, confidence))

    confidence_label = bucket_label(confidence, args.label_cutoffs)

    rationale_parts = []
    for name in ("test", "lint", "diff", "revision"):
        c = components[name]
        contrib = c["score"] * c["weight"] / total_weight
        rationale_parts.append(
            f"{name}: {c['phrase']} (score={c['score']:g}, weight={c['weight']:g}%, contrib={contrib:.1f})"
        )
    confidence_rationale = "; ".join(rationale_parts) + f"; total={confidence}"

    return {
        "file_path": args.file_path,
        "confidence": confidence,
        "confidence_label": confidence_label,
        "confidence_rationale": confidence_rationale,
        "doc_status": doc_status,
        "components": components,
    }


def validate(args) -> None:
    """Raise ValueError for inputs that cannot be real measurements."""
    counts = ("tests_passed", "tests_failed", "lint_errors", "lint_warnings", "lines_changed")
    for name in counts:
        if getattr(args, name) < 0:
            raise ValueError(f"--{name.replace('_', '-')} must not be negative")
    if args.file_size is not None and args.file_size < 0:
        raise ValueError("--file-size must not be negative")
    if args.revision_cycles is not None and args.revision_cycles < 0:
        raise ValueError("--revision-cycles must not be negative")
    weights = (args.test_weight, args.lint_weight, args.diff_weight, args.revision_weight)
    if any(w < 0 for w in weights):
        raise ValueError("weights must not be negative")
    if sum(weights) == 0:
        raise ValueError("at least one weight must be above 0")
    if args.error_penalty < 0 or args.warning_penalty < 0:
        raise ValueError("penalties must not be negative")


def _guard_events():
    """Ledger's sender from the sibling skill folder; None when Ledger is not installed."""
    path = Path(__file__).resolve().parents[2] / "agent-project-logger" / "scripts" / "guard_events.py"
    if not path.is_file():
        return None
    if "guard_events" in sys.modules:
        return sys.modules["guard_events"]
    spec = importlib.util.spec_from_file_location("guard_events", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["guard_events"] = module
    spec.loader.exec_module(module)
    return module


def emit_score_event(result: dict, args) -> None:
    """Record the score as a guard event. Never raises; warns on stderr when it cannot."""
    try:
        ge = _guard_events()
        if ge is None:
            print(
                "bmad-guard: warning: Ledger (agent-project-logger) not installed; score not recorded", file=sys.stderr
            )
            return
        file_path = Path(args.file_path)
        root = (Path(args.project_root) if args.project_root else project_root_for(file_path.resolve())).resolve()
        try:
            target = file_path.resolve().relative_to(root).as_posix()
        except ValueError:
            target = args.file_path
        fields = {
            "agent": "confidence-scorer/SCORE",
            "target_path": target,
            "confidence": result["confidence"],
            "confidence_label": result["confidence_label"],
            "confidence_rationale": result["confidence_rationale"],
            "doc_status": result["doc_status"],
            "doc_type": args.doc_type or "",
        }
        if file_path.is_file():
            fields["target_sha256"] = hashlib.sha256(file_path.read_bytes()).hexdigest()
        verdict = "warn" if result["confidence_label"] == "Low" else "info"
        event = ge.build_event("confidence-scorer", args.stage or "", verdict, "CODE_SCORED", **fields)
        ge.emit(event, ge.Config.for_project(root))
    except Exception as err:  # logging never blocks scoring
        print(f"bmad-guard: warning: score not recorded ({type(err).__name__})", file=sys.stderr)


def json_pairs(value: str, name: str) -> list:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, list) or not all(isinstance(pair, list) and len(pair) == 2 for pair in parsed):
        raise argparse.ArgumentTypeError(f"{name} must be a JSON list of [threshold, value] pairs")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file-path", required=True, help="Path to the file being scored (recorded as doc_path)")

    parser.add_argument("--tests-passed", type=int, default=0, help="Passing tests touching this file (default: 0)")
    parser.add_argument("--tests-failed", type=int, default=0, help="Failing tests touching this file (default: 0)")
    parser.add_argument(
        "--no-tests-score",
        type=float,
        default=50,
        help="Test sub-score used when no tests touch this file (default: %(default)s)",
    )

    parser.add_argument("--lint-errors", type=int, default=0, help="Lint/static-analysis error count (default: 0)")
    parser.add_argument("--lint-warnings", type=int, default=0, help="Lint/static-analysis warning count (default: 0)")
    parser.add_argument(
        "--error-penalty", type=float, default=20, help="Points deducted per lint error (default: %(default)s)"
    )
    parser.add_argument(
        "--warning-penalty", type=float, default=5, help="Points deducted per lint warning (default: %(default)s)"
    )

    parser.add_argument(
        "--lines-changed", type=int, default=0, help="Lines added+removed for this file in the diff (default: 0)"
    )
    parser.add_argument(
        "--file-size",
        type=int,
        default=None,
        help="Line count of the file; auto-counted from --file-path if omitted",
    )

    parser.add_argument(
        "--revision-cycles",
        type=int,
        default=None,
        help="Explicit revision cycle count; overrides state-file resolution",
    )
    parser.add_argument("--doc-status", default=None, help="Explicit doc_status; overrides state-file resolution")
    parser.add_argument(
        "--state-file", default=None, help="Path to shared approval-state.json for resolving revision cycles/doc_status"
    )
    parser.add_argument("--doc-type", default=None, help="State-file key for the current run, e.g. 'story-3.2'")

    parser.add_argument(
        "--test-weight", type=float, default=40, help="Weight for the test signal (default: %(default)s)"
    )
    parser.add_argument(
        "--lint-weight", type=float, default=25, help="Weight for the lint signal (default: %(default)s)"
    )
    parser.add_argument(
        "--diff-weight", type=float, default=20, help="Weight for the diff-size signal (default: %(default)s)"
    )
    parser.add_argument(
        "--revision-weight", type=float, default=15, help="Weight for the revision-cycle signal (default: %(default)s)"
    )

    parser.add_argument(
        "--diff-ratio-cutoffs",
        type=lambda v: json_pairs(v, "--diff-ratio-cutoffs"),
        default=DEFAULT_DIFF_RATIO_CUTOFFS,
        help="JSON list of [max_ratio, score] pairs, ascending (default: %(default)s)",
    )
    parser.add_argument(
        "--revision-cycle-cutoffs",
        type=lambda v: json_pairs(v, "--revision-cycle-cutoffs"),
        default=DEFAULT_REVISION_CYCLE_CUTOFFS,
        help="JSON list of [max_cycles, score] pairs, ascending (default: %(default)s)",
    )
    parser.add_argument(
        "--label-cutoffs",
        type=lambda v: json_pairs(v, "--label-cutoffs"),
        default=DEFAULT_LABEL_CUTOFFS,
        help="JSON list of [min_score, label] pairs (default: %(default)s)",
    )

    parser.add_argument("--stage", default="", help="Lifecycle stage for the event, e.g. dev, stories")
    parser.add_argument("--project-root", default=None, help="Project root (default: git top level of --file-path)")
    parser.add_argument("--no-event", action="store_true", help="Do not record a guard event (dry runs)")
    parser.add_argument("-o", "--output", help="Write result JSON here instead of stdout")
    args = parser.parse_args(argv)

    try:
        validate(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = compute(args)
    if not args.no_event:
        emit_score_event(result, args)

    output_text = json.dumps(result)
    if args.output:
        Path(args.output).write_text(output_text, encoding="utf-8")
    else:
        print(output_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
