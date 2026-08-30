from google_health_viewer import __version__
from google_health_viewer.branding import APP_NAME, REPOSITORY_URL, SUPPORT_URL


def test_public_branding_and_version():
    assert APP_NAME == "VitalChronicle"
    assert __version__ == "1.0.0"
    assert REPOSITORY_URL == "https://github.com/SebRoLENS/google-health-dashboard-ai"
    assert SUPPORT_URL == "https://buymeacoffee.com/sebromi"
