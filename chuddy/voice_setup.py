"""Explicit free local voice/ASR preparation; never captures or plays audio."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = '5de7a54aa4e5e2baadb0182dde554908b48b85c2'


def run(command):
    subprocess.run([str(x) for x in command], check=True, cwd=ROOT)


def python_at(folder):
    return folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-voice', action='store_true')
    parser.add_argument('--download-voice', choices=['turbo', 'nano'])
    parser.add_argument('--install-asr', action='store_true')
    parser.add_argument('--torch-wheel', type=Path)
    args = parser.parse_args()
    base = ROOT / '.chuddy'
    base.mkdir(exist_ok=True)
    if args.install_voice:
        environment = base / 'voice-venv'
        if not python_at(environment).exists():
            venv.create(environment, with_pip=True)
        python = python_at(environment)
        if args.torch_wheel:
            run([python, '-m', 'pip', 'install', args.torch_wheel.resolve()])
        else:
            run([python, '-m', 'pip', 'install', 'torch==2.9.1', '--index-url', 'https://download.pytorch.org/whl/cu128'])
        run([python, '-m', 'pip', 'install', '--no-deps', 'torchaudio==2.9.1', '--index-url', 'https://download.pytorch.org/whl/cu128'])
        run([python, '-m', 'pip', 'install', 'setuptools==80.9.0', 'numpy==1.26.4', 'librosa==0.11.0', 'transformers==5.2.0',
             'diffusers==0.29.0', 'safetensors==0.5.3', 'conformer==0.3.2', 'pykakasi==2.3.0', 'pyloudnorm==0.2.0',
             'omegaconf==2.4.0', 'spacy-pkuseg==1.0.2', 'einops==0.8.2', 'onnx==1.18.0', 'ml_dtypes==0.5.4', 'pre-commit'])
        run([python, '-m', 'pip', 'install', '--no-deps', 's3tokenizer==0.3.0', 'resemble-perth==1.0.1'])
        source = base / 'chatterbox-src'
        if not source.exists():
            run(['git', 'clone', 'https://github.com/resemble-ai/chatterbox.git', source])
        run(['git', '-C', source, 'checkout', SOURCE_COMMIT])
        # Upstream __init__ reads distribution metadata even when imported by path.
        run([python, '-m', 'pip', 'install', '--no-deps', '-e', source])
        print('Voice dependencies/source prepared. A compatible CUDA driver and model smoke test are still required.')
    if args.download_voice:
        python = python_at(base / 'voice-venv')
        model = 'chatterbox-' + args.download_voice
        # Default HTTP download; public model, no account/API key is required.
        script = "from huggingface_hub import snapshot_download; print(snapshot_download(" + repr('ResembleAI/' + model) + ",local_dir=" + repr(str(base / 'models' / model)) + ",allow_patterns=['ve.safetensors','s3gen_meanflow.safetensors','t3_*_v1.safetensors','*.json','*.txt','*.pt','*.model']))"
        run([python, '-c', script])
    if args.install_asr:
        environment = base / 'asr-venv'
        if not python_at(environment).exists():
            venv.create(environment, with_pip=True)
        python = python_at(environment)
        run([python, '-m', 'pip', 'install', 'faster-whisper==1.2.1', 'av==15.1.0'])
        run([python, '-c', "from huggingface_hub import snapshot_download; print(snapshot_download('Systran/faster-whisper-tiny.en',local_dir=" + repr(str(base / 'models' / 'whisper-tiny.en')) + '))'])
        (base / 'asr.json').write_text(json.dumps({'python': str(python), 'model_path': str(base / 'models' / 'whisper-tiny.en')}, indent=2), encoding='utf-8')
        print('CPU transcription prepared. No microphone has been opened.')
    if not any((args.install_voice, args.download_voice, args.install_asr)):
        parser.print_help()


if __name__ == '__main__':
    main()
