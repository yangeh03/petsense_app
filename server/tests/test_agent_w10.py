"""W10 单元/API 测试：录音确认与协议、PetGIF 日记、私有社区草稿（agent-w10-spec.md §5）。

录音链路按 D3 默认关停，测试显式开启 PETSENSE_AGENT_RECORDING_ENABLED；
MQTT 发布与音频回传全部走真实服务函数 + 打桩 publish，不依赖 broker。
"""
import base64
import json
import os
import struct
import sys
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from helpers import TurnClient, fresh_db
from test_agent import model_turn, allowed_stance
from test_pets import seed_user_with_pet

from petsense import config, db
from petsense import db as petsense_db
from petsense.agent import actions as agent_actions
from petsense.agent import proposals as proposal_service
from petsense.agent import registry
from petsense.agent.routes import bp as agent_bp
from petsense.db import utc_now
from petsense.db.models import Device, Pet, Post, TelemetryLatest
from petsense.db.models.agent import AgentAction, AgentProposal, AgentTurn
from petsense.telemetry import commands as commands_module
from petsense.telemetry import mqtt_ingest
from sqlalchemy import select

DEVICE = 'esp32-w10'


def make_wav(seconds, sample_rate=16000, bits=16, channels=1):
    data_bytes = int(sample_rate * seconds) * channels * bits // 8
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    header = struct.pack('<4sI4s4sIHHIIHH4sI',
                         b'RIFF', 36 + data_bytes, b'WAVE', b'fmt ', 16, 1, channels,
                         sample_rate, byte_rate, block_align, bits, b'data', data_bytes)
    return header + b'\x01\x00' * (data_bytes // 2)


def send_audio_frames(device_id, token, wav, chunk=4096, sample_rate=16000, bits=16,
                      declared_ms=None, size=None, requested_ms=None):
    """按 psrec01 真实帧格式回传（start/chunk/end），requestedMs 缺省用实际时长。"""
    chunks = [wav[i:i + chunk] for i in range(0, len(wav), chunk)]
    duration_ms = declared_ms if declared_ms is not None else \
        (len(wav) - 44) * 1000 // (sample_rate * bits // 8)
    agent_actions.ingest_agent_audio(device_id, {
        'type': 'agent_audio_start', 'proto': 'psrec01', 'requestId': token,
        'deviceId': device_id, 'format': 'wav', 'sampleRate': sample_rate,
        'channels': 1, 'bits': bits, 'durationMs': duration_ms,
        'requestedMs': requested_ms if requested_ms is not None else duration_ms,
        'size': size or len(wav), 'chunks': len(chunks)})
    for index, data in enumerate(chunks):
        agent_actions.ingest_agent_audio(device_id, {
            'type': 'agent_audio_chunk', 'proto': 'psrec01', 'requestId': token,
            'chunkIndex': index, 'data': base64.b64encode(data).decode()})
    agent_actions.ingest_agent_audio(device_id, {
        'type': 'agent_audio_end', 'proto': 'psrec01', 'requestId': token})


class W10TestCase(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet(DEVICE)
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalar_one().id
        self.env = patch.dict(os.environ, {'PETSENSE_AGENT_RECORDING_ENABLED': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        # 测试不挂真实超时线程；需要时直接调用 _on_recording_timeout
        timer_patch = patch.object(agent_actions, '_schedule_recording_timeout', lambda *a, **k: None)
        timer_patch.start()
        self.addCleanup(timer_patch.stop)
        agent_actions._audio_buffers.clear()
        agent_actions._device_caps.clear()
        agent_actions.note_device_caps(DEVICE, {'agentAudio': agent_actions.PROTO})

    def seed_telemetry(self, age_seconds=10):
        payload = {'health': {'valid': True, 'heartRate': 120}, 'battery': {'percent': 80}}
        with db.session_scope() as s:
            # 认领时间早于回传帧（latest_telemetry 会校验 checkpoint.received_at >= claimed_at）
            s.execute(Device.__table__.update().where(Device.device_id == DEVICE)
                      .values(claimed_at=utc_now() - timedelta(hours=1)))
            existing = s.get(TelemetryLatest, DEVICE)
            if existing is not None:
                s.delete(existing)
            s.add(TelemetryLatest(device_id=DEVICE, owner_id=self.uid, boot_id='b1', ordinal=1,
                                  received_at=utc_now() - timedelta(seconds=age_seconds),
                                  payload=json.dumps(payload)))

    def make_turn(self, text='帮我录个音'):
        with db.session_scope() as s:
            turn = AgentTurn(pet_id=self.pet_id, request_id=f'r{self.uid}-{uuid.uuid4().hex[:10]}',
                             text=text, target='auto', reply='', sources='[]', speaker='assistant',
                             status='complete', created_at=utc_now())
            s.add(turn)
            s.flush()
            return turn.id

    # ---- 通用：建提案 → 确认 → 执行（默认全部条件就绪）----
    def prepare_recording(self, duration=5, turn_id=None):
        context = {'pet_id': self.pet_id, 'user_id': self.uid, 'turn_id': turn_id,
                   'text': '录一下', 'target': 'auto'}
        result = registry.execute_tool('control_device', {'durationSec': duration}, 'dialogue',
                                       'allowed', context)
        self.assertTrue(result['allowed'], result)
        return result['result']

    def confirm_recording(self, proposal_payload):
        proposal_id = proposal_payload['proposal']['id']
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            row, turn_id, changed = proposal_service.decide(s, pet, proposal_id, 'confirm')
            self.assertTrue(changed)
            self.assertEqual(row.status, 'confirmed')
        action, _ = agent_actions.execute_confirmed(self.pet_id, proposal_id)
        return action

    def action_row(self, action_id):
        with db.session_scope() as s:
            row = s.get(AgentAction, action_id)
            return row


class RecordingProposalTests(W10TestCase):
    def test_duration_bounds_and_device_required(self):
        for bad in (0, 11, 'x', None, True, 2.5):
            result = self.prepare_recording(bad)
            self.assertIn('error', result, bad)
        with db.session_scope() as s:
            # device_id 非空约束：改绑到无主设备表达“当前用户未绑定”
            s.execute(Pet.__table__.update().where(Pet.id == self.pet_id)
                      .values(device_id='esp32-unbound'))
        result = self.prepare_recording(5)
        self.assertIn('项圈', result['error'])

    def test_proposal_created_without_side_effects(self):
        published = patch('petsense.telemetry.mqtt_ingest.publish_control_command',
                          return_value=('{}', True))
        published.start()
        self.addCleanup(published.stop)
        turn_id = self.make_turn()
        result = self.prepare_recording(5, turn_id)
        self.assertNotIn('error', result)
        self.assertFalse(result['written'])
        proposal = result['proposal']
        self.assertEqual(proposal['kind'], 'record_audio')
        self.assertEqual(proposal['status'], 'pending')
        self.assertEqual(proposal['fields']['durationSec'], 5)
        self.assertEqual(proposal['fields']['deviceId'], DEVICE)
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentAction)).scalars().all(), [])
            turn = s.get(AgentTurn, turn_id)
            self.assertIsNone(turn.blocks)  # 决策前无动作块
        # 拒绝：无 MQTT 副作用、无回执行
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            proposal_service.decide(s, pet, proposal['id'], 'reject')
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentAction)).scalars().all(), [])
        action, _ = agent_actions.execute_confirmed(self.pet_id, proposal['id'])
        self.assertIsNone(action)  # 非确认状态不执行

    def test_switch_and_schemas(self):
        self.assertTrue(agent_actions.recording_enabled())
        with patch.dict(os.environ, {'PETSENSE_AGENT_RECORDING_ENABLED': ''}):
            self.assertFalse(agent_actions.recording_enabled())
            schemas = registry.schemas_for('dialogue', 'allowed')
            self.assertNotIn('control_device', [s['name'] for s in schemas])
            self.assertIn('create_diary', [s['name'] for s in schemas])
        schemas = registry.schemas_for('dialogue', 'allowed')
        self.assertIn('control_device', [s['name'] for s in schemas])


