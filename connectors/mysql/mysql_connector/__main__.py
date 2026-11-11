import logging
import os
from pathlib import Path

from connector_sdk import make_server

from . import config as configuration
from .server import manifest, routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
config = configuration.from_env()
key = Path(os.environ["CONNECTOR_KEY_FILE"]).read_text().strip()
make_server(manifest(config), routes(config), key, port=int(os.environ.get("PORT", "8080"))).serve_forever()
