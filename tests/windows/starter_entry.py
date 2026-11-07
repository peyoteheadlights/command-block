"""What the live test's own copy of the starter service runs (an installed
copy runs ``mcsc.exe starter-service`` instead). Arguments: --name NAME
--settings FILE."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import starter

sys.exit(starter.run_service(sys.argv[1:]))
