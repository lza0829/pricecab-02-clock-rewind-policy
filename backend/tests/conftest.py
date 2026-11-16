from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# backend/ 加入导入路径（测试与源码分离，不放进 app 包）。
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# 会话级固定数据目录：必须在 import app.* 之前设置，app.config 导入时即读取 DATA_DIR。
_SESSION_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _SESSION_TMP.name


@pytest.fixture()
def client():
    from app.config import DB_PATH
    from app.seed import seed_if_empty
    from fastapi.testclient import TestClient
    from app.main import app

    # 每个测试用例前删掉库文件并重新播种，保证相互隔离。
    if Path(DB_PATH).exists():
        Path(DB_PATH).unlink()
    seed_if_empty()

    with TestClient(app) as c:
        yield c
