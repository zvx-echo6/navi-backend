"""navi-offroute API blueprint — faithful port of recon's offroute routes.

  POST /api/offroute       off-network effort-based routing (OffrouteRouter)
  GET  /api/mvum           MVUM road/trail access lookup (MVUMReader)

Both public (no auth), matching recon. Same request/response shapes and status
codes. Ported from recon's lib/api.py:api_offroute / api_mvum.
"""
import logging
import re

from flask import Blueprint, request, jsonify

from .router import OffrouteRouter
from .mvum import MVUMReader

logger = logging.getLogger('navi_offroute.route')

bp = Blueprint('offroute', __name__)

VALID_MODES = ("auto", "foot", "mtb", "atv", "vehicle")
VALID_BOUNDARY_MODES = ("strict", "pragmatic", "emergency")


@bp.route("/api/offroute", methods=["POST"])
def api_offroute():
    """
    Off-network routing from wilderness to destination.

    Request body:
        {start:[lat,lon], end:[lat,lon],
         mode: auto|foot|mtb|atv|vehicle (default foot),
         boundary_mode: strict|pragmatic|emergency (default pragmatic)}

    Response: {status:"ok", route:<GeoJSON FeatureCollection>, summary:{...}}
    or {status:"error", message}. 400 on bad input / router error, 500 on uncaught.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON body provided"}), 400

        start = data.get("start")
        end = data.get("end")
        if not start or not end:
            return jsonify({"status": "error", "message": "Missing start or end coordinates"}), 400
        if not isinstance(start, (list, tuple)) or len(start) != 2:
            return jsonify({"status": "error", "message": "start must be [lat, lon]"}), 400
        if not isinstance(end, (list, tuple)) or len(end) != 2:
            return jsonify({"status": "error", "message": "end must be [lat, lon]"}), 400

        start_lat, start_lon = float(start[0]), float(start[1])
        end_lat, end_lon = float(end[0]), float(end[1])

        mode = data.get("mode", "foot")
        if mode not in VALID_MODES:
            return jsonify({"status": "error", "message": "mode must be auto, foot, mtb, atv, or vehicle"}), 400

        boundary_mode = data.get("boundary_mode", "pragmatic")
        if boundary_mode not in VALID_BOUNDARY_MODES:
            return jsonify({"status": "error", "message": "boundary_mode must be strict, pragmatic, or emergency"}), 400

        router = OffrouteRouter()
        try:
            result = router.route(
                start_lat=start_lat, start_lon=start_lon,
                end_lat=end_lat, end_lon=end_lon,
                mode=mode, boundary_mode=boundary_mode,
            )
        finally:
            router.close()

        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result)

    except Exception as e:
        logger.exception("Offroute error")
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/mvum", methods=["GET"])
def api_mvum():
    """MVUM (Motor Vehicle Use Map) access near a point. Roads first, then trails.

    GET /api/mvum?lat=&lon=&radius=  (radius default 50 m)
    Returns {status:"ok", feature:{...}|null}. 400 missing coords, 500 on uncaught.
    """
    try:
        lat = request.args.get("lat", type=float)
        lon = request.args.get("lon", type=float)
        radius = request.args.get("radius", 50, type=float)

        if lat is None or lon is None:
            return jsonify({"status": "error", "message": "lat and lon required"}), 400

        reader = MVUMReader()
        try:
            feature = reader.query_nearest(lat, lon, radius, "mvum_roads")
            if feature is None:
                feature = reader.query_nearest(lat, lon, radius, "mvum_trails")
            if feature is None:
                return jsonify({"status": "ok", "feature": None})

            access = {
                "passenger_vehicle": {"status": feature.get("passengervehicle"),
                                      "dates": feature.get("passengervehicle_datesopen")},
                "high_clearance": {"status": feature.get("highclearancevehicle"),
                                   "dates": feature.get("highclearancevehicle_datesopen")},
                "atv": {"status": feature.get("atv"), "dates": feature.get("atv_datesopen")},
                "motorcycle": {"status": feature.get("motorcycle"),
                               "dates": feature.get("motorcycle_datesopen")},
                "4wd_gt50": {"status": feature.get("fourwd_gt50inches"),
                             "dates": feature.get("fourwd_gt50_datesopen")},
                "2wd_gt50": {"status": feature.get("twowd_gt50inches"),
                             "dates": feature.get("twowd_gt50_datesopen")},
                "e_bike_class1": {"status": feature.get("e_bike_class1"),
                                  "dates": feature.get("e_bike_class1_dur")},
                "e_bike_class2": {"status": feature.get("e_bike_class2"),
                                  "dates": feature.get("e_bike_class2_dur")},
                "e_bike_class3": {"status": feature.get("e_bike_class3"),
                                  "dates": feature.get("e_bike_class3_dur")},
            }

            maint_level = feature.get("operationalmaintlevel", "")
            maint_num = None
            if maint_level:
                match = re.match(r"(\d+)", maint_level)
                if match:
                    maint_num = int(match.group(1))

            result = {
                "id": feature.get("id"),
                "name": feature.get("name"),
                "forest": feature.get("forestname"),
                "district": feature.get("districtname"),
                "surface": feature.get("surfacetype"),
                "maintenance_level": maint_num,
                "seasonal": feature.get("seasonal"),
                "symbol": feature.get("symbol"),
                "trail_class": feature.get("trailclass"),
                "trail_system": feature.get("trailsystem"),
                "access": access,
                "geometry": feature.get("geojson"),
            }
            return jsonify({"status": "ok", "feature": result})
        finally:
            reader.close()

    except Exception as e:
        logger.exception("MVUM query error")
        return jsonify({"status": "error", "message": str(e)}), 500
