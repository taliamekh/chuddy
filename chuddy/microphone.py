"""User-operated conversational microphone; no capture or model work on creation."""
import queue
import threading


MODES = ('push_to_talk', 'always_listening')


class SpeechSegmenter:
    """Bounded energy/silence segmentation, not a speaker or speech classifier."""

    def __init__(self, threshold=.012, silence=.7, minimum=.25, maximum=12.):
        self.threshold, self.silence, self.minimum, self.maximum = threshold, silence, minimum, maximum
        self.reset()

    def reset(self):
        self.chunks, self.elapsed, self.voiced, self.quiet, self.rate = [], 0., 0., 0., None
        self.pre_roll = None

    def feed(self, samples, rate):
        import numpy as np
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all() or not 8000 <= rate <= 96000:
            return None
        if self.rate is not None and self.rate != rate:
            self.reset()
        self.rate = rate
        duration = len(samples) / rate
        loud = float(np.sqrt(np.mean(np.square(samples)))) >= self.threshold
        if not self.chunks and not loud:
            self.pre_roll = samples[-int(rate * .15):].copy()
            return None
        if not self.chunks and self.pre_roll is not None:
            self.chunks.append(self.pre_roll)
            self.elapsed += len(self.pre_roll) / rate
        self.chunks.append(samples.copy())
        self.elapsed += duration
        self.voiced += duration if loud else 0.
        self.quiet = 0. if loud else self.quiet + duration
        if self.quiet < self.silence and self.elapsed < self.maximum:
            return None
        segment = np.concatenate(self.chunks)[:int(rate * self.maximum)] if self.voiced >= self.minimum else None
        self.reset()
        return (segment, rate) if segment is not None else None


