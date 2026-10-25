import io
import json
import sys
import types

import pytest
from PIL import Image

import app as civic_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    instance_dir = tmp_path / "instance"
    upload_dir = instance_dir / "private_uploads"
    instance_dir.mkdir()
    upload_dir.mkdir()
    monkeypatch.setattr(civic_app, "INSTANCE_DIR", instance_dir)
    monkeypatch.setattr(civic_app, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(civic_app, "DATABASE_PATH", instance_dir / "test.sqlite3")
    civic_app.initialize_database()
    civic_app.app.config.update(TESTING=True)
    with civic_app.app.test_client() as test_client:
        yield test_client


def image_bytes(format="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), color=(35, 120, 100)).save(buffer, format=format)
    return buffer.getvalue()


def demo_post(client, data=None, files=None):
    form_data = {"mode": "demo", **(data or {})}
    if files:
        form_data["images"] = files
    return client.post("/api/analyze", data=form_data, content_type="multipart/form-data")


def test_dashboard_and_history_routes_render(client):
    dashboard_pages = [
        client.get("/"),
        client.get("/dashboard"),
    ]
    for response in dashboard_pages:
        assert response.status_code == 200
        assert b"NagrikShield" in response.data
        assert b'href="/dashboard"' in response.data
        assert b'id="dashboard-view" class="page-view" >' in response.data
        assert b'id="history-view" class="page-view" hidden>' in response.data

    history_page = client.get("/history")
    assert history_page.status_code == 200
    assert b'href="/dashboard#safety-note"' in history_page.data
    assert b'id="dashboard-view" class="page-view" hidden>' in history_page.data
    assert b'id="history-view" class="page-view" >' in history_page.data
    assert client.get("/api/history").json == {"reports": [], "total": 0}


def test_frontend_assets_are_served_by_flask(client):
    css = client.get("/static/css/style.css")
    javascript = client.get("/static/js/app.js")
    assert css.status_code == 200
    assert b"--canvas: #07121D" in css.data
    assert javascript.status_code == 200
    assert b'fetch("/api/history")' in javascript.data


def test_empty_form_is_rejected(client):
    response = client.post("/api/analyze", data={"mode": "demo"})
    assert response.status_code == 400
    assert "Add a description" in response.json["error"]


def test_text_only_submission_creates_clearly_labelled_demo_report(client):
    response = demo_post(client, {"description": "A damaged road surface"})
    assert response.status_code == 200
    report = response.json["report"]
    assert report["mode_label"] == "DEMO — no AI analysis"
    assert "No AI model analyzed" in report["explanation"]
    assert report["severity"] == "Unknown"
    assert report["risk_score"] == 0


def test_image_only_and_image_plus_text_submissions(client):
    image_only = demo_post(
        client,
        files=[(io.BytesIO(image_bytes()), "road.png")],
    )
    assert image_only.status_code == 200
    assert image_only.json["report"]["image_count"] == 1
    assert "does not inspect them" in image_only.json["report"]["visible_observations"][0]

    combined = demo_post(
        client,
        {"description": "There is water over the road"},
        [(io.BytesIO(image_bytes("JPEG")), "road.jpg")],
    )
    assert combined.status_code == 200
    assert combined.json["report"]["description"] == "There is water over the road"
    assert combined.json["report"]["image_count"] == 1


def test_unsupported_corrupted_and_oversized_images_rejected(client):
    unsupported = demo_post(
        client,
        files=[(io.BytesIO(b"not an image"), "picture.gif")],
    )
    assert unsupported.status_code == 400
    assert "PNG, JPG, JPEG, or WEBP" in unsupported.json["error"]

    corrupted = demo_post(
        client,
        files=[(io.BytesIO(b"broken image data"), "picture.png")],
    )
    assert corrupted.status_code == 400
    assert "not a valid" in corrupted.json["error"]

    mismatched = demo_post(
        client,
        files=[(io.BytesIO(image_bytes("PNG")), "picture.jpg")],
    )
    assert mismatched.status_code == 400
    assert "extension does not match" in mismatched.json["error"]

    oversized = demo_post(
        client,
        files=[(io.BytesIO(b"x" * (8 * 1024 * 1024 + 1)), "large.jpg")],
    )
    assert oversized.status_code == 400
    assert "8 MB or smaller" in oversized.json["error"]


def test_missing_api_key_returns_explicit_live_mode_error(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.post(
        "/api/analyze",
        data={"mode": "live", "description": "Road hazard"},
    )
    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json["error"]


def install_fake_google(monkeypatch, *, text=None, error=None):
    class FakeFiles:
        def upload(self, file):
            return {"uploaded_path": file}

    class FakeModels:
        def generate_content(self, model, contents):
            assert model == civic_app.MODEL_ID
            assert contents
            if error:
                raise error
            return types.SimpleNamespace(text=text)

    fake_genai = types.SimpleNamespace(
        Client=lambda api_key: types.SimpleNamespace(files=FakeFiles(), models=FakeModels())
    )
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)


def test_invalid_credentials_return_safe_error(client, monkeypatch):
    class CredentialsRejected(Exception):
        status_code = 403

    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-real-key")
    install_fake_google(monkeypatch, error=CredentialsRejected("permission denied"))
    response = client.post(
        "/api/analyze",
        data={"mode": "live", "description": "Road hazard"},
    )
    assert response.status_code == 502
    assert "Google rejected" in response.json["error"]
    assert "test-not-a-real-key" not in response.get_data(as_text=True)


def test_api_failure_returns_error_without_saving_fake_analysis(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    install_fake_google(monkeypatch, error=RuntimeError("service unavailable"))
    response = client.post(
        "/api/analyze",
        data={"mode": "live", "description": "Road hazard"},
    )
    assert response.status_code == 502
    assert "could not complete" in response.json["error"]
    assert client.get("/api/history").json["total"] == 0


def test_api_timeout_is_reported(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    class TimedOutFuture:
        def add_done_callback(self, callback):
            callback(self)

        def result(self, timeout):
            assert timeout == civic_app.AI_TIMEOUT_SECONDS
            raise civic_app.FutureTimeout()

    class FakeExecutor:
        def submit(self, _function):
            return TimedOutFuture()

    monkeypatch.setattr(civic_app, "executor", FakeExecutor())
    response = client.post(
        "/api/analyze",
        data={"mode": "live", "description": "Road hazard"},
    )
    assert response.status_code == 504
    assert "too long" in response.json["error"]


def test_malformed_model_json_uses_explicit_safe_fallback(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    install_fake_google(monkeypatch, text="This is not JSON")
    response = client.post(
        "/api/analyze",
        data={"mode": "live", "description": "Road hazard"},
    )
    assert response.status_code == 200
    report = response.json["report"]
    assert report["used_fallback"] is True
    assert report["severity"] == "Unknown"
    assert report["risk_score"] == 0
    assert report["mode_label"] == "Gemma 4 — response fallback"


def test_score_is_computed_by_bounded_explainable_server_rubric(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    model_output = {
        "hazard_category": "Open drain",
        "visible_observations": ["A gap is visible."],
        "possible_risks": ["A person could fall."] * 8,
        "severity": "Critical",
        "risk_score": 999999,
        "risk_factors": ["Visible opening"] * 8,
        "missing_evidence": ["Wider view"],
        "recommended_actions": ["Keep away."],
        "explanation": "A visible gap is present.",
        "uncertainty": "Depth is not established.",
    }
    install_fake_google(monkeypatch, text=json.dumps(model_output))
    response = client.post(
        "/api/analyze",
        data={"mode": "live", "description": "An open drain"},
    )
    report = response.json["report"]
    assert response.status_code == 200
    assert report["risk_score"] == 100
    assert "capped at 100" in report["risk_score_basis"]


def test_multilingual_text_survives_validation_and_pdf_generation(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    model_output = {
        "hazard_category": "Road",
        "visible_observations": ["రోడ్డు పక్కన గుంత కనిపిస్తోంది."],
        "possible_risks": [],
        "severity": "Moderate",
        "risk_factors": ["Visible road damage"],
        "missing_evidence": [],
        "recommended_actions": ["దూరంగా ఉండండి."],
        "explanation": "ఇది ప్రాథమిక అంచనా మాత్రమే.",
        "uncertainty": "చిత్రం ఒక్కటితో లోతు తెలియదు.",
    }
    install_fake_google(monkeypatch, text=json.dumps(model_output, ensure_ascii=False))
    response = client.post(
        "/api/analyze",
        data={"mode": "live", "language": "te", "description": "గుంత ఉంది"},
    )
    assert response.status_code == 200
    report = response.json["report"]
    assert report["explanation"] == "ఇది ప్రాథమిక అంచనా మాత్రమే."
    pdf = client.get(report["pdf_url"])
    assert pdf.status_code == 200
    assert pdf.mimetype == "application/pdf"
    assert pdf.data.startswith(b"%PDF")


def test_demo_pdf_history_and_input_limits(client):
    response = demo_post(
        client,
        {"description": "<script>alert(1)</script>"},
        [(io.BytesIO(image_bytes()), "road.png")],
    )
    report = response.json["report"]
    assert client.get(report["pdf_url"]).status_code == 200
    history = client.get("/api/history").json
    assert history["total"] == 1
    assert history["reports"][0]["reference"] == report["reference"]

    long_description = client.post(
        "/api/analyze",
        data={"mode": "demo", "description": "x" * 2001},
    )
    assert long_description.status_code == 400
    assert "2000 characters" in long_description.json["error"]


@pytest.mark.parametrize("language", ["en", "te", "hi", "ta", "kn", "mr", "ur", "bn"])
def test_all_supported_language_selections_are_accepted_in_demo(client, language):
    response = client.post(
        "/api/analyze",
        data={"mode": "demo", "language": language, "description": "Road report"},
    )
    assert response.status_code == 200
    assert response.json["report"]["language"] == language
