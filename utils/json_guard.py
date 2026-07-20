"""
Guard global de Content-Type y JSON para requests con body.

El problema que resuelve
────────────────────────
41 rutas llaman request.get_json(silent=True) y 5 más lo llamaban sin
silent. Con silent=True, un body vacío o malformado produce data=None
sin excepción — y la ruta que hace data.get(...) truena con
AttributeError 500 en vez de responder un error claro al cliente.
Peor: un body JSON válido pero con raíz array o escalar ("[1,2]", "5")
también pasa silent=True y truena igual.

La solución: validar UNA vez, antes de cualquier ruta
─────────────────────────────────────────────────────
Un before_request centralizado garantiza que, cuando una ruta recibe
un body, ese body es SIEMPRE un objeto JSON válido. Después del guard,
request.get_json(silent=True) en cualquier ruta devuelve:
  - dict  → si el cliente mandó body (garantizado por el guard)
  - None  → solo si el cliente no mandó body (las rutas ya manejan
            ese caso con `or {}` o `if not data`)

Contrato de respuestas del guard:
  415 → Content-Type no soportado (ni JSON ni multipart)
  400 → Content-Type dice JSON pero el body no parsea, o la raíz
        no es un objeto

Por qué se permite multipart/form-data:
  unit_routes.py tiene un endpoint de subida de imagen. Ese endpoint
  valida su propio contenido (request.files) — el guard solo debe
  garantizar que nadie más reciba basura, no bloquear uploads.

Por qué NO se filtra por método (POST/PUT/PATCH):
  El criterio correcto es "¿trae body?", no "¿qué método es?". Un
  DELETE con body JSON debe validarse igual; un POST sin body (logout)
  debe pasar sin fricción. content_length es la señal honesta.
"""

import logging

from flask import Flask, jsonify, request

logger = logging.getLogger(__name__)


def registrar_guard_json(app: Flask) -> None:
    """
    Registra el before_request que valida el body de TODAS las rutas.

    Se llama una sola vez desde create_app(), después de CORS (para que
    los preflight OPTIONS ya estén resueltos por flask-cors) y antes de
    registrar blueprints por claridad de lectura — el orden real de
    ejecución lo dicta Flask, no la posición en el archivo.
    """

    @app.before_request
    def _validar_body_json():
        # Sin body no hay nada que validar: GET, HEAD, OPTIONS del
        # preflight CORS, y POSTs legítimos sin payload (p.ej. logout).
        if not request.content_length:
            return None

        # Uploads de archivos: el endpoint valida request.files por su
        # cuenta. Bloquearlos aquí rompería la subida de imágenes.
        if request.mimetype == "multipart/form-data":
            return None

        # is_json acepta application/json y variantes +json (RFC 6839).
        # Cualquier otro Content-Type con body (form-urlencoded, text,
        # binario) no tiene consumidor en esta API → 415 explícito en
        # vez de un 500 confuso más adelante.
        if not request.is_json:
            logger.warning(
                "Body rechazado por Content-Type '%s' en %s %s",
                request.mimetype,
                request.method,
                request.path,
            )
            return (
                jsonify(
                    {
                        "error": (
                            "Content-Type no soportado. "
                            "Usa application/json."
                        )
                    }
                ),
                415,
            )

        # Content-Type correcto pero body que no parsea (JSON roto,
        # encoding inválido). get_json cachea el resultado: las rutas
        # que lo llamen después NO pagan un segundo parseo.
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "El body no es JSON válido."}), 400

        # Raíz no-objeto: "[1,2]" o "5" son JSON válido pero ninguna
        # ruta de esta API los acepta — y data.get(...) sobre ellos es
        # AttributeError 500. Se rechaza aquí con mensaje claro. Si un
        # día un endpoint bulk necesita arrays, se le agrega una
        # exención explícita por path en este guard.
        if not isinstance(data, dict):
            return (
                jsonify({"error": "El body debe ser un objeto JSON."}),
                400,
            )

        return None