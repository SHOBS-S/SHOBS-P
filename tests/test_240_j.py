"""2.4.0 step 3: MicroObservatory's elevation from its HEIGHT keyword (1268 m), read only at the MOBS site."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import photometry_core as core  # noqa: E402


def test_mobs_height_read():
    h = {"LATITUDE": 31.68, "LONGITUD": -110.88, "HEIGHT": 1268.0, "OBSERVAT": "Whipple Observatory"}
    assert core.header_site(h) == (31.68, -110.88, 1268.0)


def test_height_ignored_elsewhere():
    h = {"LATITUDE": 35.4, "LONGITUD": -81.9, "HEIGHT": 4176}
    assert core.header_site(h)[2] is None


def test_real_elevation_keyword_wins():
    h = {"LATITUDE": 31.68, "LONGITUD": -110.88, "HEIGHT": 1268.0, "SITEELEV": 1300.0}
    assert core.header_site(h)[2] == 1300.0


def test_mobs_without_height_unchanged():
    h = {"LATITUDE": 31.68, "LONGITUD": -110.88}
    assert core.header_site(h)[2] is None
