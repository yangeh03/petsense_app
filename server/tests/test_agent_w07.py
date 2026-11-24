"""W07 单元测试：图片附件端点、turns 图片引用、模型输入构造与轨迹（agent-w07-spec.md）。"""
import base64
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from helpers import TurnClient, fresh_db, new_conversation
from PIL import Image
from test_pets import seed_user_with_pet

from petsense import config
from petsense.agent import attachments
from petsense.agent.routes import bp
from petsense.db import session_scope
from petsense.db.models import Pet
from petsense.db.models.agent import AgentRun
from sqlalchemy import select


def model_turn(text, calls=()):
    return {'text': text, 'function_calls': list(calls), 'usage': {}}


def allowed_stance(speaker='assistant', intent='social'):
    return '{"scope":"allowed","intent":"%s","speaker":"%s"}\n' % (intent, speaker)


def image_bytes(size=(640, 480), color=(200, 40, 40), fmt='JPEG'):
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format=fmt)
    return buffer.getvalue()


class W07Base(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.media_root = holder.name
        media = patch.object(config, 'AGENT_MEDIA_DIR', holder.name)
        media.start()
        self.addCleanup(media.stop)
        self.uid = seed_user_with_pet()
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        auth = patch('petsense.agent.routes.require_app_user', return_value=({'id': self.uid}, None))
        auth.start()
        self.addCleanup(auth.stop)
        with session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.client = TurnClient(self.app.test_client(), self.pet)
        self.path = f'/api/app/agent/pets/{self.pet}/turns'
        self.images_path = f'/api/app/agent/pets/{self.pet}/images'

    def _upload(self, payload=None, filename='a.jpg', mimetype='image/jpeg'):
        payload = image_bytes() if payload is None else payload
        return self.client.post(self.images_path,
                                data={'image': (io.BytesIO(payload), filename, mimetype)},
                                content_type='multipart/form-data')

    def _upload_ok(self, **kwargs):
        response = self._upload(**kwargs)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.json

    def _respond_capture(self, reply='看到了'):
        """打桩模型并捕获 input_items，用于断言模型输入构造。"""
        captured = {}

        def fake(input_items, tools=None, on_delta=None, max_output_tokens=1800, web_search=False):
            captured['items'] = input_items
            return model_turn(allowed_stance() + reply)

        patcher = patch('petsense.agent.model.respond', side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return captured

    def _user_contents(self, captured):
        return [item['content'] for item in captured['items'] if item.get('role') == 'user']


class UploadTests(W07Base):
    def test_upload_stores_content_addressed_file(self):
        meta = self._upload_ok()
        self.assertRegex(meta['id'], r'^[0-9a-f]{64}$')
        self.assertEqual((meta['width'], meta['height']), (640, 480))
        self.assertGreater(meta['bytes'], 0)
        self.assertTrue(os.path.isfile(
            os.path.join(self.media_root, str(self.uid), meta['id'] + '.jpg')))

    def test_upload_downscales_to_server_cap(self):
        meta = self._upload_ok(payload=image_bytes(size=(2000, 1000)))
        self.assertEqual((meta['width'], meta['height']), (1280, 640))

    def test_upload_accepts_png_and_normalizes(self):
        meta = self._upload_ok(payload=image_bytes(fmt='PNG'), filename='a.png', mimetype='image/png')
        self.assertTrue(meta['id'].endswith('') or len(meta['id']) == 64)
        self.assertTrue(os.path.isfile(os.path.join(self.media_root, str(self.uid), meta['id'] + '.jpg')))

    def test_upload_rejects_non_image(self):
        response = self._upload(payload=b'not an image at all', filename='a.jpg')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['code'], 'invalid_image')

    def test_upload_rejects_oversize(self):
        with patch.object(config, 'MAX_UPLOAD_BYTES', 64):
            response = self._upload()
        self.assertEqual(response.status_code, 400)

    def test_upload_dedups_same_content(self):
        first, second = self._upload_ok(), self._upload_ok()
        self.assertEqual(first['id'], second['id'])

    def test_upload_quota_rejects_structured(self):
        with patch.object(attachments, 'MAX_PER_USER', 1):
            self._upload_ok(payload=image_bytes(color=(1, 2, 3)))
            response = self._upload(payload=image_bytes(color=(9, 8, 7)))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['code'], 'storage_full')

    def test_upload_requires_auth(self):
        with patch('petsense.agent.routes.require_app_user', return_value=(None, ({'message': 'unauthorized'}, 401))):
            self.assertEqual(self._upload().status_code, 401)


