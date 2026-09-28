"""Offline eval quality gate (mirrors Go cmd/offerpilot-eval)."""
from __future__ import annotations

import json
import sys

from .corpus import json_schema, load_corpus_file, load_default_corpus
from .runner import Runner
from .schema import Corpus, Thresholds

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_INVALID = 2

_HELP = object()


class _Options:
    def __init__(self):
        self.corpus_path = ""
        self.pretty = True
        self.print_schema = False
        self.max_duplicate_rate = -1.0
        self.min_evidence_validity = -1.0
        self.min_mode_coverage = -1.0
        self.min_seniority_coverage = -1.0
        self.min_mode_seniority_coverage = -1.0
        self.min_mode_conformance = -1.0
        self.max_privacy_hits = -1


def main() -> None:
    sys.exit(run(sys.argv[1:], sys.stdout, sys.stderr))


def run(args, stdout, stderr) -> int:
    opts, err = parse_options(args)
    if err is _HELP:
        return EXIT_PASSED
    if err is not None:
        write_error(stderr, err)
        return EXIT_INVALID

    if opts.print_schema:
        schema = json_schema()
        stdout.write(schema.decode("utf-8") + "\n")
        return EXIT_PASSED

    try:
        corpus = load_corpus(opts.corpus_path)
    except Exception as exc:
        write_error(stderr, exc)
        return EXIT_INVALID
    try:
        thresholds = apply_overrides(corpus.thresholds, opts)
    except Exception as exc:
        write_error(stderr, exc)
        return EXIT_INVALID
    try:
        runner = Runner(thresholds)
    except Exception as exc:
        write_error(stderr, exc)
        return EXIT_INVALID
    try:
        report = runner.run(corpus)
    except Exception as exc:
        write_error(stderr, exc)
        return EXIT_INVALID

    write_report(stdout, report, opts.pretty)
    return EXIT_FAILED if not report.passed else EXIT_PASSED


_BOOL_FLAGS = {"-schema": "print_schema"}
_FLOAT_FLAGS = {
    "-max-duplicate-rate": "max_duplicate_rate",
    "-min-evidence-validity": "min_evidence_validity",
    "-min-mode-coverage": "min_mode_coverage",
    "-min-seniority-coverage": "min_seniority_coverage",
    "-min-matrix-coverage": "min_mode_seniority_coverage",
    "-min-mode-conformance": "min_mode_conformance",
}
_INT_FLAGS = {"-max-privacy-hits": "max_privacy_hits"}


def parse_options(args):
    opts = _Options()
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-h", "-help", "--help"):
            return None, _HELP

        if arg == "-pretty" or arg.startswith("-pretty="):
            opts.pretty = True
            if "=" in arg:
                opts.pretty = _parse_bool(arg.split("=", 1)[1])
            index += 1
            continue
        if arg == "-schema":
            opts.print_schema = True
            index += 1
            continue
        if arg == "-corpus" or arg.startswith("-corpus="):
            if "=" in arg:
                opts.corpus_path = arg.split("=", 1)[1]
                index += 1
            else:
                if index + 1 >= len(args):
                    return None, ValueError("-corpus requires a value")
                opts.corpus_path = args[index + 1]
                index += 2
            continue

        for flag, attribute in _FLOAT_FLAGS.items():
            if arg == flag or arg.startswith(flag + "="):
                value = _next_value(args, index, arg)
                if isinstance(value, Exception):
                    return None, value
                setattr(opts, attribute, _parse_float(value))
                index = _advance(args, index, arg)
                break
        else:
            for flag, attribute in _INT_FLAGS.items():
                if arg == flag or arg.startswith(flag + "="):
                    value = _next_value(args, index, arg)
                    if isinstance(value, Exception):
                        return None, value
                    setattr(opts, attribute, _parse_int(value))
                    index = _advance(args, index, arg)
                    break
            else:
                if arg.startswith("-"):
                    return None, ValueError(f"unknown flag: {arg}")
                return None, ValueError(f"unexpected positional arguments: {args[index:]}")
    return opts, None


def _advance(args, index, flag):
    return index + 1 if "=" in args[index] else index + 2


def _next_value(args, index, flag):
    if "=" in args[index]:
        return args[index].split("=", 1)[1]
    if index + 1 >= len(args):
        return ValueError(f"{flag} requires a value")
    return args[index + 1]


def _parse_bool(value):
    lowered = value.lower()
    if lowered in ("1", "t", "true"):
        return True
    if lowered in ("0", "f", "false"):
        return False
    raise ValueError(f"invalid boolean value {value!r}")


def _parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid number {value!r}")


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid integer {value!r}")


def load_corpus(path: str) -> Corpus:
    if not path:
        return load_default_corpus()
    return load_corpus_file(path)


def apply_overrides(thresholds: Thresholds, opts: _Options) -> Thresholds:
    for flag, attribute in _FLOAT_FLAGS.items():
        value = getattr(opts, attribute)
        if value == -1:
            continue
        if value < 0 or value > 1:
            raise ValueError(f"-{flag.lstrip('-')} must be between 0 and 1")
        setattr(thresholds, attribute, value)
    if opts.max_privacy_hits != -1:
        if opts.max_privacy_hits < 0:
            raise ValueError("-max-privacy-hits must be non-negative")
        thresholds.maxPrivacyMarkerHits = opts.max_privacy_hits
    return thresholds


def write_report(writer, report, pretty: bool) -> None:
    data = report.model_dump(mode="json", exclude_none=True)
    writer.write(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n")


def write_error(writer, err) -> None:
    writer.write(json.dumps({"error": str(err)}, ensure_ascii=False) + "\n")
