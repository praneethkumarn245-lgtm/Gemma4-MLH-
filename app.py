import io
import json
import os
import re
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image as PillowImage, UnidentifiedImageError
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as PdfImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from werkzeug.exceptions import RequestEntityTooLarge

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = INSTANCE_DIR / "private_uploads"
DATABASE_PATH = INSTANCE_DIR / "nagrikshield.sqlite3"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 5
MAX_PIXELS = 20_000_000
AI_TIMEOUT_SECONDS = 70
MODEL_ID = "gemma-4-26b-a4b-it"
SUPPORTED_LANGUAGES = {
    "en": "English",
    "te": "Telugu",
    "hi": "Hindi",
    "ta": "Tamil",
    "kn": "Kannada",
    "mr": "Marathi",
    "ur": "Urdu",
    "bn": "Bengali",
}
SEVERITY_BASE = {"Low": 15, "Moderate": 35, "High": 60, "Critical": 80, "Unknown": 0}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGES * MAX_IMAGE_BYTES + 2 * 1024 * 1024
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gemma")
ai_slots = threading.BoundedSemaphore(2)

INSTANCE_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)


class ImageValidationError(ValueError):
    pass


class AIServiceError(RuntimeError):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def get_db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_db() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS reports (
                reference TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                image_paths TEXT NOT NULL
            )"""
        )


initialize_database()


def clean_text(value, field, limit, required=False):
    value = (value or "").strip()
    if required and not value:
        raise ValueError(f"{field} is required.")
    if len(value) > limit:
        raise ValueError(f"{field} must be {limit} characters or fewer.")
    return value


def process_images(files, report_reference):
    uploads = [item for item in files if item and item.filename]
    if len(uploads) > MAX_IMAGES:
        raise ImageValidationError(f"Upload no more than {MAX_IMAGES} images.")

    report_dir = UPLOAD_DIR / report_reference
    stored_paths = []
    report_dir.mkdir()
    for upload in uploads:
        raw = upload.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ImageValidationError("Each image must be 8 MB or smaller.")
        if not raw:
            raise ImageValidationError("An uploaded image is empty.")
        extension = Path(upload.filename).suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ImageValidationError("Use a PNG, JPG, JPEG, or WEBP image.")
        try:
            with PillowImage.open(io.BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ImageValidationError("Use a PNG, JPG, JPEG, or WEBP image.")
                expected_format = {
                    ".png": "PNG",
                    ".jpg": "JPEG",
                    ".jpeg": "JPEG",
                    ".webp": "WEBP",
                }[extension]
                if image.format != expected_format:
                    raise ImageValidationError(
                        "The file extension does not match the image content."
                    )
                if image.width * image.height > MAX_PIXELS:
                    raise ImageValidationError("Image dimensions are too large to process safely.")
                image.verify()
            with PillowImage.open(io.BytesIO(raw)) as image:
                image.thumbnail((1800, 1800))
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                elif image.mode == "L":
                    image = image.convert("RGB")
                filename = f"{secrets.token_hex(12)}.jpg"
                path = report_dir / filename
                image.save(path, format="JPEG", quality=88, optimize=True)
                stored_paths.append(path)
        except ImageValidationError:
            raise
        except (
            UnidentifiedImageError,
            OSError,
            PillowImage.DecompressionBombError,
        ) as exc:
            raise ImageValidationError(
                "One of the files is not a valid, readable image."
            ) from exc
    return stored_paths


def fallback_analysis(language_name):
    return {
        "hazard_category": "Unclear from the submitted evidence",
        "visible_observations": [],
        "possible_risks": [],
        "severity": "Unknown",
        "risk_score": 0,
        "risk_factors": [],
        "risk_score_basis": "Unknown evidence receives 0 in the preliminary rubric.",
        "missing_evidence": ["A clear image or a more specific description"],
        "recommended_actions": [
            "Do not approach a potentially dangerous area.",
            "Ask a person to review the report before taking action.",
        ],
        "explanation": (
            "The AI response could not be safely interpreted. No hazard or severity "
            "has been confirmed."
        ),
        "uncertainty": (
            f"Analysis could not be validated. The requested {language_name} wording "
            "was not independently verified; this fallback is in English."
        ),
        "human_review_required": True,
    }


def extract_json(text):
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s*```$", "", candidate)
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start = candidate.find("{")
        if start < 0:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate[start:])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def safe_list(value, limit=8, item_limit=300):
    if not isinstance(value, list):
        return []
    return [
        item.strip()[:item_limit]
        for item in value
        if isinstance(item, str) and item.strip()
    ][:limit]


def normalize_analysis(model_data):
    severities = {item.lower(): item for item in SEVERITY_BASE}
    severity = model_data.get("severity")
    severity = severities.get(severity.strip().lower(), "Unknown") if isinstance(severity, str) else "Unknown"
    observations = safe_list(model_data.get("visible_observations"))
    risks = safe_list(model_data.get("possible_risks"))
    factors = safe_list(model_data.get("risk_factors"), limit=3)
    risk_score = min(
        100,
        max(
            0,
            SEVERITY_BASE[severity]
            + min(3, len(risks)) * 5
            + min(3, len(factors)) * 3,
        ),
    )
    basis = (
        f"{SEVERITY_BASE[severity]} severity base + "
        f"{min(3, len(risks))} possible-risk item(s) x 5 + "
        f"{min(3, len(factors))} listed factor(s) x 3; capped at 100. "
        "This is a preliminary prioritization rubric, not a probability."
    )

    def get_text(key, default, limit=800):
        value = model_data.get(key)
        return value.strip()[:limit] if isinstance(value, str) and value.strip() else default

    return {
        "hazard_category": get_text("hazard_category", "Unclear from the submitted evidence", 120),
        "visible_observations": observations,
        "possible_risks": risks,
        "severity": severity,
        "risk_score": risk_score,
        "risk_factors": factors,
        "risk_score_basis": basis,
        "missing_evidence": safe_list(model_data.get("missing_evidence")),
        "recommended_actions": safe_list(model_data.get("recommended_actions")),
        "explanation": get_text(
            "explanation",
            "The model did not provide a usable explanation.",
        ),
        "uncertainty": get_text(
            "uncertainty",
            "The model did not provide an uncertainty explanation.",
        ),
        "human_review_required": True,
    }


def gemma_analysis(prompt, image_paths, language_name):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AIServiceError(
            "Live AI is unavailable because GEMINI_API_KEY is not configured. "
            "Choose clearly labelled Demo mode or configure the key on the server.",
            503,
        )
    if not ai_slots.acquire(blocking=False):
        raise AIServiceError("The analysis service is busy. Please try again shortly.", 503)

    def call_model():
        from google import genai

        client = genai.Client(api_key=api_key)
        uploaded_files = [client.files.upload(file=str(path)) for path in image_paths]
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=[*uploaded_files, prompt],
        )
        return response.text

    future = executor.submit(call_model)
    future.add_done_callback(lambda _future: ai_slots.release())
    try:
        response_text = future.result(timeout=AI_TIMEOUT_SECONDS)
    except FutureTimeout as exc:
        raise AIServiceError(
            "The AI service took too long to respond. Please retry later.",
            504,
        ) from exc
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        message = str(exc).lower()
        if status == 429 or "429" in message or "resource_exhausted" in message:
            raise AIServiceError(
                "The AI service is rate-limited or out of quota. Please try later.",
                503,
            ) from exc
        if status in {401, 403} or "401" in message or "403" in message or "permission_denied" in message:
            raise AIServiceError(
                "Google rejected the API credentials or model access. Check the server key and API access.",
                502,
            ) from exc
        raise AIServiceError(
            "The AI service could not complete this analysis. No report was marked as AI-reviewed.",
            502,
        ) from exc

    parsed = extract_json(response_text)
    if parsed is None:
        return fallback_analysis(language_name), True
    return normalize_analysis(parsed), False


def make_prompt(form_data, image_count):
    language = SUPPORTED_LANGUAGES[form_data["language"]]
    return f"""
