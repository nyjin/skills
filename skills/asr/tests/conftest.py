import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

FIXTURE = Path(__file__).parent / "fixtures" / "test_ko.wav"


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: MLX 모델을 실제로 돌리는 통합 테스트")
