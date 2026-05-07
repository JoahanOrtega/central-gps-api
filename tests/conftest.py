"""
tests/conftest.py
================================================================================
Configuracion global de pytest.

El problema que resuelve:
  db/connection.py crea los pools de BD al nivel de modulo (al importarse).
  Cuando pytest importa workers/poi_worker.py o app.py, Python ejecuta
  ese codigo inmediatamente — antes de que cualquier mock o fixture tenga
  oportunidad de actuar.

  psycopg2 en Windows lee las variables de entorno con el encoding del
  sistema (WIN1252). Si DB_PASSWORD o cualquier variable contiene caracteres
  no-ASCII, falla con UnicodeDecodeError antes de conectarse.

  Solucion: mockear _make_main_pool y _make_telemetry_pool en db.connection
  ANTES de que se importe cualquier modulo del proyecto. sys.modules se
  parchea antes del import, por lo que el mock ya esta activo cuando
  db/connection.py intenta crear los pools.
"""

import os
import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# 1. Variables de entorno en ASCII puro — antes de cualquier import
# ---------------------------------------------------------------------------
# _require() en config.py lanza SystemExit si estas faltan.
# Usamos valores dummy ASCII — las conexiones reales estan mockeadas.

os.environ["SECRET_KEY"] = "a" * 128
os.environ["REFRESH_SECRET_KEY"] = "b" * 128
os.environ["DB_PASSWORD"] = "test_password"
os.environ["TELEMETRY_DB_PASSWORD"] = "test_password"
os.environ["DB_HOST"] = "127.0.0.1"
os.environ["DB_NAME"] = "test_db"
os.environ["DB_USER"] = "postgres"
os.environ["DB_PORT"] = "5432"
os.environ["TELEMETRY_DB_HOST"] = "127.0.0.1"
os.environ["TELEMETRY_DB_NAME"] = "test_telem"
os.environ["TELEMETRY_DB_USER"] = "postgres"
os.environ["TELEMETRY_DB_PORT"] = "5432"
os.environ["FLASK_TESTING"] = "true"
os.environ["WORKER_ENABLED"] = "false"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["WORKER_POLL_INTERVAL"] = "15"

# ---------------------------------------------------------------------------
# 2. Mockear los pools de BD antes de que db/connection.py se importe
# ---------------------------------------------------------------------------
# Creamos un mock del modulo db.connection antes de que Python lo cargue.
# Cualquier modulo que haga "from db.connection import ..." recibe el mock.

_mock_pool = MagicMock()
_mock_pool.getconn.return_value = MagicMock()
_mock_pool.putconn.return_value = None

_mock_connection_module = MagicMock()
_mock_connection_module.get_db_connection.return_value = MagicMock()
_mock_connection_module.release_db_connection.return_value = None
_mock_connection_module.get_db_telemetry_connection.return_value = MagicMock()
_mock_connection_module.release_db_telemetry_connection.return_value = None

# Inyectar el mock en sys.modules ANTES de que cualquier test importe
# modulos del proyecto que dependan de db.connection
sys.modules["db.connection"] = _mock_connection_module

# ---------------------------------------------------------------------------
# 3. Silenciar loggers ruidosos durante los tests
# ---------------------------------------------------------------------------
import logging

logging.getLogger("db.connection").setLevel(logging.CRITICAL)
logging.getLogger("apscheduler").setLevel(logging.CRITICAL)
logging.getLogger("werkzeug").setLevel(logging.CRITICAL)