class RecordingExecutionTests(W10TestCase):
    def setUp(self):
        super().setUp()
        self.published = []
        publish_patch = patch(
            'petsense.telemetry.mqtt_ingest.publish_control_command',
            side_effect=lambda device, command: (self.published.append((device, command)), ('', True))[1])
        publish_patch.start()
        self.addCleanup(publish_patch.stop)

    def issue(self, duration=5, turn_id=None):
        result = self.prepare_recording(duration, turn_id)
        self.assertNotIn('error', result)
        return self.confirm_recording(result)

    def test_confirm_publishes_once_and_tracks_action(self):
        self.seed_telemetry()
        turn_id = self.make_turn()
        action = self.issue(5, turn_id)
        self.assertEqual(action['status'], 'executing')
        self.assertTrue(action['commandSent'])
        self.assertFalse(action['audioReceived'])
        self.assertEqual(len(self.published), 1)
        device, command = self.published[0]
        self.assertEqual(device, DEVICE)
        self.assertEqual(command['type'], 'agent_record_audio')
        self.assertEqual(command['proto'], 'psrec01')
        self.assertEqual(command['durationSec'], 5)
        self.assertEqual(command['requestId'], self.action_row(action['id']).request_token)
        self.assertGreaterEqual(len(command['requestId']), 24)  # 高熵凭据
        # 回执块写入回合且提案卡翻 confirmed；重复确认不再发布
        with db.session_scope() as s:
            turn = s.get(AgentTurn, turn_id)
            blocks = json.loads(turn.blocks)
            self.assertEqual([b['type'] for b in blocks].count('action'), 1)
            proposal = s.execute(select(AgentProposal)).scalar_one()
            self.assertEqual(proposal.status, 'confirmed')
        action2, _ = agent_actions.execute_confirmed(self.pet_id, self.published and
                                                     self.action_row(action['id']).proposal_id)
        self.assertEqual(action2['id'], action['id'])
        self.assertEqual(len(self.published), 1)

    def _fail_issue(self, expect_reason):
        result = self.prepare_recording(5)
        action = self.confirm_recording(result)
        self.assertEqual(action['status'], 'failed')
        self.assertIn(expect_reason, action['error'])
        self.assertEqual(len(self.published), 0)
        return action

    def test_confirm_gates(self):
        # 开关关停 / 离线 / 固件能力缺失 / 旧入口占用
        with patch.dict(os.environ, {'PETSENSE_AGENT_RECORDING_ENABLED': ''}):
            self._fail_issue('录音能力未开放')
        agent_actions._device_caps.pop(DEVICE, None)
        self._fail_issue('固件不支持')
        agent_actions.note_device_caps(DEVICE, {'agentAudio': 'psrec01'})
        self.seed_telemetry(age_seconds=99999)
        self._fail_issue('离线')
        self.seed_telemetry()
        commands_module.audio_recording_states[DEVICE] = {'enabled': True}
        try:
            self._fail_issue('旧入口录音')
        finally:
            commands_module.audio_recording_states.pop(DEVICE, None)

    def test_binding_change_and_publish_unknown(self):
        self.seed_telemetry()
        result = self.prepare_recording(5)   # 绑定有效时建候选
        self.assertNotIn('error', result)
        with db.session_scope() as s:
            s.execute(Pet.__table__.update().where(Pet.id == self.pet_id)
                      .values(device_id='esp32-other'))
        action = self.confirm_recording(result)
        self.assertEqual(action['status'], 'failed')
        self.assertIn('绑定已变化', action['error'])
        self.assertEqual(len(self.published), 0)
        # 发布结果不明确 → unknown，等待原结果，不自动重试
        with db.session_scope() as s:
            s.execute(Pet.__table__.update().where(Pet.id == self.pet_id).values(device_id=DEVICE))
        self.published.clear()
        publish_patch = patch('petsense.telemetry.mqtt_ingest.publish_control_command',
                              return_value=('{}', None))
        publish_patch.start()
        self.addCleanup(publish_patch.stop)
        result = self.prepare_recording(3)
        action = self.confirm_recording(result)
        self.assertEqual(action['status'], 'unknown')
        self.assertIn('未知', action['error'])

    def test_single_device_mutex(self):
        self.seed_telemetry()
        first = self.issue(5)
        self.assertEqual(first['status'], 'executing')
        result = self.prepare_recording(5)
        second = self.confirm_recording(result)
        self.assertEqual(second['status'], 'failed')
        self.assertIn('未完成的录音', second['error'])


