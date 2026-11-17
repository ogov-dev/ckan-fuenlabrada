"""Los scripts de migración y conectores no son paquetes: se añaden al path."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("migration", "connectors/transparencia"):
    sys.path.insert(0, str(ROOT / sub))