class ServeTests(W07Base):
    def test_owner_reads_image_with_immutable_cache(self):
        meta = self._upload_ok()
        response = self.client.get(f'/api/app/agent/images/{meta["id"]}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        # private：鉴权资源不进共享缓存；内容寻址仍 immutable
        cache_control = response.headers['Cache-Control']
        self.assertIn('immutable', cache_control)
        self.assertIn('private', cache_control)
        self.assertNotIn('public', cache_control)

    def test_other_user_cannot_read(self):
        meta = self._upload_ok()
        with patch('petsense.agent.routes.require_app_user', return_value=({'id': self.uid + 1}, None)):
            response = self.client.get(f'/api/app/agent/images/{meta["id"]}')
        self.assertEqual(response.status_code, 404)

    def test_bad_ids_rejected(self):
        for bad in ('..', 'abc', 'x' * 64, '%2e%2e%2f' + 'a' * 60):
            self.assertEqual(self.client.get(f'/api/app/agent/images/{bad}').status_code, 404, bad)


class TurnImagesTests(W07Base):
    def test_turn_inline_image_blocks_and_persist(self):
        meta = self._upload_ok()
        captured = self._respond_capture()
        response = self.client.post(self.path, json={'text': '看看这张', 'requestId': 'r1',
                                                     'images': [meta['id']]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json['turn']['images'][0]['id'], meta['id'])
        contents = self._user_contents(captured)
        self.assertIsInstance(contents[-1], list)
        self.assertEqual(contents[-1][0], {'type': 'input_text', 'text': '看看这张'})
        self.assertTrue(contents[-1][1]['image_url'].startswith('data:image/jpeg;base64,'))
        expected = base64.b64encode(open(os.path.join(
            self.media_root, str(self.uid), meta['id'] + '.jpg'), 'rb').read()).decode()
        self.assertEqual(contents[-1][1]['image_url'], 'data:image/jpeg;base64,' + expected)

    def test_pure_image_turn_without_text_block(self):
        meta = self._upload_ok()
        captured = self._respond_capture()
        response = self.client.post(self.path, json={'text': '', 'requestId': 'r2',
                                                     'images': [meta['id']]})
        self.assertEqual(response.status_code, 200)
        contents = self._user_contents(captured)
        self.assertIsInstance(contents[-1], list)
        self.assertFalse(any(block['type'] == 'input_text' for block in contents[-1]))

    def test_image_validation(self):
        self.assertEqual(self.client.post(self.path, json={
            'text': 'hi', 'requestId': 'a', 'images': ['not-hex']}).status_code, 400)
        # 合法格式但未上传（或已失效）：404 引导重新上传（与 test_send_with_deleted_image_reference_404 同口径）
        self.assertEqual(self.client.post(self.path, json={
            'text': 'hi', 'requestId': 'b', 'images': ['a' * 64]}).status_code, 404)
        # 非字符串元素不可哈希：必须结构化 400，不能 500（去重 set 之前先查类型）
        for bad in ({'id': 'x'}, ['nested'], None, True, 123):
            response = self.client.post(self.path, json={
                'text': 'hi', 'requestId': 'v-bad', 'images': [bad]})
            self.assertEqual(response.status_code, 400, repr(bad))
            self.assertEqual(response.json['message'], '图片引用无效')
        ids = [self._upload_ok(payload=image_bytes(color=(i, 0, 0)))['id'] for i in range(4)]
        self.assertEqual(self.client.post(self.path, json={
            'text': 'hi', 'requestId': 'c', 'images': ids}).status_code, 400)
        self.assertEqual(self.client.post(self.path, json={
            'text': 'hi', 'requestId': 'd', 'images': [ids[0], ids[0]]}).status_code, 400)

    def test_idempotency_compares_images(self):
        meta = self._upload_ok()
        captured = self._respond_capture()
        body = {'text': '看看', 'requestId': 'same', 'images': [meta['id']]}
        first = self.client.post(self.path, json=body)
        second = self.client.post(self.path, json=body)
        self.assertEqual(first.json, second.json)
        other = self._upload_ok(payload=image_bytes(color=(7, 7, 7)))
        conflict = self.client.post(self.path, json={**body, 'images': [other['id']]})
        self.assertEqual(conflict.status_code, 409)

    def test_history_inline_window_d1(self):
        first = self._upload_ok(payload=image_bytes(color=(10, 10, 10)))
        second = self._upload_ok(payload=image_bytes(color=(20, 20, 20)))
        self._respond_capture()
        for index, image_id in enumerate((first['id'], second['id'])):
            response = self.client.post(self.path, json={'text': f'第{index}张', 'requestId': f'h{index}',
                                                         'images': [image_id]})
            self.assertEqual(response.status_code, 200)
        captured = self._respond_capture()
        response = self.client.post(self.path, json={'text': '刚才的图呢', 'requestId': 'h2'})
        self.assertEqual(response.status_code, 200)
        contents = self._user_contents(captured)
        inlined = [c for c in contents if isinstance(c, list)]
        placeholders = [c for c in contents if isinstance(c, str) and '已省略' in c]
        self.assertEqual(len(inlined), 1, 'D1：最近 1 条带图历史内联')
        self.assertTrue(inlined[0][-1]['image_url'].startswith('data:image/jpeg;base64,'))
        expected = base64.b64encode(open(os.path.join(
            self.media_root, str(self.uid), second['id'] + '.jpg'), 'rb').read()).decode()
        self.assertTrue(any(block['image_url'] == 'data:image/jpeg;base64,' + expected
                            for block in inlined[0] if block['type'] == 'input_image'))
        self.assertEqual(len(placeholders), 1, '更早历史图片降级为占位')
        self.assertIn('（附 1 张图片，已省略）', placeholders[0])

    def test_missing_history_file_degrades_with_placeholder(self):
        # 文件在回合落库后被清理：下轮运行中该历史图内联失败，降级占位且不中断。
        meta = self._upload_ok()
        self._respond_capture()
        response = self.client.post(self.path, json={'text': '看看', 'requestId': 'm1',
                                                     'images': [meta['id']]})
        self.assertEqual(response.status_code, 200)
        os.remove(os.path.join(self.media_root, str(self.uid), meta['id'] + '.jpg'))
        captured = self._respond_capture()
        response = self.client.post(self.path, json={'text': '刚才那张呢', 'requestId': 'm2'})
        self.assertEqual(response.status_code, 200, '历史文件缺失不中断运行')
        contents = self._user_contents(captured)
        self.assertIn({'type': 'input_text', 'text': '（1 张图片已失效）'}, contents[0],
                      f'内联窗口内缺失应降级占位：{contents[0]}')

    def test_send_with_deleted_image_reference_404(self):
        meta = self._upload_ok()
        os.remove(os.path.join(self.media_root, str(self.uid), meta['id'] + '.jpg'))
        self._respond_capture()
        response = self.client.post(self.path, json={'text': '看看', 'requestId': 'm3',
                                                     'images': [meta['id']]})
        self.assertEqual(response.status_code, 404, '发送时引用不存在的图片应 404')

    def test_trace_records_images_without_base64(self):
        meta = self._upload_ok()
        self._respond_capture()
        response = self.client.post(self.path, json={'text': '看看', 'requestId': 't1',
                                                     'images': [meta['id']]})
        self.assertEqual(response.status_code, 200)
        with session_scope() as s:
            run = s.execute(select(AgentRun).order_by(AgentRun.id.desc())).scalars().first()
            messages = run.messages
        self.assertNotIn('data:image', messages, '轨迹不得包含 base64')
        entry = next(m for m in json.loads(messages) if 'images' in m)
        self.assertEqual(entry['images'][0]['id'], meta['id'])
        self.assertTrue(entry['images'][0]['inline'])
        self.assertEqual(entry['image_notes'], [])

    def test_legacy_turn_without_images_renders_empty(self):
        self._respond_capture()
        response = self.client.post(self.path, json={'text': '旧消息', 'requestId': 'L1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['turn']['images'], [])
        listed = self.client.get(self.path).json['turns']
        self.assertEqual(listed[-1]['images'], [])


if __name__ == '__main__':
    unittest.main()
