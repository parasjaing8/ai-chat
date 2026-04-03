"""Add project root to sys.path so `import server` resolves correctly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