class RecordingAudioProtocolTests(W10TestCase):
    def setUp(self):
        super().setUp()
        self.seed_telemetry()
        publish_patch = patch('petsense.telemetry.mqtt_ingest.publish_control_command',
                              return_value=('{}', True))
        publish_patch.start()
        self.addCleanup(publish_patch.stop)

    def issue_executing(self, duration=1):
        result = self.prepare_recording(duration)
        action = self.confirm_recording(result)
        self.assertEqual(action['status'], 'executing')
        row = self.action_row(action['id'])
        return action['id'], row.request_token

    def test_full_roundtrip_and_authed_read(self):
        action_id, token = self.issue_executing(1)
        wav = make_wav(1.0)
        send_audio_frames(DEVICE, token, wav)
        row = self.action_row(action_id)
        self.assertEqual(row.status, 'completed')
        result = json.loads(row.result_json)
        self.assertTrue(result['audioReceived'])
        self.assertEqual(result['audio']['durationMs'], 1000)
        self.assertEqual(result['audio']['sampleRate'], 16000)
        path = Path(config.RUNTIME_ROOT) / 'agent-recordings' / f'{action_id}.wav'
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), wav)
        # 存储目录独立于旧公共 /audio/ 链路（禁止经旧 URL 暴露 Agent 音频）
        self.assertNotEqual(path.parent, Path(config.AUDIO_DIR))

        app = Flask(__name__)
        app.register_blueprint(agent_bp)
        client = app.test_client()
        url = f'/api/app/agent/recordings/{action_id}/audio'
        self.assertEqual(client.get(url).status_code, 401)
        with patch('petsense.agent.routes.app_user_from_request',
                   return_value={'id': self.uid + 1}):
            self.assertEqual(client.get(url).status_code, 404)  # 异账号不可读
        with patch('petsense.agent.routes.app_user_from_request',
                   return_value={'id': self.uid}):
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'audio/wav')
            self.assertEqual(bytes(response.data), wav)
            self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        # token 查询参数鉴权（WebView 播放路径）
        from petsense.auth.routes import issue_app_session
        token_value = issue_app_session(self.uid)
        with patch('petsense.agent.routes.app_user_from_request', return_value=None):
            response = client.get(f'{url}?token={token_value}')
            self.assertEqual(response.status_code, 200)

    def test_wrong_device_and_replay_dropped(self):
        action_id, token = self.issue_executing(1)
        send_audio_frames('esp32-other', token, make_wav(1.0))
        self.assertEqual(self.action_row(action_id).status, 'executing')  # 错设备丢弃
        send_audio_frames(DEVICE, token, make_wav(1.0))
        self.assertEqual(self.action_row(action_id).status, 'completed')
        # 终态后重放/迟到分片不串到新请求
        send_audio_frames(DEVICE, token, make_wav(1.0))
        row = self.action_row(action_id)
        self.assertEqual(row.status, 'completed')
        self.assertEqual(len(list((Path(config.RUNTIME_ROOT) / 'agent-recordings').glob('*.wav'))), 1)

    def test_unknown_token_and_bad_request_id(self):
        action_id, token = self.issue_executing(1)
        agent_actions.ingest_agent_audio(DEVICE, {'type': 'agent_audio_start',
                                                   'requestId': 'no-such-token', 'size': 100,
                                                   'chunks': 1})
        agent_actions.ingest_agent_audio(DEVICE, {'type': 'agent_audio_chunk',
                                                   'requestId': 'short', 'data': 'AAAA'})
        agent_actions.ingest_agent_audio(DEVICE, 'not-a-dict')
        self.assertEqual(self.action_row(action_id).status, 'executing')

    def test_incomplete_and_mismatch(self):
        # end 先到：不完整 → failed
        action_id, token = self.issue_executing(1)
        wav = make_wav(1.0)
        agent_actions.ingest_agent_audio(DEVICE, {
            'type': 'agent_audio_start', 'requestId': token, 'size': len(wav),
            'chunks': 2, 'sampleRate': 16000, 'channels': 1, 'bits': 16,
            'durationMs': 1000, 'requestedMs': 1000})
        agent_actions.ingest_agent_audio(DEVICE, {
            'type': 'agent_audio_chunk', 'requestId': token, 'chunkIndex': 0,
            'data': base64.b64encode(wav[:100]).decode()})
        agent_actions.ingest_agent_audio(DEVICE, {'type': 'agent_audio_end', 'requestId': token})
        row = self.action_row(action_id)
        self.assertEqual(row.status, 'failed')
        self.assertIn('不完整', row.error)
        # 时长短于请求 → failed（真实样本数核对）
        result = self.prepare_recording(5)
        action = self.confirm_recording(result)
        send_audio_frames(DEVICE, self.action_row(action['id']).request_token, make_wav(1.0),
                          requested_ms=5000)
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'failed')
        self.assertIn('短于请求', row.error)
        # 超长拒绝
        result = self.prepare_recording(1)
        action = self.confirm_recording(result)
        send_audio_frames(DEVICE, self.action_row(action['id']).request_token,
                          make_wav(10.0), declared_ms=9000, requested_ms=1000)
        row = self.action_row(action['id'])
        self.assertIn(row.status, ('failed', 'executing'))

    def test_duplicate_chunks_and_oversize(self):
        action_id, token = self.issue_executing(1)
        wav = make_wav(0.2)  # 44 + 6400
        chunks = [wav[i:i + 4096] for i in range(0, len(wav), 4096)]
        agent_actions.ingest_agent_audio(DEVICE, {
            'type': 'agent_audio_start', 'requestId': token, 'size': len(wav),
            'chunks': len(chunks), 'sampleRate': 16000, 'channels': 1, 'bits': 16,
            'durationMs': 200, 'requestedMs': 1000})
        for index, data in enumerate(chunks):
            frame = {'type': 'agent_audio_chunk', 'requestId': token, 'chunkIndex': index,
                     'data': base64.b64encode(data).decode()}
            agent_actions.ingest_agent_audio(DEVICE, frame)
            agent_actions.ingest_agent_audio(DEVICE, frame)  # QoS1 重复投递
        # 全部分片到达即完成核对：0.2s 短于请求的 1s → 按真实样本数判失败
        row = self.action_row(action_id)
        self.assertEqual(row.status, 'failed')
        self.assertIn('短于请求', row.error)
        # 参数越界 → failed
        result = self.prepare_recording(1)
        action = self.confirm_recording(result)
        agent_actions.ingest_agent_audio(DEVICE, {
            'type': 'agent_audio_start', 'requestId': self.action_row(action['id']).request_token,
            'size': 10 * 1024 * 1024, 'chunks': 2, 'sampleRate': 16000, 'channels': 1,
            'bits': 16, 'durationMs': 1000, 'requestedMs': 1000})
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'failed')
        self.assertIn('参数无效', row.error)

    def test_status_frames_and_timeout(self):
        action_id, token = self.issue_executing(1)
        agent_actions.ingest_agent_audio(DEVICE, {'type': 'agent_audio_status',
                                                   'requestId': token, 'status': 'busy'})
        row = self.action_row(action_id)
        self.assertEqual(row.status, 'failed')
        self.assertIn('正在录音', row.error)
        # 超时终态：后到包按终态处理
        result = self.prepare_recording(1)
        action = self.confirm_recording(result)
        agent_actions._on_recording_timeout(action['id'])
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'failed')
        self.assertIn('超时', row.error)
        send_audio_frames(DEVICE, self.action_row(action['id']).request_token, make_wav(1.0))
        self.assertEqual(self.action_row(action['id']).status, 'failed')
        self.assertEqual(agent_actions._audio_buffers, {})

    def test_binding_invalidated_during_upload(self):
        action_id, token = self.issue_executing(1)
        with db.session_scope() as s:
            s.execute(Pet.__table__.update().where(Pet.id == self.pet_id)
                      .values(device_id='esp32-other'))
        send_audio_frames(DEVICE, token, make_wav(1.0))
        row = self.action_row(action_id)
        self.assertEqual(row.status, 'failed')
        self.assertIn('归属已变化', row.error)
        self.assertFalse((Path(config.RUNTIME_ROOT) / 'agent-recordings' / f'{action_id}.wav').exists())

    def test_mqtt_topic_routing(self):
        action_id, token = self.issue_executing(1)
        wav = make_wav(1.0)
        chunks = [wav[i:i + 4096] for i in range(0, len(wav), 4096)]
        frames = [
            {'type': 'agent_audio_start', 'requestId': token, 'deviceId': DEVICE,
             'format': 'wav', 'sampleRate': 16000, 'channels': 1, 'bits': 16,
             'durationMs': 1000, 'requestedMs': 1000, 'size': len(wav), 'chunks': len(chunks)},
        ] + [
            {'type': 'agent_audio_chunk', 'requestId': token, 'chunkIndex': i,
             'data': base64.b64encode(c).decode()} for i, c in enumerate(chunks)
        ] + [{'type': 'agent_audio_end', 'requestId': token}]
        for frame in frames:
            message = SimpleNamespace(topic=f'petsense/sensor/{DEVICE}/agent_audio',
                                      payload=json.dumps(frame).encode())
            mqtt_ingest.on_message(None, None, message)
        self.assertEqual(self.action_row(action_id).status, 'completed')
        # envelope 能力上报路径
        message = SimpleNamespace(topic=f'petsense/sensor/{DEVICE}',
                                  payload=json.dumps({'deviceId': DEVICE,
                                                      'agentAudio': 'psrec01'}).encode())
        mqtt_ingest.on_message(None, None, message)
        self.assertEqual(agent_actions.device_capability(DEVICE), 'psrec01')
        self.assertIsNone(agent_actions.device_capability('esp32-unknown'))


