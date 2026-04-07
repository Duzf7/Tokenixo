# -*- mode: python ; coding: utf-8 -*-

import tiktoken
import tokenizers
import os

tiktoken_path = os.path.dirname(tiktoken.__file__)
tokenizers_path = os.path.dirname(tokenizers.__file__)

a = Analysis(
    ['Tokenixo.py'],
    pathex=[],
    binaries=[],
    datas=[
        (tiktoken_path, 'tiktoken'),
        (tokenizers_path, 'tokenizers'),
    ],
    hiddenimports=['tiktoken', 'tokenizers'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Tokenixo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/Tokenixo.icns',
)

app = BUNDLE(
    exe,
    name='Tokenixo.app',
    icon='assets/Tokenixo.icns',
    bundle_identifier='com.duzf7.tokenixo',
)
