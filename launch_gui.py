"""Start POI Studio without installing anything or contacting external services."""
from pathlib import Path
import runpy, sys
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/'src'))
runpy.run_path(str(root/'gui'/'server.py'),run_name='__main__')