class DiaryActionTests(W10TestCase):
    def seed_walking(self, minutes=5):
        from petsense.db.models import TelemetryMinute
        from petsense.common import shanghai_now
        start = shanghai_now().replace(hour=0, minute=0, second=0, microsecond=0)
        with db.session_scope() as s:
            for i in range(minutes):
                s.add(TelemetryMinute(owner_id=self.uid, device_id=DEVICE,
                                      minute=start.astimezone() + timedelta(minutes=i),
                                      metrics='{}', actions=json.dumps({'walking': 60}),
                                      observed_seconds=60, frames=60))

    def set_avatar(self):
        from PIL import Image
        uploads = Path(config.UPLOADS_DIR)
        uploads.mkdir(parents=True, exist_ok=True)
        path = uploads / 'w10-avatar.jpg'
        Image.new('RGB', (8, 8), '#E8835C').save(path, format='JPEG')
        with db.session_scope() as s:
            s.execute(Pet.__table__.update().where(Pet.id == self.pet_id)
                      .values(avatar_image='w10-avatar.jpg'))

    def test_read_direct_and_prechecks(self):
        context = {'pet_id': self.pet_id, 'user_id': self.uid, 'turn_id': None}
        result = registry.execute_tool('create_diary', {'op': 'read'}, 'dialogue', 'allowed', context)
        self.assertTrue(result['allowed'])
        self.assertIn('date', result['result'])
        self.assertIn('jobs', result['result'])
        # 生成前检查：缺照片 / 走动不足 → 不建提案
        with patch('petsense.petgif.daily_diary._shared_pet_id', return_value='petgif-1'):
            result = registry.execute_tool('create_diary', {'op': 'generate'}, 'dialogue',
                                           'allowed', context)
            self.assertIn('照片', result['result']['error'])
            self.set_avatar()
            result = registry.execute_tool('create_diary', {'op': 'generate'}, 'dialogue',
                                           'allowed', context)
            self.assertIn('走动', result['result']['error'])
            self.seed_walking()
            result = registry.execute_tool('create_diary', {'op': 'generate'}, 'dialogue',
                                           'allowed', context)
        self.assertNotIn('error', result)
        proposal = result['result']['proposal']
        self.assertEqual(proposal['kind'], 'create_diary')
        self.assertEqual(proposal['fields']['petId'], self.pet_id)
        self.assertEqual(proposal['fields']['avatar'], 'w10-avatar.jpg')
        self.assertGreaterEqual(proposal['fields']['walkingSeconds'], 60)
        self.assertEqual(proposal['fields']['steps'],
                         proposal['fields']['walkingSeconds'] * 150 // 60)
        with patch('petsense.petgif.daily_diary._shared_pet_id', return_value=None):
            result = registry.execute_tool('create_diary', {'op': 'generate'}, 'dialogue',
                                           'allowed', context)
            self.assertIn('暂未开放', result['result']['error'])

    def test_confirm_stale_guards(self):
        self.set_avatar()
        self.seed_walking()
        context = {'pet_id': self.pet_id, 'user_id': self.uid, 'turn_id': None}
        with patch('petsense.petgif.daily_diary._shared_pet_id', return_value='petgif-1'):
            result = registry.execute_tool('create_diary', {'op': 'generate'}, 'dialogue',
                                           'allowed', context)
        proposal_id = result['result']['proposal']['id']
        # 档案照变化 → 拒绝执行
        with db.session_scope() as s:
            s.execute(Pet.__table__.update().where(Pet.id == self.pet_id)
                      .values(avatar_image='other.jpg'))
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            proposal_service.decide(s, pet, proposal_id, 'confirm')
        action, _ = agent_actions.execute_confirmed(self.pet_id, proposal_id)
        self.assertEqual(action['status'], 'failed')
        self.assertIn('照片已变化', action['error'])

    def test_confirm_submits_via_shared_service_and_hook(self):
        from petsense.db.models import PetgifDiaryJob
        self.set_avatar()
        self.seed_walking()
        context = {'pet_id': self.pet_id, 'user_id': self.uid, 'turn_id': None}
        with patch('petsense.petgif.daily_diary._shared_pet_id', return_value='petgif-1'):
            result = registry.execute_tool('create_diary', {'op': 'generate'}, 'dialogue',
                                           'allowed', context)
        proposal_id = result['result']['proposal']['id']
        submitted = []

        def fake_submit(user_id, pet_id, replace_job_id):
            submitted.append((user_id, pet_id, replace_job_id))
            return {'jobId': 'job-1', 'status': 'queued', 'date': '2026-11-23'}, 202

        with patch('petsense.petgif.daily_diary.submit_daily_ai', side_effect=fake_submit), \
                patch('petsense.petgif.daily_diary.precheck_generate', return_value=None), \
                patch('petsense.petgif.daily_diary._profile_photo'):
            with db.session_scope() as s:
                pet = s.get(Pet, self.pet_id)
                proposal_service.decide(s, pet, proposal_id, 'confirm')
            action, _ = agent_actions.execute_confirmed(self.pet_id, proposal_id)
        self.assertEqual(action['status'], 'executing')
        self.assertEqual(action['job']['jobId'], 'job-1')
        self.assertEqual(submitted, [(self.uid, self.pet_id, None)])
        # 任务完成钩子：completed 才呈现结果
        now = utc_now()
        with db.session_scope() as s:
            s.add(PetgifDiaryJob(id='job-1', user_id=self.uid, pet_id=self.pet_id,
                                 petgif_id='petgif-1', idempotency_key='daily:2026-11-23:ai:first',
                                 status='queued', input_json='{"date":"2026-11-23","mode":"ai"}',
                                 photo_paths_json='[]', result_json='{}', created_at=now,
                                 updated_at=now))
        from petsense.petgif import diary_jobs
        diary_jobs._set_terminal('job-1', 'completed',
                                 result={'posterUrl': '/exercise-diaries/poster/job-1'})
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'completed')
        self.assertEqual(json.loads(row.result_json)['job']['posterUrl'],
                         '/exercise-diaries/poster/job-1')