class ConversationMicrophone:
    def __init__(self, audio, settings_getter, profile_getter, on_transcript, suppress=None):
        self.audio, self.settings_getter, self.profile_getter = audio, settings_getter, profile_getter
        self.on_transcript, self.suppress = on_transcript, suppress or (lambda: False)
        self.lock, self.operations = threading.RLock(), threading.Lock()
        self.generation, self.profile_id = 0, None
        self.mode, self.active, self.valid, self.transcribing = 'push_to_talk', False, False, False
        self.status, self.error, self.audio_generation = 'off', '', None
        self.stop_event = threading.Event()

    def _profile(self):
        profile = self.profile_getter()
        return profile.get('id') if isinstance(profile, dict) else profile

    def current(self, generation, profile_id):
        with self.lock:
            valid = self.valid and generation == self.generation and profile_id == self.profile_id
        return valid and self._profile() == profile_id

    def info(self):
        with self.lock:
            mode, active, status, error = self.mode, self.active, self.status, self.error
            transcribing, generation = self.transcribing, self.generation
            started = self.audio_generation is not None
        audio = self.audio.info()
        if audio.get('error') and 'error' in audio.get('status', ''):
            active, error, status = False, audio['error'], 'microphone error'
        if not active and not transcribing:
            configured = self.settings_getter().get('microphone_mode', 'push_to_talk')
            mode = configured if configured in MODES else 'push_to_talk'
        recording = bool(audio.get('recording'))
        frames = audio.get('ptt_frames')
        ready = bool(audio.get('input_ready', recording))
        if mode == 'push_to_talk' and frames is not None:
            ready = ready and frames > 0
        capturing = active and started and ready
        if active and not capturing:
            status = 'waiting for microphone audio' if recording else 'starting microphone'
        return {'mode': mode, 'active': active, 'capturing': capturing,
                'transcribing': transcribing, 'status': status, 'error': error, 'generation': generation,
                'input_device': audio.get('input_device'), 'ptt_frames': frames, 'ptt_first_pcm_ms': audio.get('ptt_first_pcm_ms'),
                'source': 'computer microphone · local faster-whisper', 'raw_audio_saved': False}

    def start(self):
        mode = self.settings_getter().get('microphone_mode', 'push_to_talk')
        if mode not in MODES:
            raise ValueError('Choose Push to talk or Always listening.')
        profile = self._profile()
        self.audio.require_transcriber()
        with self.operations:
            audio_info = self.audio.info()
            capture_failed = bool(audio_info.get('error') and 'error' in audio_info.get('status', ''))
            with self.lock:
                if self.active and not capture_failed:
                    return self.info()
                retire = self.valid or self.transcribing or capture_failed
                if retire:
                    self.generation += 1
                    self.active, self.valid, self.transcribing = False, False, False
                    self.stop_event.set()
            if retire:
                # Retire a failed capture or prior PTT ASR before another stream
                # starts; its queued PCM/process/results belong to the old token.
                self.audio.stop()
            with self.lock:
                self.generation += 1
                generation = self.generation
                self.mode, self.profile_id, self.active, self.valid = mode, profile, True, True
                self.status, self.error, self.transcribing = 'starting microphone', '', False
                self.audio_generation = None
                stop = self.stop_event = threading.Event()
            try:
                if mode == 'push_to_talk':
                    self.audio.start_conversation()
                else:
                    chunks, segments = queue.Queue(maxsize=30), queue.Queue(maxsize=2)
                    def feed(samples, rate):
                        if not stop.is_set():
                            try:
                                chunks.put_nowait((samples, rate))
                            except queue.Full:
                                pass  # Never block the sound-device callback.
                    self.audio.start_continuous_conversation(feed)
                with self.lock:
                    self.audio_generation = self.audio.generation
                    self.status = 'listening' if mode == 'always_listening' else 'recording voice message'
                if mode == 'always_listening':
                    threading.Thread(target=self._segment_loop, args=(chunks, segments, generation, profile, stop), daemon=True).start()
                    threading.Thread(target=self._transcribe_loop, args=(segments, generation, profile, self.audio_generation, stop), daemon=True).start()
            except Exception as error:
                with self.lock:
                    self.active, self.valid, self.status, self.error = False, False, 'microphone error', str(error)[:200]
                stop.set()
                self.audio.stop()
                raise
        return self.info()

    def stop(self, *, preserve_scoring=False):
        # Invalidate before waiting for any stream operation; callbacks run outside
        # these locks and the controller revalidates their token at final admission.
        with self.lock:
            self.generation += 1
            self.active, self.valid, self.transcribing, self.status = False, False, False, 'off'
            self.stop_event.set()
        with self.operations:
            with self.lock:
                # A concurrent Start may have been inside its stream operation
                # when Stop first invalidated it. Stop is final when it returns.
                if self.active or self.valid:
                    self.generation += 1
                    self.active, self.valid, self.transcribing, self.status = False, False, False, 'off'
                    self.stop_event.set()
            if preserve_scoring:
                self.audio.cancel_push_to_talk()
            else:
                self.audio.stop()
        return self.info()

    def finish(self, *, expected_generation=None):
        with self.lock:
            if expected_generation is not None and (expected_generation != self.generation or not self.active):
                return {'text': '', 'cancelled': True}
            if not self.active or self.mode != 'push_to_talk':
                raise ValueError('Start a push-to-talk message first.')
            generation, profile = self.generation, self.profile_id
            audio_generation = self.audio_generation
            cancel_event = self.stop_event
            self.active, self.transcribing, self.status = False, True, 'transcribing locally'
        try:
            result = (self.audio.finish_conversation() if expected_generation is None else
                      self.audio.finish_conversation(expected_generation=audio_generation, cancel_event=cancel_event))
            if not self.current(generation, profile) or result.get('cancelled'):
                return {'text': '', 'cancelled': True}
            return {**result, 'generation': generation, 'profile_id': profile}
        except ValueError as error:
            with self.lock:
                if generation == self.generation:
                    self.error = str(error)[:200]
            raise
        finally:
            with self.lock:
                if generation == self.generation:
                    self.transcribing, self.status = False, 'off'

    def _segment_loop(self, chunks, segments, generation, profile, stop):
        segmenter = SpeechSegmenter()
        while not stop.is_set():
            try:
                samples, rate = chunks.get(timeout=.2)
            except queue.Empty:
                continue
            if not self.current(generation, profile):
                return
            if self.suppress():
                segmenter.reset()
                continue
            segment = segmenter.feed(samples, rate)
            if segment is not None:
                try:
                    segments.put_nowait(segment)
                except queue.Full:
                    with self.lock:
                        if generation == self.generation:
                            self.error = 'Speech queue is full; wait for Chuddy to catch up.'

    def _transcribe_loop(self, segments, generation, profile, audio_generation, stop):
        while not stop.is_set():
            try:
                samples, rate = segments.get(timeout=.2)
            except queue.Empty:
                continue
            if not self.current(generation, profile):
                return
            if self.suppress():
                continue
            with self.lock:
                if generation == self.generation:
                    self.transcribing, self.status = True, 'transcribing locally'
            try:
                result = self.audio.transcribe_samples(samples, rate, audio_generation)
                text = str(result.get('text', '')).strip()
                if text and not result.get('cancelled') and self.current(generation, profile) and not self.suppress():
                    self.on_transcript(text, generation, profile)
            except Exception as error:
                with self.lock:
                    if generation == self.generation:
                        self.error = str(error)[:200]
            finally:
                with self.lock:
                    if generation == self.generation:
                        self.transcribing, self.status = False, 'listening'

    close = stop
