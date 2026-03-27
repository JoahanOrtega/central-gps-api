from flask import Blueprint, jsonify, request
from services.unit_service import get_units, create_unit
from utils.auth_guard import jwt_required

units_bp = Blueprint("units", __name__)


@units_bp.route("/units", methods=["GET"])
@jwt_required
def list_units():
    try:
        search = request.args.get("search", "").strip()
        units = get_units(search=search if search else None)
        return jsonify(units), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@units_bp.route("/units", methods=["POST"])
def create_new_unit():
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "El cuerpo de la solicitud es requerido"}), 400

        required_fields = [
            "numero",
            "marca",
            "modelo",
            "anio",
            "matricula",
            "tipo",
            "odometro_inicial",
            "fecha_instalacion",
            "imei",
            "chip",
        ]

        missing_fields = [field for field in required_fields if not data.get(field)]

        if missing_fields:
            return (
                jsonify(
                    {
                        "error": "Faltan campos requeridos",
                        "missingFields": missing_fields,
                    }
                ),
                400,
            )

        payload = {
            "id_empresa": 2,
            "id_operador": data.get("id_operador"),
            "numero": data.get("numero"),
            "marca": data.get("marca"),
            "modelo": data.get("modelo"),
            "anio": data.get("anio"),
            "matricula": data.get("matricula"),
            "tipo": data.get("tipo"),
            "imagen": data.get("imagen", ""),
            "id_modelo_avl": data.get("id_modelo_avl"),
            "imei": data.get("imei"),
            "chip": data.get("chip"),
            "odometro_inicial": data.get("odometro_inicial"),
            "tipo_combustible": data.get("tipo_combustible"),
            "capacidad_tanque": data.get("capacidad_tanque"),
            "rendimiento_establecido": data.get("rendimiento_establecido"),
            "no_serie": data.get("no_serie"),
            "nombre_aseguradora": data.get("nombre_aseguradora"),
            "telefono_aseguradora": data.get("telefono_aseguradora"),
            "no_poliza_seguro": data.get("no_poliza_seguro"),
            "vigencia_poliza_seguro": data.get("vigencia_poliza_seguro"),
            "vigencia_verificacion_vehicular": data.get(
                "vigencia_verificacion_vehicular"
            ),
            "input1": data.get("input1"),
            "input2": data.get("input2"),
            "input3": data.get("input3"),
            "input4": data.get("input4"),
            "output1": data.get("output1"),
            "output2": data.get("output2"),
            "output3": data.get("output3"),
            "output4": data.get("output4"),
            "rs232": data.get("rs232"),
            "fecha_instalacion": data.get("fecha_instalacion"),
            "id_usuario_registro": 2,
            "status": 1,
        }

        result = create_unit(payload)

        return jsonify({"message": "Unidad creada correctamente", "unit": result}), 201

    except Exception as error:
        return jsonify({"error": str(error)}), 500