You are assisting with a preliminary civic-hazard report. Analyze only the provided
citizen description and {image_count} image(s), if any. Never invent measurements,
exact locations, injuries, causes, dates, legal findings, or authority decisions.
Separate visible evidence from possible inference. Images cannot establish structural
integrity, electrical status, contamination, or exact severity. Use severity Unknown
when evidence is insufficient. Recommend keeping away from danger. Require human review.
Return ONLY one JSON object with these exact fields:
hazard_category (string), visible_observations (array of strings describing only
visible/submitted evidence), possible_risks (array of strings clearly framed as
possibilities), severity (Low, Moderate, High, Critical, or Unknown), risk_factors
(array of up to 3 short evidence-linked strings), missing_evidence (array of strings),
recommended_actions (array of safe, non-emergency-response instructions),
explanation (string), uncertainty (string).
Write the explanation and uncertainty in {language}; keep category and severity labels
in English. If reliable phrasing in that language is uncertain, say so in uncertainty
and use English rather than presenting an unverified translation. Do not include a
risk score; the application calculates a transparent preliminary rubric separately.
Citizen description: {form_data["description"] or "(none supplied)"}
Additional observations: {form_data["observations"] or "(none supplied)"}
User-entered location (unverified): {form_data["location"] or "(not supplied)"}
User-entered date/time (unverified): {form_data["occurred_at"] or "(not supplied)"}
""".strip()


def demo_analysis(form_data, image_count):
    evidence = []
    if form_data["description"]:
        evidence.append("A citizen-provided description was submitted.")
    if image_count:
        evidence.append(f"{image_count} image(s) were attached; demo mode does not inspect them.")
    if form_data["observations"]:
        evidence.append("Additional citizen observations were submitted.")
    return {
        "hazard_category": "Demo example — not classified",
        "visible_observations": evidence,
        "possible_risks": [],
        "severity": "Unknown",
        "risk_score": 0,
        "risk_factors": [],
        "risk_score_basis": "Demo mode does not calculate an AI hazard score.",
        "missing_evidence": ["A live AI analysis or human review is needed."],
        "recommended_actions": [
            "Keep a safe distance from any suspected hazard.",
            "For an immediate threat, move to safety and contact appropriate local emergency services or civic authorities.",
        ],
        "explanation": "This is a clearly labelled demo response. No AI model analyzed the report.",
        "uncertainty": "Demo mode does not inspect images, classify hazards, or verify translations.",
        "human_review_required": True,
    }


def persist_report(reference, created_at, payload, image_paths):
    relative_paths = [str(path.relative_to(INSTANCE_DIR)) for path in image_paths]
    with get_db() as connection:
        connection.execute(
            "INSERT INTO reports (reference, created_at, payload, image_paths) VALUES (?, ?, ?, ?)",
            (reference, created_at, json.dumps(payload), json.dumps(relative_paths)),
        )


def load_report(reference):
    with get_db() as connection:
        row = connection.execute(
            "SELECT reference, created_at, payload, image_paths FROM reports WHERE reference = ?",
            (reference,),
        ).fetchone()
    if row is None:
        return None
    return {
        "reference": row["reference"],
        "created_at": row["created_at"],
        "payload": json.loads(row["payload"]),
        "image_paths": json.loads(row["image_paths"]),
    }


def render_pdf(report):
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#0D5960")))
    styles.add(ParagraphStyle(name="SmallBody", parent=styles["BodyText"], fontSize=9, leading=13))
    story = [
        Paragraph("NagrikShield AI — Civic Hazard Report", styles["ReportTitle"]),
        Spacer(1, 10),
    ]
    payload = report["payload"]
    rows = [
        ("Report reference", report["reference"]),
        ("Generated", report["created_at"]),
        ("Analysis mode", payload["mode_label"]),
        ("Description", payload["description"] or "Not provided"),
        ("Location (citizen-entered, unverified)", payload["location"] or "Not provided"),
        ("Date/time (citizen-entered, unverified)", payload["occurred_at"] or "Not provided"),
        ("Category", payload["hazard_category"]),
        ("Severity", payload["severity"]),
        ("Preliminary risk score", f'{payload["risk_score"]}/100'),
        ("Score basis", payload["risk_score_basis"]),
    ]
    for label, value in rows:
        story.append(Paragraph(f"<b>{escape(label)}:</b> {escape(str(value))}", styles["SmallBody"]))
        story.append(Spacer(1, 5))

    for title, key in [
        ("Visible / submitted observations", "visible_observations"),
        ("Possible risks (inferences)", "possible_risks"),
        ("Risk factors used by the rubric", "risk_factors"),
        ("Missing evidence", "missing_evidence"),
        ("Recommended safe next steps", "recommended_actions"),
    ]:
        story.append(Paragraph(f"<b>{escape(title)}</b>", styles["Heading3"]))
        items = payload.get(key) or ["None provided."]
        for item in items:
            story.append(Paragraph(f"• {escape(str(item))}", styles["SmallBody"]))
        story.append(Spacer(1, 6))

    for label in ("explanation", "uncertainty", "observations"):
        text = payload.get(label, "")
        if text:
            title = {
                "explanation": "Explanation",
                "uncertainty": "Uncertainty",
                "observations": "Additional citizen observations",
            }[label]
            story.append(Paragraph(f"<b>{title}</b>", styles["Heading3"]))
            story.append(Paragraph(escape(str(text)), styles["SmallBody"]))
            story.append(Spacer(1, 6))

    for relative_path in report["image_paths"]:
        path = (INSTANCE_DIR / relative_path).resolve()
        if path.is_file() and UPLOAD_DIR.resolve() in path.parents:
            try:
                story.extend([Paragraph("Submitted image (thumbnail)", styles["Heading3"]), PdfImage(str(path), width=3.1 * inch, height=2.1 * inch, kind="proportional"), Spacer(1, 8)])
            except (OSError, ValueError):
                story.append(Paragraph("An image thumbnail could not be added.", styles["SmallBody"]))

    story.extend(
        [
            Spacer(1, 10),
            Paragraph(
                "<b>Human review required.</b> This preliminary prioritization aid is not "
                "a calibrated probability, official risk determination, emergency service, "
                "or proof that a report was sent to any authority. Images and descriptions "
                "may be incomplete or incorrect. For immediate danger, move to safety and "
                "contact appropriate local emergency services or civic authorities.",
                styles["SmallBody"],
            ),
        ]
    )
    document.build(story)
    buffer.seek(0)
    return buffer


@app.get("/")
@app.get("/dashboard")
@app.get("/history")
def dashboard():
    active_page = "history" if request.path == "/history" else "dashboard"
    return render_template("index.html", active_page=active_page)


@app.get("/api/history")
def history_api():
    with get_db() as connection:
        rows = connection.execute(
            "SELECT reference, created_at, payload FROM reports ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    entries = []
    for row in rows:
        payload = json.loads(row["payload"])
        entries.append(
            {
                "reference": row["reference"],
                "created_at": row["created_at"],
                "hazard_category": payload["hazard_category"],
                "severity": payload["severity"],
                "risk_score": payload["risk_score"],
                "mode_label": payload["mode_label"],
            }
        )
    return jsonify({"reports": entries, "total": len(entries)})


@app.post("/api/analyze")
def analyze_api():
    form_data = {
        "description": "",
        "observations": "",
        "location": "",
        "occurred_at": "",
        "language": request.form.get("language", "en"),
    }
    image_paths = []
    persisted = False
    report_reference = f"NS-{secrets.token_hex(5).upper()}"
    try:
        form_data["description"] = clean_text(
            request.form.get("description"), "Description", 2000
        )
        form_data["observations"] = clean_text(
            request.form.get("observations"), "Additional observations", 1000
        )
        form_data["location"] = clean_text(request.form.get("location"), "Location", 200)
        form_data["occurred_at"] = clean_text(
            request.form.get("occurred_at"), "Date/time", 40
        )
        if form_data["language"] not in SUPPORTED_LANGUAGES:
            raise ValueError("Choose a supported explanation language.")
        if not any(form_data[key] for key in ("description", "observations")) and not any(
            item and item.filename for item in request.files.getlist("images")
        ):
            raise ValueError("Add a description, observations, or at least one image.")
        image_paths = process_images(request.files.getlist("images"), report_reference)
        mode = request.form.get("mode", "live")
        if mode == "demo":
            analysis = demo_analysis(form_data, len(image_paths))
            used_fallback = False
            mode_label = "DEMO — no AI analysis"
        elif mode == "live":
            analysis, used_fallback = gemma_analysis(
                make_prompt(form_data, len(image_paths)),
                image_paths,
                SUPPORTED_LANGUAGES[form_data["language"]],
            )
            mode_label = (
                "Gemma 4 — response fallback"
                if used_fallback
                else f"Gemma 4 — {MODEL_ID}"
            )
        else:
            raise ValueError("Choose Live AI or Demo mode.")
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = {
            **analysis,
            **form_data,
            "mode_label": mode_label,
            "used_fallback": used_fallback,
            "image_count": len(image_paths),
        }
        persist_report(report_reference, created_at, payload, image_paths)
        persisted = True
        return jsonify(
            {
                "report": {
                    "reference": report_reference,
                    "created_at": created_at,
                    **payload,
                    "image_count": len(image_paths),
                    "pdf_url": f"/reports/{report_reference}.pdf",
                }
            }
        )
    except ImageValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except AIServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except RequestEntityTooLarge:
        return jsonify({"error": "The complete request is too large. Keep each image under 8 MB and upload at most five."}), 413
    finally:
        report_dir = UPLOAD_DIR / report_reference
        if not persisted and report_dir.exists():
            for path in report_dir.iterdir():
                if path.is_file():
                    path.unlink()
            report_dir.rmdir()


@app.get("/reports/<reference>.pdf")
def report_pdf(reference):
    if not re.fullmatch(r"NS-[A-F0-9]{10}", reference):
        abort(404)
    report = load_report(reference)
    if report is None:
        abort(404)
    return send_file(
        render_pdf(report),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"nagrikshield-{reference.lower()}.pdf",
    )


@app.errorhandler(RequestEntityTooLarge)
def handle_large_request(_error):
    return jsonify({"error": "The request is too large. Each image must be 8 MB or smaller; upload at most five."}), 413


if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLASK_PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
    )