class DraftActionTests(W10TestCase):
    def setUp(self):
        super().setUp()
        self.context = {'pet_id': self.pet_id, 'user_id': self.uid,
                        'turn_id': self.make_turn('帮我把这段发出去')}

    def save(self, caption, explicit=True, location=None):
        return registry.execute_tool('draft_social_post',
                                     {'caption': caption, 'location': location,
                                      'explicit': explicit}, 'dialogue', 'allowed', self.context)

    def test_explicit_direct_save_and_blocks(self):
        result = self.save('今天遛弯超开心', True, '小区花园')
        self.assertTrue(result['allowed'])
        payload = result['result']
        self.assertTrue(payload['saved'])
        action = payload['action']
        self.assertEqual(action['status'], 'completed')
        self.assertEqual(action['draft']['caption'], '今天遛弯超开心')
        self.assertEqual(action['draft']['location'], '小区花园')
        with db.session_scope() as s:
            turn = s.get(AgentTurn, self.context['turn_id'])
            blocks = json.loads(turn.blocks)
            self.assertEqual(blocks[-1], {'type': 'action', 'action': action})
            self.assertEqual(s.execute(select(Post)).scalars().all(), [])  # 无发布副作用

    def test_proposal_path_and_confirm(self):
        result = self.save('想发个动态', explicit=False)
        self.assertFalse(result['result']['written'])
        proposal = result['result']['proposal']
        self.assertEqual(proposal['kind'], 'social_draft')
        self.assertEqual(proposal['fields']['caption'], '想发个动态')
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentAction)).scalars().all(), [])
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            proposal_service.decide(s, pet, proposal['id'], 'confirm')
        action, _ = agent_actions.execute_confirmed(self.pet_id, proposal['id'])
        self.assertEqual(action['status'], 'completed')

    def test_validation(self):
        for caption in ('', 'x' * 501, None):
            result = self.save(caption)
            self.assertIn('error', result['result'], caption)
        result = self.save('ok', True, 'y' * 81)
        self.assertIn('地点', result['result']['error'])

    def test_private_slot_and_dismiss(self):
        self.save('第一条', True)
        app = Flask(__name__)
        app.register_blueprint(agent_bp)
        client = app.test_client()
        url = f'/api/app/agent/pets/{self.pet_id}/social-draft'
        with patch('petsense.agent.routes.require_app_user',
                   return_value=({'id': self.uid + 1}, None)):
            # 异账号：先越权宠物（404）
            self.assertEqual(client.get(url).status_code, 404)
        with patch('petsense.agent.routes.require_app_user',
                   return_value=({'id': self.uid}, None)):
            data = client.get(url).get_json()
            self.assertEqual(data['draft']['caption'], '第一条')
            self.assertEqual(client.delete(url).get_json(), {'success': True, 'draft': None})
            self.assertIsNone(client.get(url).get_json()['draft'])
        # 再保存新草稿 → 槽位恢复最新
        self.save('第二条', True)
        with patch('petsense.agent.routes.require_app_user',
                   return_value=({'id': self.uid}, None)):
            self.assertEqual(client.get(url).get_json()['draft']['caption'], '第二条')


