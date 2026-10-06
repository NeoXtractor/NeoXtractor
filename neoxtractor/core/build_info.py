"""Provides build information."""

import datetime

try:
    from build._build_info import BUILD_INFO  # type: ignore
except ImportError:
    BUILD_INFO = None


class _BuildInfo:
    """Class to hold build information."""

    @property
    def is_release(self) -> bool:
        """Check if the build is a release version."""
        return BUILD_INFO is not None and BUILD_INFO["version"] is not None

    @property
    def version(self) -> str | None:
        """Get the version of the build."""
        if BUILD_INFO is not None:
            return BUILD_INFO["version"]
        return None

    @property
    def build_time(self) -> datetime.datetime | None:
        """Get the build time."""
        if BUILD_INFO is not None:
            return datetime.datetime.fromtimestamp(BUILD_INFO["build_time"])
        return None

    @property
    def commit_hash(self) -> str | None:
        """Get the commit hash."""
        if BUILD_INFO is not None:
            return BUILD_INFO.get("commit_hash")
        return None

    @property
    def branch(self) -> str | None:
        """Get the branch name."""
        if BUILD_INFO is not None:
            return BUILD_INFO.get("branch")
        return None


BuildInfo = _BuildInfo()
