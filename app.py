from flask import Flask
from flask_cors import CORS
from routes import auth_bp, users_bp, units_bp
from routes.poi_routes import poi_bp

app = Flask(__name__)
CORS(app)

app.register_blueprint(auth_bp)
app.register_blueprint(users_bp)
app.register_blueprint(units_bp)
app.register_blueprint(poi_bp)


@app.route("/", methods=["GET"])
def health_check():
    return {"message": "API CentralGPS funcionando correctamente"}, 200


if __name__ == "__main__":
    app.run(debug=True)