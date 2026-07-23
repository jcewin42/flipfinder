import tempfile

from flipfinder.categories.outboard_motors import OutboardMotorProfile
from flipfinder.db import Database
from flipfinder.inference.base import InferenceBackend
from flipfinder.models import ListingDetail, Photo
from flipfinder.pipeline.feedback_store import FeedbackStore
from flipfinder.pipeline.valuation import evaluate_listing


class RecordingBackend(InferenceBackend):
    """Fake backend that just records what image_urls it was called with."""
    name = "recording"

    def __init__(self):
        self.last_image_urls = None

    def evaluate(self, prompt, image_urls=None):
        self.last_image_urls = list(image_urls or [])
        return '{"estimated_resale_value": 500, "estimated_repair_cost": 50, "estimated_repair_hours": 1.0, "confidence": 0.8, "reasoning": "ok"}'


def _detail_with_photos(n: int) -> ListingDetail:
    photos = [Photo(url=f"http://example.com/{i}.jpg") for i in range(n)]
    return ListingDetail(
        id="1", source="sociavault", category_id="outboard_motors",
        title="Yamaha 40hp outboard", description="runs great", price=500, url="u",
        photos=photos, attributes={}, seller={}, location={}, posted_at=None,
    )


def test_evaluate_listing_sends_only_configured_image_count():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db = Database(f.name)
        fs = FeedbackStore(db)
        category = OutboardMotorProfile(
            latitude=38.8, longitude=-77.3, radius_km=40,
            base_service_cost=150, image_count=2,
        )
        backend = RecordingBackend()
        detail = _detail_with_photos(5)  # listing has 5 photos, category only wants 2

        evaluate_listing(detail, category, fs, backend, db)

        assert len(backend.last_image_urls) == 2
        assert backend.last_image_urls == ["http://example.com/0.jpg", "http://example.com/1.jpg"]


def test_evaluate_listing_sends_all_photos_if_fewer_than_configured():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db = Database(f.name)
        fs = FeedbackStore(db)
        category = OutboardMotorProfile(
            latitude=38.8, longitude=-77.3, radius_km=40,
            base_service_cost=150, image_count=5,
        )
        backend = RecordingBackend()
        detail = _detail_with_photos(1)

        evaluate_listing(detail, category, fs, backend, db)

        assert len(backend.last_image_urls) == 1
