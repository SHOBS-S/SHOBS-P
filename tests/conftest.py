"""2.3.5: the labelled-button questions (clearv_app.ask_choice) never wait for a click under test.

Many tests stand messagebox.askyesno in with a mock to answer SHOBS-P's questions. Several of those questions now use
ask_choice (buttons that say what they do, with the safe one as the default), which opens a real window and would
wait forever. This stand-in answers them the way the tests already answer: when the test has mocked askyesno, its
Yes picks the first non-safe button (the old Yes) and its No the safe one; otherwise the safe button, as Enter or
Escape would."""
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
