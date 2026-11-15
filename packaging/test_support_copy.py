"""Packaging privacy regression using a synthetic public/private source tree."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'build_windows.py'
spec = importlib.util.spec_from_file_location('chuddy_build_windows', SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SupportCopyPrivacy(unittest.TestCase):
    def test_private_support_files_are_not_copied(self):
        with tempfile.TemporaryDirectory(prefix='chuddy-copy-regression-') as folder:
            root = Path(folder) / 'source'
            out = Path(folder) / 'bundle'
            out.mkdir()
            public = ['pi/engine.py', 'pi/config.simulator.json', 'pi/config.physical-template.json',
                      'friends/service.py', 'extension/manifest.json', 'docs/API.md',
                      'requirements.txt', 'requirements-audio.txt']
            private = ['pi/config.local.json', 'pi/service.env', 'pi/other.env', 'pi/.env',
                       'pi/secret.env.backup', 'pi/data.sqlite3', 'pi/data.sqlite3-wal',
                       'pi/data.sqlite3-shm', 'friends/scores.db-wal', 'friends/nested/token.key',
                       'docs/runtime/trace.txt', 'docs/recordings/voice.wav', 'docs/private/notes.md',
                       'pi/__pycache__/engine.pyc', 'pi/settings.json', 'friends/credentials.json',
                       'docs/training-data/reference.wav', 'docs/gpt-sovits-training/captions.txt',
                       'pi/checkpoints/voice.ckpt', 'friends/private-model.safetensors']
            for name in public + private + ['chuddy/' + name for name in builder.SUPPORT_WORKERS]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture', encoding='utf-8')
            with patch.object(builder, 'ROOT', root):
                builder.copy_support(out)
            for name in public:
                self.assertTrue((out / name).is_file(), name)
            for name in private:
                self.assertFalse((out / name).exists(), name)
            builder.assert_public_bundle(out)

    def test_existing_private_destination_fails_instead_of_silently_shipping(self):
        with tempfile.TemporaryDirectory(prefix='chuddy-copy-existing-') as folder:
            target = Path(folder)
            (target / 'pi').mkdir()
            (target / 'pi' / 'config.local.json').write_text('private', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'config.local.json'):
                builder.copy_support(target)

    def test_public_pythonnet_runtime_does_not_allow_user_runtime_captures(self):
        with tempfile.TemporaryDirectory(prefix='chuddy-copy-runtime-') as folder:
            target = Path(folder)
            public = target / '_internal' / 'pythonnet' / 'runtime' / 'Python.Runtime.dll'
            public.parent.mkdir(parents=True)
            public.write_text('fixture', encoding='utf-8')
            builder.assert_public_bundle(target)
            capture = target / 'docs' / 'runtime' / 'capture.txt'
            capture.parent.mkdir(parents=True)
            capture.write_text('private', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'runtime'):
                builder.assert_public_bundle(target)


if __name__ == '__main__':
    unittest.main()