class RecordingUnknownAndExpiryTests(W10TestCase):
    """发布结果不明（unknown）与到期边界：等待原结果但不悬置，迟到帧不复活终态。"""

    def setUp(self):
        super().setUp()
        self.seed_telemetry()

    def issue_with_publish(self, publish_result, duration=1):
        calls = []

        def fake(device, command):
            calls.append((device, command))
            return '{}', publish_result

        with patch('petsense.telemetry.mqtt_ingest.publish_control_command', side_effect=fake):
            result = self.prepare_recording(duration)
            self.assertNotIn('error', result)
            action = self.confirm_recording(result)
        return action, calls

    def test_unknown_accepts_valid_late_audio(self):
        action, calls = self.issue_with_publish(None)
        self.assertEqual(action['status'], 'unknown')
        self.assertIn('未知', action['error'])
        self.assertEqual(len(calls), 1)
        token = self.action_row(action['id']).request_token
        send_audio_frames(DEVICE, token, make_wav(1.0))
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'completed')
        self.assertTrue(json.loads(row.result_json)['audioReceived'])
        self.assertEqual(len(calls), 1)   # 等待原结果期间不自动重发

    def test_unknown_expires_and_never_auto_resends(self):
        action, calls = self.issue_with_publish(None)
        self.assertEqual(action['status'], 'unknown')
        agent_actions._on_recording_timeout(action['id'])
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'failed')
        self.assertIn('超时', row.error)
        # 过期后到达的音频不再被接受，也不落文件
        token = self.action_row(action['id']).request_token
        send_audio_frames(DEVICE, token, make_wav(1.0))
        self.assertEqual(self.action_row(action['id']).status, 'failed')
        self.assertFalse(
            (Path(config.RUNTIME_ROOT) / 'agent-recordings' / f'{action["id"]}.wav').exists())
        self.assertEqual(len(calls), 1)

    def test_late_frame_cannot_override_terminal_or_other_action(self):
        action, _ = self.issue_with_publish(True)
        token = self.action_row(action['id']).request_token
        send_audio_frames(DEVICE, token, make_wav(1.0))
        self.assertEqual(self.action_row(action['id']).status, 'completed')
        # 终态后重复回传不改状态、不产生第二份文件
        send_audio_frames(DEVICE, token, make_wav(1.0))
        self.assertEqual(self.action_row(action['id']).status, 'completed')
        recordings = list((Path(config.RUNTIME_ROOT) / 'agent-recordings').glob('*.wav'))
        self.assertEqual(len(recordings), 1)
        # 另一个动作不被上一个动作的凭据完成
        second, _ = self.issue_with_publish(True)
        self.assertNotEqual(self.action_row(second['id']).request_token, token)
        send_audio_frames(DEVICE, token, make_wav(1.0))
        self.assertEqual(self.action_row(second['id']).status, 'executing')


