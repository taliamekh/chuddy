import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from chuddy.agent import LocalAgent, local_endpoint, local_request
from chuddy.store import Store
from tests.helpers import FakeVoice


def chunk(content='', done=False, calls=None):
    result = {'message': {'role': 'assistant', 'content': content}, 'done': done}
    if calls:
        result['message']['tool_calls'] = calls
    return result


class ModelFixture:
    def __init__(self, plans, model_details=None):
        self.plans = list(plans)
        self.requests = []
        self.lock = threading.Lock()
        self.gates = []
        self.model_details = model_details or {'capabilities': ['completion', 'tools'], 'thinking': {'values': [False]}}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/api/show':
                    data = json.dumps(fixture.model_details).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                with fixture.lock:
                    fixture.requests.append((self.path, payload))
                    plan = fixture.plans.pop(0) if fixture.plans else {'error': 'Unexpected model request'}
                if plan.get('redirect'):
                    self.send_response(307)
                    self.send_header('Location', plan['redirect'])
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers()
                if plan.get('error'):
                    plan = {'chunks': [{'error': plan['error']}]}
                try:
                    for index, item in enumerate(plan.get('chunks', [])):
                        self.wfile.write(json.dumps(item).encode() + b'\n')
                        self.wfile.flush()
                        if plan.get('pause_after') == index + 1:
                            if plan.get('entered'):
                                plan['entered'].set()
                            plan['gate'].wait(timeout=5)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        for plan in self.plans:
            if plan.get('gate'):
                plan['gate'].set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'agent.sqlite3')
        # Keep these legacy-path regression fixtures explicit. Candidate action
        # orchestration has its own contracts in test_persona_observed_work.
        self.store.setting('ai_model', 'qwen3:4b-instruct-2507-q4_K_M')
        self.voice = FakeVoice()
        self.fixtures = []
        self.agents = []
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for agent in self.agents:
            agent.cancel()
            if agent.worker:
                agent.worker.join(timeout=3)
        for fixture in self.fixtures:
            fixture.close()
        self.store.close()
        self.temp.cleanup()

    def agent(self, plans=None, tool=None):
        if plans is not None:
            fixture = ModelFixture(plans)
            self.fixtures.append(fixture)
            self.store.setting('ai_endpoint', fixture.url)
        agent = LocalAgent(self.store, lambda: {'test_context': True}, tool or (lambda *a, **k: {'ok': True}), self.voice)
        self.agents.append(agent)
        return agent

    def finish(self, agent):
        agent.worker.join(timeout=5)
        self.assertFalse(agent.worker.is_alive(), 'Agent did not finish its mocked turn.')
        return agent.info()

    def test_local_endpoint_excludes_cloud_urls_and_credentials(self):
        for endpoint in ('https://api.example.com', 'http://example.com', 'http://127.0.0.1/path', 'http://u:p@localhost', 'http://localhost?secret=1'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                local_endpoint(endpoint)
        self.assertEqual(local_endpoint('http://127.0.0.1:11434/'), 'http://127.0.0.1:11434')

    def test_local_model_response_is_distinct_from_scripted_fallback(self):
        agent = self.agent([{'chunks': [chunk('A fixture-only model sentence.', done=True)]}])
        agent.begin('Hello')
        state = self.finish(agent)
        self.assertEqual(state['source'], 'local model')
        self.assertTrue(state['ready'])
        self.assertEqual(self.store.messages()[-1]['text'], 'A fixture-only model sentence.')
        self.assertEqual(self.store.messages()[-1]['source'], 'local model')
        self.assertIsNotNone(state['metrics']['first_token_ms'])

    def test_scripted_mode_never_requests_inference(self):
        self.store.setting('ai_enabled', False)
        agent = self.agent()
        with patch('chuddy.agent.local_request', side_effect=AssertionError('must remain offline')) as request:
            agent.begin('What is 2 + 2?')
            state = self.finish(agent)
        request.assert_not_called()
        self.assertEqual(state['source'], 'offline script')
        self.assertIn('22', self.store.messages()[-1]['text'])

    def test_ordinary_conversation_does_not_disclose_tools_or_unrelated_deadlines(self):
        agent = self.agent([{'chunks': [chunk('A quiet day under a rock.', done=True)]}])
        agent.context = lambda: {'session': 'running', 'deadlines': [{'title': 'Unrelated report'}],
                                'character_clock': 'snack thirty', 'memory': [
                                    {'id': 7, 'key': 'snack', 'value': 'pretzels', 'updated': 'private metadata'}]}
        agent.begin('How was your day?')
        self.finish(agent)
        payload = self.fixtures[-1].requests[-1][1]
        self.assertNotIn('tools', payload)
        context = json.loads(payload['messages'][0]['content'].split('Approved context: ', 1)[1])
        self.assertEqual(context['memory'], [{'key': 'snack', 'value': 'pretzels'}])
        self.assertNotIn('deadlines', context)
        self.assertNotIn('character_clock', context)

    def test_memory_request_discloses_only_its_relevant_tool(self):
        agent = self.agent([{'chunks': [chunk('Pretzels. Got it.', done=True)]}])
        agent.begin('Remember that I like pretzels.')
        self.finish(agent)
        tools = self.fixtures[-1].requests[-1][1]['tools']
        self.assertEqual([t['function']['name'] for t in tools], ['remember_preference'])

    def test_work_observation_keeps_configured_destination_and_permitted_tools(self):
        agent = self.agent([{'chunks': [chunk('That report can wait.', done=True)]}])
        agent.context = lambda: {'session': 'running', 'work': 'Report', 'distraction_destination': 'casino'}
        agent.begin('The user is working on a report.', observation=True)
        self.finish(agent)
        payload = self.fixtures[-1].requests[-1][1]
        context = json.loads(payload['messages'][0]['content'].split('Approved context: ', 1)[1])
        self.assertEqual(context['distraction_destination'], 'casino')
        self.assertIn('request_distraction', [t['function']['name'] for t in payload['tools']])

    def test_observed_work_regenerates_an_exact_recent_line_without_repeating_tools(self):
        self.store.message('assistant','That report can wait.','scripted work banter')
        agent = self.agent([
            {'chunks':[chunk('That report can wait.',done=True)]},
            {'chunks':[chunk("We should rest. I already thought about the report, so that's half of thinking done.",done=True)]},
        ])
        agent.begin('The user is working on a report.',observation=True)
        state = self.finish(agent)
        self.assertEqual(state['source'],'local model')
        self.assertNotEqual(self.store.messages()[-1]['text'],'That report can wait.')
        self.assertEqual(len(self.fixtures[-1].requests),2)
        self.assertNotIn('tools',self.fixtures[-1].requests[-1][1])
        self.assertLessEqual(self.fixtures[-1].requests[0][1]['options']['num_predict'],128)

    def test_factual_recall_can_repeat_without_a_creative_retry(self):
        self.store.message('assistant','Pretzels.','local model')
        agent = self.agent([{'chunks':[chunk('Pretzels.',done=True)]}])
        agent.begin('What is my favorite snack?')
        state = self.finish(agent)
        self.assertEqual(state['source'],'local model')
        self.assertEqual(self.store.messages()[-1]['text'],'Pretzels.')
        self.assertEqual(len(self.fixtures[-1].requests),1)

    def test_model_error_is_honestly_labeled_fallback(self):
        agent = self.agent([{'error': 'model missing'}])
        agent.begin('Spell report')
        state = self.finish(agent)
        self.assertIn('offline fallback', state['source'])
        self.assertFalse(state['ready'])
        self.assertIn('B-U-T-T', self.store.messages()[-1]['text'])

    def test_thinking_only_model_is_rejected_before_chat(self):
        fixture = ModelFixture([], model_details={'capabilities': ['completion', 'tools'], 'thinking': {'values': [True]}})
        self.fixtures.append(fixture)
        self.store.setting('ai_endpoint', fixture.url)
        agent = self.agent()
        agent.begin('Hello')
        state = self.finish(agent)
        self.assertIn('offline fallback', state['source'])
        self.assertEqual(fixture.requests, [])
        self.assertIn('thinking-only', state['error'])

    def test_bounded_tool_roundtrip_and_trace(self):
        calls = []
        def execute(name, args, **kwargs):
            calls.append((name, args, kwargs))
            return {'ok': True, 'context': {'mock_balance': 100}}
        agent = self.agent([
            {'chunks': [chunk(calls=[{'function': {'name': 'read_context', 'arguments': {}}}], done=True)]},
            {'chunks': [chunk('I found one hundred pretend clams.', done=True)]},
        ], execute)
        agent.begin('How many clams?')
        state = self.finish(agent)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'read_context')
        self.assertEqual(state['trace'][0]['tool'], 'read_context')
        second_payload = self.fixtures[0].requests[1][1]
        self.assertTrue(any(m['role'] == 'tool' for m in second_payload['messages']))

    def test_cancellation_discards_late_response_and_tool(self):
        gate, entered = threading.Event(), threading.Event()
        self.gates.append(gate)
        executed = []
        agent = self.agent([{'chunks': [chunk(''), chunk(calls=[{'function': {'name': 'remember_preference', 'arguments': {'key': 'snack', 'value': 'clams'}}}], done=True)], 'pause_after': 1, 'gate': gate, 'entered': entered}], lambda *a, **k: executed.append(a))
        agent.begin('Remember clams')
        self.assertTrue(entered.wait(timeout=2))
        agent.cancel()
        gate.set()
        agent.worker.join(timeout=3)
        self.assertEqual(executed, [])
        self.assertEqual(self.store.messages()[-1]['role'], 'user')
        self.assertEqual(agent.info()['status'], 'idle')

    def test_new_turn_replaces_old_stalled_stream_without_late_commit(self):
        gate, entered = threading.Event(), threading.Event()
        self.gates.append(gate)
        agent = self.agent([
            {'chunks': [chunk(''), chunk('Stale answer.', done=True)], 'pause_after': 1, 'gate': gate, 'entered': entered},
            {'chunks': [chunk('New answer.', done=True)]},
        ])
        agent.begin('First message')
        self.assertTrue(entered.wait(timeout=2))
        old_worker = agent.worker
        agent.begin('Second message')
        gate.set()
        self.finish(agent)
        old_worker.join(timeout=3)
        answers = [m['text'] for m in self.store.messages() if m['role'] == 'assistant']
        self.assertIn('New answer.', answers)
        self.assertNotIn('Stale answer.', answers)

    def test_short_first_sentence_does_not_block_streaming_speech(self):
        gate, entered = threading.Event(), threading.Event()
        self.gates.append(gate)
        self.store.setting('voice', True)
        agent = self.agent([{'chunks': [chunk('Uh. A sufficiently long second sentence. '), chunk('And the end.', done=True)], 'pause_after': 1, 'gate': gate, 'entered': entered}])
        agent.begin('Hello')
        self.assertTrue(entered.wait(timeout=2))
        self.assertTrue(self.voice.spoke.wait(timeout=1), 'Complete phrase was not queued while the stream remained open.')
        self.assertIn('second sentence', self.voice.spoken[0][0])
        gate.set()
        self.finish(agent)
        self.assertEqual(sum('second sentence' in item[0] for item in self.voice.spoken), 1)

    def test_slow_turn_has_one_filler_without_recording(self):
        gate, entered = threading.Event(), threading.Event()
        self.gates.append(gate)
        self.store.setting('voice', True)
        agent = self.agent([{'chunks': [chunk(''), chunk('Hello friend.', done=True)], 'pause_after': 1, 'gate': gate, 'entered': entered}])
        result = agent.begin('Hello')
        self.assertTrue(entered.wait(timeout=2))
        deadline = time.monotonic() + 1.2
        while not self.voice.fillers and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.voice.fillers, [result['turn_id']])
        gate.set()
        self.finish(agent)

    def test_model_redirect_cannot_escape_local_endpoint(self):
        fixture = ModelFixture([{'redirect': 'http://example.com/should-never-open'}])
        self.fixtures.append(fixture)
        with self.assertRaises((ValueError, OSError)):
            local_request(fixture.url, '/api/chat', {'messages': []})

    def test_warm_reports_the_selected_model_after_a_settings_change(self):
        agent = self.agent([{'chunks': [{'done': True}]}])
        self.store.setting('ai_model', 'llama3.1:8b-instruct-q4_K_M')
        agent.warm()
        deadline = time.monotonic() + 3
        while agent.info()['status'] == 'warming' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(agent.info()['ready'])
        self.assertEqual(agent.info()['model'], self.store.settings()['ai_model'])
        self.assertEqual(self.fixtures[-1].requests[-1][1]['model'], self.store.settings()['ai_model'])
