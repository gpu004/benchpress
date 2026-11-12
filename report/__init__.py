"""Benchpress report entry: build the model, then render artifacts."""

from report.model import build_report
from report.render import write_artifacts


def report(path):
    write_artifacts(build_report(path))
