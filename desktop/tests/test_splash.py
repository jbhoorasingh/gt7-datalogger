"""The splash's start lights (gt7_desktop.splash): its timing, the controller
that drives a window, and when the app shows it."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gt7_desktop import main, paths, splash
from gt7_desktop.splash import GO, HOLD, LIGHTS, SLOW, STEP, Frame, Splash, frame_at


def test_four_lights_come_on_at_the_pace_and_wait_for_the_server() -> None:
    assert frame_at(0.0, None).lit == 0
    for i in range(1, LIGHTS):
        assert frame_at(i * STEP, None).lit == i
    # However long the server takes, the fifth light is its own.
    assert frame_at(30.0, None).lit == LIGHTS - 1
    assert not frame_at(30.0, None).go


def test_the_fifth_light_then_lights_out_and_the_green_flag() -> None:
    ready = 2.0
    assert frame_at(ready, ready) == Frame(LIGHTS, False, "Ready", False)
    out = ready + HOLD
    assert frame_at(out, ready) == Frame(0, True, "Opening the dashboard…", False)
    assert frame_at(out + GO, ready).done
    assert frame_at(out, ready).image == "go.png"
    assert frame_at(ready, ready).image == "lights-5.png"


def test_a_fast_server_still_gets_the_full_count() -> None:
    fifth = LIGHTS * STEP
    assert frame_at(0.2, 0.1).lit == 0  # ready already, but the lights still count
    assert frame_at(fifth - 0.01, 0.1).lit == LIGHTS - 1
    assert frame_at(fifth, 0.1).lit == LIGHTS
    assert frame_at(fifth + HOLD, 0.1).go


def test_a_slow_first_start_says_why() -> None:
    assert frame_at(SLOW - 0.1, None).status == "Starting the server…"
    assert "first start" in frame_at(SLOW, None).status


def test_every_frame_has_its_picture() -> None:
    names = {frame_at(t / 10, 1.0).image for t in range(60)} | {
        frame_at(t / 10, None).image for t in range(60)}
    assert names == {f"lights-{i}.png" for i in range(LIGHTS + 1)} | {"go.png"}
    for name in names:
        assert paths.icon(f"splash/{name}").is_file(), name


class FakeView:
    def __init__(self) -> None:
        self.shown: list[tuple[Frame, str]] = []
        self.frames: list[Frame] = []
        self.closed = 0

    def show(self, frame: Frame, version: str) -> None:
        self.shown.append((frame, version))

    def render(self, frame: Frame) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        self.closed += 1


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _splash() -> tuple[Splash, FakeView, Clock]:
    view, clock = FakeView(), Clock()
    s = Splash(view, "Version 1.0.1", clock=clock)
    s._run = lambda: None  # type: ignore[method-assign]  # driven by tick() here
    s.start()
    return s, view, clock


def test_the_controller_opens_the_dashboard_once_as_the_lights_go_out() -> None:
    s, view, clock = _splash()
    assert view.shown[0][1] == "Version 1.0.1"
    opened: list[float] = []
    start = clock.now
    clock.now = start + 1.0
    s.tick()
    s.ready(lambda: opened.append(clock.now))  # the server answers after 1 s
    clock.now = start + LIGHTS * STEP  # the fifth light, at the pace
    s.tick()
    assert view.frames[-1].lit == LIGHTS and opened == []
    clock.now += HOLD
    s.tick()
    assert view.frames[-1].go and len(opened) == 1
    s.tick()
    assert len(opened) == 1  # once
    clock.now += GO
    assert s.tick() is False
    assert view.closed == 1 and s.closed


def test_only_changes_reach_the_window() -> None:
    s, view, clock = _splash()
    s.tick()
    s.tick()
    assert view.frames == []
    clock.now += STEP
    s.tick()
    assert [f.lit for f in view.frames] == [1]


def test_failing_closes_it_at_once_and_only_once() -> None:
    s, view, _clock = _splash()
    s.fail()
    s.fail()
    assert view.closed == 1
    assert s.tick() is False


def test_a_dashboard_that_fails_to_open_still_closes_the_splash() -> None:
    s, view, clock = _splash()

    def boom() -> None:
        raise RuntimeError("no browser")

    s.ready(boom)
    clock.now += 10
    s.tick()
    clock.now += GO
    assert s.tick() is False and view.closed == 1


def _app(tmp_path: Path, argv: list[str]) -> main.DesktopApp:
    f = paths.folders(tmp_path)
    paths.create(f)
    return main.DesktopApp(main.parse(argv), f)


class ReadySup:
    def __init__(self) -> None:
        self.ready = threading.Event()
        self.ready.set()
        self.state = SimpleNamespace(phase="running", status={})
        self.port = 8000
        self.url = "http://127.0.0.1:8000/"

    def stop(self) -> None:
        pass


def test_the_browser_opens_through_the_splash(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(main, "open_browser", opened.append)
    app = _app(tmp_path, [])
    app.supervisor = ReadySup()
    told: list[Any] = []
    app.splash = SimpleNamespace(closed=False, ready=told.append, fail=lambda: None)  # type: ignore[assignment]
    app._after_ready()
    assert opened == [] and told == [app.open_dashboard]
    told[0]()
    assert opened == ["http://127.0.0.1:8000/"]


def test_without_a_splash_the_browser_opens_at_once(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(main, "open_browser", opened.append)
    app = _app(tmp_path, [])
    app.supervisor = ReadySup()
    app._after_ready()
    assert opened == ["http://127.0.0.1:8000/"]


@pytest.mark.parametrize(("argv", "shown"), [([], True), (["--background"], False)])
def test_the_splash_shows_when_opened_never_at_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], shown: bool,
) -> None:
    from gt7_desktop import ports

    made: list[bool] = []
    monkeypatch.setattr(splash, "make_view", lambda: made.append(True))

    def no_port(*_args: Any) -> Any:
        raise ports.NoPort("none")  # stop right after the splash would show

    monkeypatch.setattr(ports, "choose", no_port)
    app = _app(tmp_path, argv)
    try:
        assert app.start() == main.EXIT_STARTUP
    finally:
        app.lock.release()
    assert made == ([True] if shown else [])


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows window")
def test_the_windows_splash_window_shows_renders_and_closes() -> None:
    pytest.importorskip("PIL")
    from gt7_desktop import splash_windows

    view = splash_windows.WindowsSplashView()
    view.show(frame_at(0.0, None), "Version 1.0.1")
    try:
        assert view.hwnd
        import ctypes

        assert splash_windows.user32.IsWindowVisible(ctypes.c_void_p(view.hwnd))
        for t in (0.3, 0.6, 2.0, 2.5):
            view.render(frame_at(t, 1.0))
        picture = view.compose(frame_at(2.5, 1.0))
        assert picture.size == (round(640 * view.scale), round(360 * view.scale))
    finally:
        view.close()
    assert view.hwnd is None
