"""Optional cooperative cancellation/progress for read-only character scans."""

from core.process_memory import check_scan_memory


class CharacterScanCancelled(Exception):
    pass


def checkpoint(cancelled=None):
    if cancelled is not None and cancelled():
        raise CharacterScanCancelled()
    check_scan_memory()


def report_progress(progress, stage, current, total):
    if progress is not None:
        progress(stage, current, max(total, 1))
