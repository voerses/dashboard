"""
Setup Paper Trading — Idempotent environment setup and validation.

Validates Python version, creates required directories, and ensures
the paper trading environment is ready to run.
"""

import os
import sys


class SetupValidator:
    """Validates and sets up the paper trading environment.

    Idempotent: safe to run multiple times without side effects.
    """

    MIN_PYTHON_VERSION = (3, 9)
    REQUIRED_DIRS = [
        "paper_trading",
        "paper_trading/logs",
        "paper_trading/data",
    ]

    def __init__(self, base_dir: str = "."):
        self.base_dir = base_dir

    def validate_python_version(self, version_tuple: tuple = None) -> dict:
        """Validate Python version meets minimum requirements.

        Args:
            version_tuple: Override version for testing (e.g., (3, 11, 0))

        Returns:
            Dict with 'valid' (bool), 'version' (str), 'min_version' (str)
        """
        if version_tuple is None:
            version_tuple = sys.version_info[:3]

        major, minor = version_tuple[0], version_tuple[1]
        min_major, min_minor = self.MIN_PYTHON_VERSION

        valid = (major, minor) >= (min_major, min_minor)

        return {
            "valid": valid,
            "version": f"{version_tuple[0]}.{version_tuple[1]}.{version_tuple[2] if len(version_tuple) > 2 else 0}",
            "min_version": f"{min_major}.{min_minor}",
        }

    def run_setup(self) -> dict:
        """Run full environment setup.

        Creates required directories and validates the environment.
        Idempotent — safe to run multiple times.

        Returns:
            Dict with 'success' (bool) and details
        """
        created_dirs = []

        for rel_dir in self.REQUIRED_DIRS:
            full_path = os.path.join(self.base_dir, rel_dir)
            if not os.path.exists(full_path):
                os.makedirs(full_path, exist_ok=True)
                created_dirs.append(rel_dir)

        python_check = self.validate_python_version()

        return {
            "success": True,
            "python_valid": python_check["valid"],
            "python_version": python_check["version"],
            "created_dirs": created_dirs,
        }
