from __future__ import annotations

import os
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-password")
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("BAYES_LIVE_DECISION_MODE", "false")