class RecordingWavStrictnessTests(W10TestCase):
    """WAV 核对：声明格式、字节数与数据完整性（spec §3）。"""

    def setUp(self):
        super().setUp()
        self.seed_telemetry()
        publish_patch = patch('petsense.telemetry.mqtt_ingest.publish_control_command',
                              return_value=('{}', True))
        publish_patch.start()
        self.addCleanup(publish_patch.stop)

    def issue(self, duration=1):
        result = self.prepare_recording(duration)
        return self.confirm_recording(result)

    def test_non_declared_format_rejected(self):
        action = self.issue(1)
        token = self.action_row(action['id']).request_token
        send_audio_frames(DEVICE, token, make_wav(1.0, sample_rate=8000), sample_rate=8000)
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'failed')
        self.assertIn('格式不符', row.error)

    def test_byte_count_mismatch_rejected(self):
        action = self.issue(1)
        token = self.action_row(action['id']).request_token
        wav = make_wav(1.0)
        send_audio_frames(DEVICE, token, wav, size=len(wav) + 100)
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'failed')
        self.assertIn('字节数', row.error)

    def test_incomplete_data_block_rejected(self):
        action = self.issue(1)
        token = self.action_row(action['id']).request_token
        wav = make_wav(1.0)
        broken = wav[:44] + struct.pack('<I', len(wav) - 44 + 8) + wav[44:]
        send_audio_frames(DEVICE, token, broken)
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'failed')
        self.assertIn('不完整', row.error)
        self.assertFalse(
            (Path(config.RUNTIME_ROOT) / 'agent-recordings' / f'{action["id"]}.wav').exists())

    def test_requested_duration_mismatch_rejected(self):
        action = self.issue(5)
        token = self.action_row(action['id']).request_token
        # 设备声明的请求时长与本动作不符：不是本次请求的回传
        send_audio_frames(DEVICE, token, make_wav(5.0), requested_ms=1000)
        row = self.action_row(action['id'])
        self.assertEqual(row.status, 'failed')
        self.assertIn('不匹配', row.error)


