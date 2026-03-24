from flask import Blueprint, jsonify, request
from services.poi_service import (
    get_pois,
    create_poi,
    get_poi_groups,
    create_poi_group,
    get_clients,
)

poi_bp = Blueprint("poi", __name__)


@poi_bp.route("/pois", methods=["GET"])
def list_pois():
    try:
        search = request.args.get("search", "").strip()
        return jsonify(get_pois(search if search else None)), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/pois", methods=["POST"])
def save_poi():
    try:
        data = request.get_json()

        required = ["nombre", "tipo_poi", "tipo_elemento"]
        missing = [field for field in required if not data.get(field)]

        if missing:
            return jsonify({
                "error": "Faltan campos requeridos",
                "missingFields": missing
            }), 400

        payload = {
            "id_empresa": 2,
            "tipo_elemento": data.get("tipo_elemento"),
            "id_elemento": data.get("id_elemento", 0),
            "nombre": data.get("nombre"),
            "direccion": data.get("direccion", ""),
            "tipo_poi": data.get("tipo_poi"),
            "tipo_marker": data.get("tipo_marker", 0),
            "url_marker": data.get("url_marker", "pin.svg"),
            "marker_path": data.get("marker_path", "MAP_PIN"),
            "marker_color": data.get("marker_color", "#5e6383"),
            "icon": data.get("icon", "la la-industry"),
            "icon_color": data.get("icon_color", "#FFFFFF"),
            "lat": data.get("lat"),
            "lng": data.get("lng"),
            "radio": data.get("radio", 50),
            "bounds": data.get("bounds", ""),
            "area": data.get("area", ""),
            "radio_color": data.get("radio_color", "#5e6383"),
            "polygon_path": data.get("polygon_path", ""),
            "polygon_color": data.get("polygon_color", "#5e6383"),
            "observaciones": data.get("observaciones", ""),
            "id_grupo_pois": data.get("id_grupo_pois", []),
            "id_usuario_registro": 2,
        }

        result = create_poi(payload)

        return jsonify({
            "message": "POI creado correctamente",
            "poi": result
        }), 201

    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/poi-groups", methods=["GET"])
def list_poi_groups():
    try:
        search = request.args.get("search", "").strip()
        return jsonify(get_poi_groups(search if search else None)), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/poi-groups", methods=["POST"])
def save_poi_group():
    try:
        data = request.get_json()

        if not data.get("nombre"):
            return jsonify({"error": "El nombre es requerido"}), 400

        payload = {
            "id_empresa": 2,
            "id_cliente": data.get("id_cliente", 0),
            "nombre": data.get("nombre"),
            "observaciones": data.get("observaciones", ""),
            "id_usuario_registro": 2,
            "is_default": data.get("is_default", 0),
        }

        result = create_poi_group(payload)
        return jsonify({"message": "Grupo creado correctamente", "group": result}), 201
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/clients", methods=["GET"])
def list_clients():
    try:
        return jsonify(get_clients()), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500