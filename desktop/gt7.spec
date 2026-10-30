# PyInstaller spec for the desktop app (#193). Build with
# `python desktop/scripts/build.py`, which runs this from a clean venv.
# Onedir only, never onefile: onefile unpacks to a temp folder on every
# start, which signing, Defender and the Local Network grant all dislike.
# ruff: noqa
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
BACKEND = ROOT / "backend"
APP = BACKEND / "app"
NAME = "gt7-datalogger"
sys.path[:0] = [str(ROOT / "desktop"), str(BACKEND)]

from gt7_desktop import APP_NAME, BUNDLE_ID  # noqa: E402

# Alembic loads env.py and each revision by path, so they ship as real files.
datas = [
    (str(p), str(Path("app") / p.parent.relative_to(APP)))
    for p in (APP / "migrations").rglob("*")
    if p.is_file() and p.suffix in (".py", ".mako") and "__pycache__" not in p.parts
]
datas += [(str(p), "app/data") for p in (APP / "data").glob("*.json")]
datas += [(str(ROOT / "frontend" / "dist"), "frontend/dist")]
datas += collect_data_files("certifi")
datas += copy_metadata("gt7-datalogger")

hiddenimports = [
    *collect_submodules("app"),
    *collect_submodules("gt7_desktop"),
    # Cython modules hide these from the freezer.
    *collect_submodules("zeroconf"),
    # SQLAlchemy loads the driver with __import__; Alembic's ops by name.
    "aiosqlite",
    *collect_submodules("alembic"),
    "ifaddr",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_sansio_impl",
    "uvicorn.lifespan.on",
]

excludes = [
    "uvloop", "httptools", "watchfiles", "yaml", "tkinter", "Crypto.SelfTest",
    # Never sqlalchemy.dialects.postgresql: alembic.ddl imports it, and every
    # migration would break in the frozen build.
    "sqlalchemy.dialects.mssql", "sqlalchemy.dialects.mysql", "sqlalchemy.dialects.oracle",
]

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT / "desktop"), str(BACKEND)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=sys.platform.startswith("linux"),
    upx=False,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)

if sys.platform == "darwin":
    from app._version import __version__

    import os

    run = os.environ.get("GITHUB_RUN_NUMBER", "0")
    major, minor, _patch = __version__.split(".")
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        bundle_identifier=BUNDLE_ID,
        version=__version__,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": f"{major}.{minor}.{run}",
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            "NSLocalNetworkUsageDescription": (
                "GT7 Datalogger sends a heartbeat to your PlayStation over the local "
                "network so it streams telemetry, and lets your phone find it."
            ),
            "NSBonjourServices": ["_gt7dl._tcp"],
        },
    )
