# PyInstaller one-folder Windows build. Large local model runtimes stay external.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).resolve().parent
datas = [
    (str(ROOT / 'web'), 'web'),
    (str(ROOT / 'inputs' / 'chuddy-reference.png'), 'inputs'),
    (str(ROOT / 'assets' / 'audio'), 'assets/audio'),
    (str(ROOT / 'assets' / 'icons'), 'assets/icons'),
]
# Optional workers run in their independently configured Python environments.
for worker in ('voice_worker.py', 'asr_worker.py', 'classifier_worker.py', 'audio_setup.py', 'worker_process.py'):
    datas.append((str(ROOT / 'chuddy' / worker), 'chuddy'))
datas += collect_data_files('webview')
binaries = []
for package in ('webview', 'pythonnet', 'clr_loader', '_sounddevice_data'):
    binaries += collect_dynamic_libs(package)

a = Analysis(
    [str(ROOT / 'run.py')], pathex=[str(ROOT)], binaries=binaries, datas=datas,
    hiddenimports=['webview.platforms.winforms', 'webview.platforms.edgechromium',
                   'pythonnet', 'clr_loader', 'sounddevice', '_sounddevice_data',
                   'pi.engine', 'scripts.setup_local_ai', 'chuddy.casino',
                   'chuddy.casino_tables', 'chuddy.casino_poker',
                   'chuddy.interception', 'chuddy.desktop_overlay'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tensorflow', 'tensorflow_hub', 'tf_keras', 'keras', 'torch', 'torchaudio',
              'transformers', 'faster_whisper', 'ctranslate2', 'scipy', 'sklearn',
              'matplotlib', 'pandas', 'cv2', 'IPython', 'pytest', 'PyQt5', 'PyQt6',
              'PySide2', 'PySide6', 'gi', 'gtk', 'cefpython3'],
    noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Chuddy', debug=False,
          icon=str(ROOT / 'assets' / 'icons' / 'chuddy-desktop.ico'),
          bootloader_ignore_signals=False, strip=False, upx=False, console=True,
          disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
          codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Chuddy')
