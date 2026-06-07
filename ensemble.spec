# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['run_app.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Include pyproject.toml for version info
        ('pyproject.toml', '.'),
        # Include migrations (SQL files needed at runtime)
        ('daemon/migrations', 'daemon/migrations'),
    ],
    hiddenimports=[
        # Tiktoken plugin system
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
        # FastAPI and Starlette (dynamic imports)
        'starlette',
        'starlette.routing',
        'starlette.middleware',
        'starlette.middleware.cors',
        'starlette.middleware.gzip',
        'starlette.responses',
        'starlette.staticfiles',
        'fastapi',
        'fastapi.applications',
        'fastapi.middleware',
        'uvicorn',
        'uvicorn.config',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # LangGraph
        'langgraph',
        'langgraph.graph',
        'langgraph.checkpoint',
        'langgraph.checkpoint.base',
        'langgraph.checkpoint.serde',
        'langgraph.constants',
        'langgraph.errors',
        'langgraph.pregel',
        # Pydantic and settings
        'pydantic',
        'pydantic.fields',
        'pydantic_settings',
        'pydantic_settings.main',
        # SQLModel and database
        'sqlmodel',
        'sqlmodel.main',
        'aiosqlite',
        # YAML
        'yaml',
        # Other dependencies
        'click',
        'httpx',
        'sse_starlette',
        # Agents module - explicitly import submodules
        'daemon',
        'daemon.api',
        'daemon.graph',
        'daemon.manager',
        'daemon.loader',
        'daemon.models',
        'daemon.config',
        'daemon.tools',
        'daemon.tools.bash',
        'daemon.tools.filesystem',
        'daemon.tools.instance',
        'daemon.sources',
        'daemon.sources.adapters',
        'daemon.sources.adapters.telegram',
        'daemon.sources.adapters.scheduler',
        # Migrations
        'daemon.migrations',
        'daemon.migrations.runner',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_tiktoken.py', 'rthook_ssl.py'],
    excludes=[
        'tkinter',
        'test',
        'pytest',
        'numpy',
        'pandas',
        'matplotlib',
        'scipy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ensemble-prod',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Keep console for logs/stderr
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=None,  # Optional: add version info
)
