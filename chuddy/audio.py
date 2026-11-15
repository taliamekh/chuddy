"""Explicit microphone capture and local classification. Playback may score too."""
import json
import math
import os
import queue
import subprocess
import tempfile
import threading
import time
import uuid
import base64
import sys
from collections import deque
from pathlib import Path
from .audio_events import SoundRuns, remove_effect_echo
from .worker_process import terminate_worker

ROOT = Path(__file__).resolve().parents[1]
PTT_FIRST_PCM_TIMEOUT = 1.5
PTT_BUFFER_SECONDS = 20


class AudioPipeline:
    def __init__(self, store, model_path):
        self.store, self.model_path = store, Path(model_path)
        self.data_dir = self.model_path.parent.parent
        self.lock, self.model_lock = threading.RLock(), threading.Lock()
        self.ptt_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.generation = 0
        self.stream = None
        self.input_ready = threading.Event()
        self.input_device = None
        self.status, self.error, self.peak = 'off', '', 0.0
        self.classifier_status = 'installed · not loaded' if (self.model_path / 'saved_model.pb').is_file() else 'not installed'
        self.model, self.last_prediction = None, None
        self.score_epoch = 0
        self.score_allowed = lambda: True
        self.effect_references = deque(maxlen=24)
        self.ptt, self.ptt_data, self.ptt_rate, self.ptt_status = False, deque(), 0, 'off'
        self.ptt_frames, self.ptt_buffered, self.ptt_started, self.ptt_first_pcm_ms = 0, 0, None, None
        self.ptt_owned_stream = False
        self.asr_process = None
        self.asr_lock = threading.Lock()
        self.conversation_sink = None
        self.classifier_process = None
        self.classifier_responses = queue.Queue()
        self.external = False

    def info(self):
        with self.ptt_lock:
            frames, first_pcm = self.ptt_frames, self.ptt_first_pcm_ms
        return {'status': self.status, 'recording': self.stream is not None, 'peak': round(self.peak, 3),
                'input_ready': self.stream is not None and self.input_ready.is_set(), 'input_device': self.input_device,
                'ptt_frames': frames, 'ptt_first_pcm_ms': first_pcm,
                'classifier': self.classifier_status, 'last_prediction': self.last_prediction,
                'raw_audio_saved': False, 'error': self.error, 'conversation': self.ptt_status,
                'transcription': 'local faster-whisper · CPU' if self._asr_paths()[0].is_file() else 'not installed'}

    def devices(self):
        try:
            import sounddevice as sd
            return [{'id': i, 'name': d['name']} for i, d in enumerate(sd.query_devices()) if d['max_input_channels'] > 0]
        except Exception:
            return []

    def _profile(self):
        profile = self.store.profile() if hasattr(self.store, 'profile') else {}
        return profile.get('id') if isinstance(profile, dict) else None

    def start(self):
        with self.lock:
            if self.stream is not None or self.status.startswith('starting'):
                return {'accepted': True}
            self.generation += 1
            self.stop_event = threading.Event()
            self.input_ready = threading.Event()
            self.input_device = None
            self.status, self.error = 'starting microphone', ''
            capture_only = bool(self.ptt and self.ptt_owned_stream)
            threading.Thread(target=self._run, args=(self.stop_event, self.generation, self._profile(), capture_only), daemon=True).start()
        return {'accepted': True}

    def cancel_scores(self):
        """Invalidate queued or in-flight scoring before slower device cleanup."""
        with self.lock:
            self.score_epoch += 1
            self.stop_event.set()

    def sound_context(self):
        with self.lock:
            return self.score_epoch, self._profile()

    def play_effect(self, voice, kind, source, cancel=None, context=None):
        """Bind one selected profile and ID to the adapter's playback callbacks."""
        if kind != 'fart':
            raise ValueError('Only fart sounds are available.')
        epoch, profile_id = context if context is not None else self.sound_context()
        event_id = 'speaker:' + str(uuid.uuid4())
        started = False

        def allowed():
            with self.lock:
                return (epoch == self.score_epoch and self.score_allowed() and self._profile() == profile_id
                        and not (cancel and cancel.is_set()))

        def on_start(path, start, duration):
            nonlocal started
            with self.lock:
                started = True
                self.effect_references.append((path, start, start + duration))

        def on_complete():
            with self.lock:
                if started and allowed():
                    self.store.event(event_id, kind, source, profile_id=profile_id, active_only=True)

        return voice.sound(kind, allowed=allowed, on_start=on_start, on_complete=on_complete,
                           admission=self.lock)

    def stop(self):
        with self.lock:
            self.stop_event.set()
            self.generation += 1
            stream, self.stream = self.stream, None
            with self.ptt_lock:
                self.ptt, self.ptt_status = False, 'off'
                self.ptt_data.clear()
                self.ptt_buffered = 0
            self.ptt_owned_stream = False
            self.conversation_sink = None
            if self.asr_process and self.asr_process.poll() is None:
                terminate_worker(self.asr_process)
            self.status, self.peak = 'off', 0.0
        if stream:
            try:
                stream.abort()
            finally:
                stream.close()

    def prepare(self):
        """Load already downloaded model, never download implicitly."""
        with self.model_lock:
            if self.model is not None or self.external:
                return True
            if not (self.model_path / 'saved_model.pb').is_file():
                self.classifier_status = 'not installed · meter/manual/test paths available'
                return False
            self.classifier_status = 'loading YAMNet on CPU'
            try:
                config_path = self.model_path.parents[1] / 'audio.json'
                if getattr(sys, 'frozen', False) and config_path.is_file():
                    config = json.loads(config_path.read_text(encoding='utf-8'))
                    if not Path(config.get('python', '')).is_file():
                        raise ValueError('External classifier Python is unavailable; prepare audio again.')
                    self.external = True
                    self.classifier_status = 'YAMNet external CPU worker · uncalibrated'
                    return True
                import tensorflow as tf
                try:
                    tf.config.set_visible_devices([], 'GPU')
                except RuntimeError:
                    pass
                self.model = tf.saved_model.load(str(self.model_path))
                self.classifier_status = 'YAMNet ready · uncalibrated'
                return True
            except Exception as error:
                self.classifier_status = 'YAMNet load failed · meter/manual/test paths available'
                self.error = str(error)[:200]
                return False

    def classify(self, samples, sample_rate):
        """Inference only: used by fixtures and Pi PCM; does not score itself."""
        import numpy as np
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float)) or not 8000 <= sample_rate <= 96000:
            raise ValueError('Sample rate must be 8000–96000 Hz.')
        waveform = np.asarray(samples, dtype=np.float32)
        if waveform.ndim == 2:
            waveform = waveform.mean(axis=1)
        if waveform.ndim != 1 or not int(sample_rate * .2) <= len(waveform) <= int(sample_rate * 15) or not np.isfinite(waveform).all():
            raise ValueError('Use 0.2–15 seconds of finite mono/stereo samples.')
        if not self.prepare():
            return {'available': False, 'error': self.classifier_status}
        if sample_rate != 16000:
            try:
                from scipy.signal import resample_poly
                factor = math.gcd(int(sample_rate), 16000)
                waveform = resample_poly(waveform, 16000 // factor, int(sample_rate) // factor).astype(np.float32)
            except ImportError:
                waveform = np.interp(np.arange(0, len(waveform), sample_rate / 16000), np.arange(len(waveform)), waveform).astype(np.float32)
        if len(waveform) < 15600:
            waveform = np.pad(waveform, (0, 15600 - len(waveform)))
        with self.model_lock:
            if self.external:
                scores = self._external_scores(waveform)
            else:
                scores, _, _ = self.model(waveform)
        scores = scores.numpy() if hasattr(scores, 'numpy') else np.asarray(scores)
        if scores.ndim != 2 or scores.shape[1] < 56 or not np.isfinite(scores).all():
            raise ValueError('Unexpected YAMNet score signature.')
        means = scores.mean(axis=0)
        # YAMNet's eructation class (53) is a competing negative for fart (55).
        confidence = float(means[55])
        result = {'available': True, 'kind': 'fart', 'confidence': round(confidence, 5),
                  'threshold': self.store.settings().get('audio_threshold', .35), 'calibrated': False}
        result['detected'] = confidence >= result['threshold'] and confidence > float(means[53])
        self.last_prediction = result
        return result

    def _external_scores(self, waveform):
        if self.classifier_process is None or self.classifier_process.poll() is not None:
            config = json.loads((self.model_path.parents[1] / 'audio.json').read_text(encoding='utf-8'))
            self.classifier_log = open(self.model_path.parents[1] / 'classifier-worker.log', 'a', encoding='utf-8')
            command = [config['python'], '-u', str(ROOT / 'chuddy' / 'classifier_worker.py'), '--model', str(self.model_path.resolve())]
            self.classifier_process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.classifier_log,
                text=True, encoding='utf-8', creationflags=0x08000000 if os.name == 'nt' else 0)
            self.classifier_responses = queue.Queue()
            def reader(process, responses):
                for line in process.stdout:
                    responses.put(line)
                responses.put(None)
            threading.Thread(target=reader, args=(self.classifier_process, self.classifier_responses), daemon=True).start()
        process = self.classifier_process
        request_id = str(uuid.uuid4())
        process.stdin.write(json.dumps({'id': request_id, 'float32': base64.b64encode(waveform.astype('<f4').tobytes()).decode('ascii')}) + '\n')
        process.stdin.flush()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                line = self.classifier_responses.get(timeout=.5)
            except queue.Empty:
                continue
            if line is None:
                break
            result = json.loads(line)
            if result.get('id') == request_id:
                if not result.get('ok'):
                    raise ValueError(result.get('error'))
                return result['scores']
        if process.poll() is None:
            terminate_worker(process)
        raise ValueError('External classifier exited or timed out. See classifier-worker.log.')

    def close(self):
        self.stop()
        if self.classifier_process and self.classifier_process.poll() is None:
            terminate_worker(self.classifier_process)
        if hasattr(self, 'classifier_log'):
            self.classifier_log.close()

    def ingest_pcm(self, pcm, sample_rate=16000, event_id=None, profile_id=None, cancel_event=None,
                   *, runs=None, start=0., references=(), admission_lock=None):
        import numpy as np
        if cancel_event is not None and cancel_event.is_set():
            return {'cancelled': True}
        if not isinstance(pcm, bytes) or len(pcm) > 1920000 or len(pcm) % 2:
            raise ValueError('Invalid 16-bit PCM chunk.')
        profile_id = profile_id or self._profile()
        waveform = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        result = self.detect_window(waveform, sample_rate, start, references)
        with admission_lock or self.lock:
            if (cancel_event is not None and cancel_event.is_set()) or not self.score_allowed():
                return {'cancelled': True}
            if result.get('kind') == 'fart' and (runs or SoundRuns()).new_event(result, start, len(waveform) / sample_rate):
                self.store.event(event_id or str(uuid.uuid4()), result['kind'], 'YAMNet Pi microphone · uncalibrated',
                                 result['confidence'], profile_id=profile_id, active_only=True)
        return result

    def detect_window(self, samples, rate, start=0., references=()):
        if references:
            samples = remove_effect_echo(samples, rate, start, references)
        return self.classify(samples, rate)

    def _run(self, stop, generation, profile_id, capture_only=False):
        stream = None
        published = False
        try:
            with self.lock:
                if stop.is_set() or generation != self.generation:
                    return
                epoch = self.score_epoch
                input_ready = self.input_ready
            import numpy as np
            import sounddevice as sd
            chunks = queue.Queue(maxsize=8)
            next_frame = 0

            def callback(data, frames, timing, status):
                nonlocal next_frame
                frame, next_frame = next_frame, next_frame + frames
                if (stop.is_set() or generation != self.generation or frames <= 0
                        or data.ndim != 2 or data.shape[0] != frames or not data.shape[1]):
                    return
                # Copy before returning the driver buffer. PTT keeps native
                # channels; scoring and continuous ASR still share mono PCM.
                samples = (data if capture_only and data.shape[1] > 1 else data[:, 0]).copy()
                if not np.isfinite(samples).all():
                    return
                with self.ptt_lock:
                    if stop.is_set() or generation != self.generation:
                        return
                    if self.ptt:
                        if self.ptt_first_pcm_ms is None:
                            self.ptt_first_pcm_ms = round((time.monotonic() - self.ptt_started) * 1000)
                            self.ptt_status = 'capturing · up to 20 seconds'
                        self.ptt_rate = rate
                        self.ptt_frames += frames
                        self.ptt_data.append(samples)
                        self.ptt_buffered += frames
                        limit = PTT_BUFFER_SECONDS * rate
                        while self.ptt_buffered > limit:
                            extra = self.ptt_buffered - limit
                            oldest = self.ptt_data[0]
                            if len(oldest) <= extra:
                                self.ptt_buffered -= len(self.ptt_data.popleft())
                            else:
                                self.ptt_data[0] = oldest[extra:].copy()
                                self.ptt_buffered -= extra
                    if capture_only:
                        self.peak = float(np.max(np.abs(samples)))
                    if not input_ready.is_set():
                        input_ready.set()
                if capture_only:
                    return
                sink = self.conversation_sink
                if sink is not None:
                    sink(samples, rate)
                try:
                    capture_start = time.monotonic() - frames / rate
                    if timing is not None and 0 < timing.inputBufferAdcTime <= timing.currentTime:
                        capture_start = time.monotonic() - (timing.currentTime - timing.inputBufferAdcTime)
                    chunks.put_nowait((frame, capture_start, samples))
                except queue.Full:
                    pass
            device = self.store.settings().get('audio_device')
            descriptor = sd.query_devices(device, 'input')
            rate = int(descriptor['default_samplerate'])
            channels = min(2, descriptor.get('max_input_channels', 1)) if capture_only else 1
            # Lower latency and variable host-sized blocks for a short held key.
            # Keep the established buffering on scoring/continuous streams.
            options = {'samplerate': rate, 'dtype': 'float32', 'device': device,
                       'blocksize': 0 if capture_only else int(rate * (.1 if self.conversation_sink else .5)),
                       'callback': callback}
            if capture_only:
                options['latency'] = 'low'
            try:
                stream = sd.InputStream(channels=channels, **options)
            except sd.PortAudioError:
                if channels == 1 or stop.is_set():
                    raise
                channels = 1
                stream = sd.InputStream(channels=channels, **options)
            with self.lock:
                if stop.is_set() or generation != self.generation:
                    return
                stream.start()
                self.stream, published = stream, True
                self.input_device = {'id': device, 'name': descriptor.get('name'), 'rate': rate, 'channels': channels}
                self.status = 'listening · push-to-talk' if capture_only else 'listening · preparing classifier'
            if capture_only:
                if not input_ready.wait(PTT_FIRST_PCM_TIMEOUT) and not stop.is_set() and not input_ready.is_set():
                    raise ValueError('The selected microphone opened but sent no audio frames.')
                if not stop.is_set():
                    stop.wait()
                return
            # A newly opened conversational microphone must capture immediately;
            # cold TensorFlow/model loading is unrelated to speech transcription.
            model_ready = self.prepare()
            with self.lock:
                if generation != self.generation:
                    return
                self.status = 'listening · YAMNet uncalibrated' if model_ready else 'listening · meter only'
            pending = np.empty(0, dtype=np.float32)
            runs, capture_id = SoundRuns(), str(uuid.uuid4())
            pending_frame, pending_start = 0, 0.
            while not stop.is_set():
                try:
                    frame, capture_start, data = chunks.get(timeout=.3)
                except queue.Empty:
                    continue
                with self.lock:
                    if generation != self.generation:
                        return
                    self.peak = float(np.max(np.abs(data)))
                if not model_ready:
                    continue
                if not len(pending) or pending_frame + len(pending) != frame:
                    pending = np.empty(0, dtype=np.float32)
                    pending_frame, pending_start = frame, capture_start
                pending = np.concatenate((pending, data))
                while len(pending) >= rate:
                    with self.lock:
                        references = tuple(self.effect_references)
                    result = self.detect_window(pending[:rate], rate, pending_start, references)
                    with self.lock:
                        if stop.is_set() or generation != self.generation or epoch != self.score_epoch:
                            return
                        if self.score_allowed() and result.get('kind') == 'fart' and runs.new_event(result, pending_frame / rate, 1.):
                            self.store.event(f'microphone:{capture_id}:{pending_frame}', result['kind'],
                                             'YAMNet microphone · uncalibrated', result['confidence'],
                                             profile_id=profile_id, active_only=True)
                    hop = rate // 2
                    pending = pending[hop:]
                    pending_frame += hop
                    pending_start += hop / rate
        except Exception as error:
            with self.lock:
                if generation == self.generation and not stop.is_set():
                    detail = str(error) if published else 'Could not open the selected microphone: ' + str(error)
                    self.status, self.error = 'microphone error', detail[:200]
                    stop.set()
        finally:
            with self.lock:
                close_stream = stream is not None and (not published or self.stream is stream)
                if self.stream is stream:
                    self.stream = None
                if generation == self.generation:
                    self.peak = 0.0
            if close_stream:
                stream.close()

    def _asr_paths(self):
        base = self.data_dir
        config_path = base / 'asr.json'
        if config_path.is_file():
            try:
                config = json.loads(config_path.read_text(encoding='utf-8'))
                return Path(config['python']), Path(config['model_path'])
            except (ValueError, KeyError, TypeError, OSError):
                self.error = 'ASR configuration is invalid; prepare local transcription again.'
        return (base / 'asr-venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'), base / 'models' / 'whisper-tiny.en')

    def start_conversation(self):
        with self.lock:
            self.ptt_owned_stream = self.stream is None and not self.status.startswith('starting')
            with self.ptt_lock:
                self.ptt_data.clear()
                self.ptt_rate, self.ptt_frames, self.ptt_buffered = 0, 0, 0
                self.ptt_started, self.ptt_first_pcm_ms = time.monotonic(), None
                self.ptt, self.ptt_status = True, 'waiting for microphone audio'
            self.start()
        return {'accepted': True, 'status': self.ptt_status}

    def require_transcriber(self):
        python, model = self._asr_paths()
        if not python.is_file() or not model.is_dir():
            raise ValueError('Prepare local faster-whisper first; see docs/VOICE.md.')

    def start_continuous_conversation(self, sink):
        with self.lock:
            self.conversation_sink = sink
        return self.start()

    def cancel_push_to_talk(self):
        """Release PTT without closing a separately enabled scoring stream."""
        with self.lock:
            if not self.ptt_owned_stream:
                with self.ptt_lock:
                    self.ptt, self.ptt_status = False, 'off'
                    self.ptt_data.clear()
                    self.ptt_buffered = 0
                if self.asr_process and self.asr_process.poll() is None:
                    terminate_worker(self.asr_process)
                return
        self.stop()

    def finish_conversation(self, *, expected_generation=None, cancel_event=None):
        """Run in a background API worker. Never submits model turns itself."""
        import numpy as np
        with self.lock:
            if (expected_generation is not None and expected_generation != self.generation
                    or cancel_event is not None and cancel_event.is_set()):
                return {'text': '', 'cancelled': True}
            with self.ptt_lock:
                self.ptt = False
                chunks, rate, generation = list(self.ptt_data), self.ptt_rate, self.generation
                self.ptt_data.clear()
                self.ptt_buffered = 0
            error = self.error if self.status == 'microphone error' else ''
            if not chunks:
                if not error and self.stream is not None and self.ptt_started is not None:
                    if time.monotonic() - self.ptt_started >= PTT_FIRST_PCM_TIMEOUT:
                        error = 'The selected microphone opened but sent no audio frames.'
                error = error or 'Released before microphone audio arrived. Hold until the microphone is on, then speak.'
            self.ptt_status = 'off' if error else 'transcribing locally'
            owned, self.ptt_owned_stream = self.ptt_owned_stream, False
            if owned:
                self.stop_event.set()
                stream, self.stream = self.stream, None
                self.status, self.peak = 'off', 0.0
            else:
                stream = None
        if stream:
            try:
                stream.abort()
            finally:
                stream.close()
        if error:
            raise ValueError(error)
        samples = np.concatenate(chunks)
        if samples.ndim == 2:
            # Choose once per take so a quiet lane cannot erase or interrupt
            # speech in the other lane of a two-channel array.
            samples = samples[:, int(np.argmax(np.sum(samples ** 2, axis=0)))]
        if not np.any(samples):
            self.ptt_status = 'off'
            raise ValueError('Microphone frames arrived, but the selected input was silent.')
        if cancel_event is None:
            return self.transcribe_samples(samples, rate, generation)
        return self.transcribe_samples(samples, rate, generation, cancel_event=cancel_event)

    def transcribe_samples(self, samples, rate, generation=None, *, cancel_event=None):
        """Local ASR for PTT or a bounded continuous segment; never begins a turn."""
        with self.asr_lock:
            return self._transcribe_samples(samples, rate, self.generation if generation is None else generation,
                                            cancel_event)

    def _transcribe_samples(self, samples, rate, generation, cancel_event=None):
        import numpy as np
        import wave
        with self.lock:
            if generation != self.generation or (cancel_event is not None and cancel_event.is_set()):
                return {'text': '', 'cancelled': True}
        self.require_transcriber()
        python, model = self._asr_paths()
        samples = np.asarray(samples, dtype=np.float32)
        if not 8000 <= rate <= 96000 or samples.ndim != 1 or not 0 < len(samples) <= rate * 20 or not np.isfinite(samples).all():
            raise ValueError('Use up to 20 seconds of finite microphone audio.')
        path, process = None, None
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', prefix='chuddy-ptt-', delete=False) as file:
                path = Path(file.name)
            with wave.open(str(path), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())
            command = [str(python), str(ROOT / 'chuddy' / 'asr_worker.py'), '--model', str(model), '--input', str(path)]
            with self.lock:
                if generation != self.generation or (cancel_event is not None and cancel_event.is_set()):
                    return {'text': '', 'cancelled': True}
                process = self.asr_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=0x08000000 if os.name == 'nt' else 0)
            stdout, stderr = process.communicate(timeout=90)
            with self.lock:
                if generation != self.generation or (cancel_event is not None and cancel_event.is_set()):
                    return {'text': '', 'cancelled': True}
                if process.returncode:
                    raise ValueError('Local transcription failed: ' + stderr[-250:])
                result = json.loads(stdout)
                self.ptt_status = 'off'
                return result
        except Exception:
            if cancel_event is None or not cancel_event.is_set():
                self.ptt_status = 'transcription error' if generation == self.generation else 'off'
            if process and process.poll() is None:
                terminate_worker(process)
            raise
        finally:
            if path:
                path.unlink(missing_ok=True)
            with self.lock:
                if self.asr_process is process:
                    self.asr_process = None
