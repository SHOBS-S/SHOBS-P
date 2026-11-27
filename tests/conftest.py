"""2.3.5: the labelled-button questions (clearv_app.ask_choice) never wait for a click under test.

Many tests stand messagebox.askyesno in with a mock to answer SHOBS-P's questions. Several of those questions now use
ask_choice (buttons that say what they do, with the safe one as the default), which opens a real window and would
wait forever. This stand-in answers them the way the tests already answer: when the test has mocked askyesno, its
Yes picks the first non-safe button (the old Yes) and its No the safe one; otherwise the safe button, as Enter or
Escape would."""
import os
from unittest import mock

import pytest


@pytest.fixture(autouse=True, scope="session")
def _ask_choice_without_a_window():
    try:
        import clearv_app as ca
    except Exception:
        yield
        return

    def answer(parent, title, message, choices, safe, cancel=None):
        ask = ca.messagebox.askyesno
        if isinstance(ask, mock.Mock):
            if ask(title, message):
                return next((v for v, _t in choices if v != safe), safe)
        return safe

    with mock.patch.object(ca, "ask_choice", answer):
        yield


@pytest.fixture(autouse=True, scope="session")
def _settings_file_of_its_own(tmp_path_factory):
    """2.4.0 step 6c: the tests never read or write the user's own ~/.shobs_p_settings.json. A test that set the
    Mount to alt-az saved it there, and every later App (in this run, or the next) started alt-az, which failed two
    drift tests run on their own (and would change a tester's real settings)."""
    try:
        import clearv_app as ca
    except Exception:
        yield
        return
    path = str(tmp_path_factory.mktemp("settings") / ".shobs_p_settings.json")
    with mock.patch.object(ca, "SETTINGS_FILE", path):
        yield


@pytest.fixture(autouse=True)
def _settings_start_fresh(_settings_file_of_its_own):
    """step 6c: and what one test saved (an alt-az Mount) does not reach the next test's new App."""
    yield
    try:
        import clearv_app as ca
        path = ca.SETTINGS_FILE
        if os.path.exists(path) and os.path.basename(os.path.dirname(path)).startswith("settings"):
            os.remove(path)   # only the session's own temp file (a test's own patched file is left to it)
    except Exception:
        pass


@pytest.fixture(autouse=True, scope="session")
def _aperture_question_without_a_window():
    """2.4.0 fit-in F4 (m143): the telescope aperture question is a box with a number field now, which would wait
    for a key under test. Answered as the tests already answer the old Yes/No: a mocked askyesno's Yes confirms the
    value shown, its No (or no mock) is Cancel. A test of the box itself patches App._ask_aperture_mm on its own."""
    try:
        import clearv_app as ca
    except Exception:
        yield
        return

    def answer(self, text, cur):
        ask = ca.messagebox.askyesno
        if isinstance(ask, mock.Mock):
            return float(cur) if ask(ca.APP_TITLE, text) else None
        return None

    with mock.patch.object(ca.App, "_ask_aperture_mm", answer):
        yield
