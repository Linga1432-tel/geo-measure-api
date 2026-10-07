import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
MAX_UNZIPPED_BYTES = int(os.getenv("MAX_UNZIPPED_MB", "200")) * 1024 * 1024
ALLOWED_EXTENSIONS = {".zip", ".kml"}
