"""AC10: Setup script.

Tests verify:
- setup validates Python version
- Idempotent (safe to run twice)
- Creates required directories (paper_trading/, etc.)
"""

import os
import sys
import pytest

from paper_trading.setup_paper_trading import SetupValidator


class TestPythonVersionValidation:
    """Setup validates Python version."""

    def test_accepts_current_python(self):
        validator = SetupValidator()
        result = validator.validate_python_version()
        # Current Python should be valid (we're running it)
        assert result["valid"] is True

    def test_rejects_python_27(self):
        validator = SetupValidator()
        result = validator.validate_python_version(version_tuple=(2, 7, 18))
        assert result["valid"] is False

    def test_rejects_python_36(self):
        validator = SetupValidator()
        result = validator.validate_python_version(version_tuple=(3, 6, 15))
        assert result["valid"] is False

    def test_accepts_python_310(self):
        validator = SetupValidator()
        result = validator.validate_python_version(version_tuple=(3, 10, 0))
        assert result["valid"] is True

    def test_accepts_python_311(self):
        validator = SetupValidator()
        result = validator.validate_python_version(version_tuple=(3, 11, 0))
        assert result["valid"] is True

    def test_validation_result_includes_version_info(self):
        validator = SetupValidator()
        result = validator.validate_python_version()
        assert "version" in result
        assert "min_version" in result


class TestIdempotency:
    """Setup is idempotent (safe to run twice)."""

    def test_setup_twice_succeeds(self, tmp_path):
        validator = SetupValidator(base_dir=str(tmp_path))
        result1 = validator.run_setup()
        result2 = validator.run_setup()
        assert result1["success"] is True
        assert result2["success"] is True

    def test_setup_creates_required_dirs(self, tmp_path):
        import os
        validator = SetupValidator(base_dir=str(tmp_path))
        validator.run_setup()
        # Should create paper_trading/ and other required directories
        paper_trading_dir = os.path.join(str(tmp_path), "paper_trading")
        assert os.path.isdir(paper_trading_dir), (
            f"Expected paper_trading/ directory to be created at {paper_trading_dir}"
        )

    def test_second_setup_does_not_overwrite_existing(self, tmp_path):
        import os
        validator = SetupValidator(base_dir=str(tmp_path))
        validator.run_setup()
        # Create a marker file
        marker = tmp_path / "marker.txt"
        marker.write_text("test")
        # Run setup again
        validator.run_setup()
        # Marker should still exist
        assert marker.exists()
        assert marker.read_text() == "test"

    def test_setup_result_has_success_key(self, tmp_path):
        validator = SetupValidator(base_dir=str(tmp_path))
        result = validator.run_setup()
        assert "success" in result