class DraftIdempotencyTests(W10TestCase):
    """草稿按授权意图幂等：一意图一行、候选卡被持久回执取代、无发布副作用。"""

    def setUp(self):
        super().setUp()
        self.turn_id = self.make_turn('帮我把这段发出去')
        self.context = {'pet_id': self.pet_id, 'user_id': self.uid, 'turn_id': self.turn_id}

    def save(self, caption, explicit=True, location=None, context=None):
        return registry.execute_tool('draft_social_post',
                                     {'caption': caption, 'location': location,
                                      'explicit': explicit}, 'dialogue', 'allowed',
                                     context or self.context)

    def test_direct_save_is_idempotent_per_intent(self):
        first = self.save('今天遛弯超开心')
        second = self.save('今天遛弯超开心')
        self.assertEqual(first['result']['action']['id'], second['result']['action']['id'])
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentAction)).scalars().all()), 1)
            self.assertEqual(s.execute(select(Post)).scalars().all(), [])

    def test_different_intent_creates_separate_rows(self):
        first = self.save('第一条')
        other_turn = self.make_turn('再写一条')
        second = self.save('第二条', context={**self.context, 'turn_id': other_turn})
        self.assertNotEqual(first['result']['action']['id'], second['result']['action']['id'])
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentAction)).scalars().all()), 2)

    def test_confirmed_draft_single_row_and_card_replaced(self):
        result = self.save('想发个动态', explicit=False)
        proposal = result['result']['proposal']
        # 模拟路由把候选卡写进回合
        with db.session_scope() as s:
            turn = s.get(AgentTurn, self.turn_id)
            turn.blocks = json.dumps([{'type': 'proposal', 'proposal': proposal}])
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            proposal_service.decide(s, pet, proposal['id'], 'confirm')
        action, _ = agent_actions.execute_confirmed(self.pet_id, proposal['id'])
        self.assertEqual(action['status'], 'completed')
        self.assertEqual(action['draft']['caption'], '想发个动态')
        # 重复确认不再新建行
        again, _ = agent_actions.execute_confirmed(self.pet_id, proposal['id'])
        self.assertEqual(again['id'], action['id'])
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(AgentAction)).scalars().all()), 1)
            self.assertEqual(s.execute(select(Post)).scalars().all(), [])
            turn = s.get(AgentTurn, self.turn_id)
            blocks = json.loads(turn.blocks)
            self.assertEqual([b['type'] for b in blocks].count('action'), 1)
            self.assertEqual([b['type'] for b in blocks].count('proposal'), 0)

    def test_private_slot_keeps_latest_and_others_intact(self):
        self.save('第一条')
        self.save('第二条')
        with db.session_scope() as s:
            rows = s.execute(select(AgentAction)).scalars().all()
            self.assertEqual(len(rows), 2)   # 不同正文的意图各自成行，前一条不被覆盖
        app = Flask(__name__)
        app.register_blueprint(agent_bp)
        client = app.test_client()
        url = f'/api/app/agent/pets/{self.pet_id}/social-draft'
        with patch('petsense.agent.routes.require_app_user',
                   return_value=({'id': self.uid}, None)):
            self.assertEqual(client.get(url).get_json()['draft']['caption'], '第二条')


class BlockStabilityTests(W10TestCase):
    def test_action_block_upsert_no_duplicates(self):
        self.seed_telemetry()
        publish_patch = patch('petsense.telemetry.mqtt_ingest.publish_control_command',
                              return_value=('{}', True))
        publish_patch.start()
        self.addCleanup(publish_patch.stop)
        turn_id = self.make_turn()
        result = self.prepare_recording(1, turn_id)
        action = self.confirm_recording(result)
        token = self.action_row(action['id']).request_token
        send_audio_frames(DEVICE, token, make_wav(1.0))
        agent_actions._on_recording_timeout(action['id'])  # 终态后重复更新不复制块
        with db.session_scope() as s:
            turn = s.get(AgentTurn, turn_id)
            blocks = json.loads(turn.blocks)
            action_blocks = [b for b in blocks if b['type'] == 'action']
            self.assertEqual(len(action_blocks), 1)
            self.assertEqual(action_blocks[0]['action']['status'], 'completed')
        # 文本降级字段始终存在（旧客户端）
        payload = agent_actions.action_public(self.action_row(action['id']))
        self.assertIn('录音', payload['text'])

    def test_turn_flow_appends_action_block(self):
        """完整 HTTP 回合：直写草稿动作块随回复落库（模型打桩）。"""
        app = Flask(__name__)
        app.register_blueprint(agent_bp)
        client = TurnClient(app.test_client(), self.pet_id)
        auth = patch('petsense.agent.routes.require_app_user',
                     return_value=({'id': self.uid}, None))
        auth.start()
        self.addCleanup(auth.stop)
        calls = [{'name': 'draft_social_post',
                  'arguments': json.dumps({'caption': '散步真开心', 'explicit': True}),
                  'call_id': 'c1'}]
        with patch('petsense.agent.model.respond',
                   return_value=model_turn(allowed_stance('assistant', 'write') + '已保存草稿', calls)):
            response = client.post(f'/api/app/agent/pets/{self.pet_id}/turns',
                                   json={'text': '帮我存个草稿', 'requestId': 'w10-1'})
        self.assertEqual(response.status_code, 200)
        blocks = response.get_json()['turn']['blocks']
        self.assertEqual(blocks[-1]['type'], 'action')
        self.assertEqual(blocks[-1]['action']['draft']['caption'], '散步真开心')


if __name__ == '__main__':
    unittest.main()
