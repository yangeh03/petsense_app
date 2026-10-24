"""WAV 音频分片重组（MQTT audio_file / audio_wav）与音频文件路由。"""

import base64
import os
import struct
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, send_from_directory, request

from petsense import config

bp = Blueprint('audio', __name__)

# audio_buffers[fileId] = {"totalChunks": N, "chunks": [None]*N, "started_at": ts, "expected_bytes": ...}
audio_buffers = {}
audio_lock = threading.Lock()


def calculate_wav_rms(wav_data):
    """Return normalized RMS for 8-bit unsigned or 16-bit signed PCM WAV data."""
    if len(wav_data) <= 44:
        return 0.0

    bits_per_sample = 16
    data_offset = 44
    try:
        if wav_data[0:4] == b'RIFF' and wav_data[8:12] == b'WAVE':
            pos = 12
            while pos + 8 <= len(wav_data):
                chunk_id = wav_data[pos:pos+4]
                chunk_size = struct.unpack('<I', wav_data[pos+4:pos+8])[0]
                chunk_start = pos + 8
                if chunk_id == b'fmt ' and chunk_start + 16 <= len(wav_data):
                    bits_per_sample = struct.unpack('<H', wav_data[chunk_start+14:chunk_start+16])[0]
                elif chunk_id == b'data':
                    data_offset = chunk_start
                    break
                pos = chunk_start + chunk_size + (chunk_size & 1)
    except Exception:
        data_offset = 44

    pcm_data = wav_data[data_offset:]
    if not pcm_data:
        return 0.0

    squares = 0.0
    count = 0
    if bits_per_sample == 8:
        for sample in pcm_data:
            value = (sample - 128) / 128.0
            squares += value * value
            count += 1
    elif bits_per_sample == 16:
        for i in range(0, len(pcm_data) - 1, 2):
            value = struct.unpack('<h', pcm_data[i:i+2])[0] / 32768.0
            squares += value * value
            count += 1
    else:
        return 0.0

    return (squares / count) ** 0.5 if count else 0.0


def handle_audio_wav_binary(payload):
    if len(payload) < 32 or payload[:7] != b'PSWAV01':
        print(f'[AUDIO] Invalid binary WAV payload bytes={len(payload)}')
        return None
    file_id = str(int.from_bytes(payload[8:12], 'little'))
    sample_rate = int.from_bytes(payload[12:16], 'little')
    wav_size = int.from_bytes(payload[16:20], 'little')
    duration_ms = int.from_bytes(payload[20:24], 'little')
    channels = int.from_bytes(payload[24:26], 'little')
    bits = int.from_bytes(payload[26:28], 'little')
    wav_data = bytes(payload[32:32 + wav_size])
    if len(wav_data) != wav_size or not wav_data.startswith(b'RIFF'):
        print(f'[AUDIO] Truncated binary WAV fileId={file_id} got={len(wav_data)} expected={wav_size}')
        return None

    wav_filename = f"audio_{file_id}_{int(time.time()*1000)}.wav"
    wav_path = os.path.join(config.AUDIO_DIR, wav_filename)
    with open(wav_path, 'wb') as f:
        f.write(wav_data)
    rms_val = calculate_wav_rms(wav_data)
    print(f'[AUDIO] Binary single {wav_filename} {len(wav_data)} bytes {sample_rate}Hz {bits}bit ch={channels} durationMs={duration_ms} rms={rms_val:.5f}')
    return {
        'type': 'audio_ready',
        'fileId': file_id,
        'filename': wav_filename,
        'url': f'/audio/{wav_filename}',
        'size': len(wav_data),
        'chunks': 1,
        'totalChunks': 1,
        'rms': round(rms_val, 5),
        'received_at': datetime.now(timezone.utc).isoformat()
    }


def handle_audio_file_chunk(payload_dict):
    """处理 audio_file 分片: start/chunk/end"""
    file_id = str(payload_dict.get('fileId', ''))
    chunk_type = payload_dict.get('type', '')
    chunk_index = int(payload_dict.get('chunkIndex', payload_dict.get('index', 0)) or 0)
    total_chunks = int(payload_dict.get('totalChunks', payload_dict.get('chunks', 0)) or 0)
    expected_size = int(payload_dict.get('size', 0) or 0)
    offset = payload_dict.get('offset')
    offset = int(offset) if offset is not None else None
    data_b64 = payload_dict.get('data', '')

    if not file_id:
        return None

    chunk_bytes = base64.b64decode(data_b64) if data_b64 else b''

    with audio_lock:
        if chunk_type == 'single' and chunk_bytes:
            wav_filename = f"audio_{file_id}_{int(time.time()*1000)}.wav"
            wav_path = os.path.join(config.AUDIO_DIR, wav_filename)
            with open(wav_path, 'wb') as f:
                f.write(chunk_bytes)
            rms_val = calculate_wav_rms(chunk_bytes)
            print(f'[AUDIO] Single file {wav_filename}  {len(chunk_bytes)} bytes  rms={rms_val:.5f}')
            return {
                'type': 'audio_ready',
                'fileId': file_id,
                'filename': wav_filename,
                'url': f'/audio/{wav_filename}',
                'size': len(chunk_bytes),
                'chunks': 1,
                'totalChunks': 1,
                'rms': round(rms_val, 5),
                'received_at': datetime.now(timezone.utc).isoformat()
            }

        if chunk_type == 'start':
            audio_buffers[file_id] = {
                'totalChunks': total_chunks,
                'chunks': [None] * total_chunks,
                'started_at': time.time(),
                'received': 0,
                'total_bytes': 0,
                'expected_size': expected_size,
                'sampleRate': int(payload_dict.get('sampleRate', 8000) or 8000),
                'channels': int(payload_dict.get('channels', 1) or 1),
                'bits': int(payload_dict.get('bits', 8) or 8)
            }
            print(f'[AUDIO] Start fileId={file_id} totalChunks={total_chunks}')
            return {'type': 'audio_progress', 'fileId': file_id, 'chunk': 0, 'received': 0, 'total': total_chunks}

        buf = audio_buffers.get(file_id)
        if not buf:
            if chunk_type != 'chunk':
                print(f'[AUDIO] Unknown fileId={file_id}, ignoring {chunk_type}')
                return None
            inferred_chunks = max(total_chunks, chunk_index + 1)
            audio_buffers[file_id] = {
                'totalChunks': inferred_chunks,
                'chunks': [None] * inferred_chunks,
                'started_at': time.time(),
                'received': 0,
                'total_bytes': 0,
                'expected_size': expected_size,
                'sampleRate': int(payload_dict.get('sampleRate', 16000) or 16000),
                'channels': int(payload_dict.get('channels', 1) or 1),
                'bits': int(payload_dict.get('bits', 16) or 16),
                'recovered_start': True
            }
            buf = audio_buffers[file_id]
            print(f'[AUDIO] Recovered missing start fileId={file_id} totalChunks={inferred_chunks}')

        if total_chunks and total_chunks > buf.get('totalChunks', 0):
            buf['chunks'].extend([None] * (total_chunks - len(buf['chunks'])))
            buf['totalChunks'] = total_chunks
        if expected_size:
            buf['expected_size'] = expected_size
        for key, default in (('sampleRate', 16000), ('channels', 1), ('bits', 16)):
            if payload_dict.get(key) is not None:
                buf[key] = int(payload_dict.get(key) or default)

        if chunk_type == 'chunk' and chunk_bytes and chunk_index >= len(buf['chunks']):
            buf['chunks'].extend([None] * (chunk_index + 1 - len(buf['chunks'])))
            buf['totalChunks'] = max(buf.get('totalChunks', 0), len(buf['chunks']))

        if chunk_type == 'chunk' and chunk_bytes and 0 <= chunk_index < len(buf['chunks']):
            if buf['chunks'][chunk_index] is None:
                buf['received'] += 1
                buf['total_bytes'] += len(chunk_bytes)
            buf['chunks'][chunk_index] = (offset, chunk_bytes)

        complete = buf['totalChunks'] > 0 and buf['received'] >= buf['totalChunks']
        if chunk_type == 'end' or complete:
            present_chunks = [c for c in buf['chunks'] if c is not None]
            if not complete and len(present_chunks) < buf['totalChunks']:
                print(f'[AUDIO] End with missing chunks fileId={file_id} chunks={len(present_chunks)}/{buf["totalChunks"]}; filling gaps with silence')

            if any(c[0] is not None for c in present_chunks):
                total_len = buf.get('expected_size') or max((c[0] or 0) + len(c[1]) for c in present_chunks)
                fill = 128 if int(buf.get('bits', 8)) == 8 else 0
                assembled = bytearray([fill] * total_len)
                if total_len >= 44:
                    sr = int(buf.get('sampleRate', 8000))
                    channels = int(buf.get('channels', 1))
                    bits = int(buf.get('bits', 8))
                    data_bytes = max(0, total_len - 44)
                    byte_rate = sr * channels * bits // 8
                    block_align = channels * bits // 8
                    header = bytearray(44)
                    header[0:4] = b'RIFF'
                    header[4:8] = (36 + data_bytes).to_bytes(4, 'little')
                    header[8:12] = b'WAVE'
                    header[12:16] = b'fmt '
                    header[16:20] = (16).to_bytes(4, 'little')
                    header[20:22] = (1).to_bytes(2, 'little')
                    header[22:24] = channels.to_bytes(2, 'little')
                    header[24:28] = sr.to_bytes(4, 'little')
                    header[28:32] = byte_rate.to_bytes(4, 'little')
                    header[32:34] = block_align.to_bytes(2, 'little')
                    header[34:36] = bits.to_bytes(2, 'little')
                    header[36:40] = b'data'
                    header[40:44] = data_bytes.to_bytes(4, 'little')
                    assembled[0:44] = header
                for chunk_offset, data in present_chunks:
                    if chunk_offset is None:
                        assembled.extend(data)
                    else:
                        assembled[chunk_offset:chunk_offset+len(data)] = data
                all_data = bytes(assembled[:buf.get('expected_size') or len(assembled)])
            else:
                all_data = b''.join([c[1] for c in present_chunks])

            if len(all_data) == 0:
                del audio_buffers[file_id]
                return None

            wav_filename = f"audio_{file_id}_{int(time.time()*1000)}.wav"
            wav_path = os.path.join(config.AUDIO_DIR, wav_filename)
            with open(wav_path, 'wb') as f:
                f.write(all_data)

            del audio_buffers[file_id]

            rms_val = calculate_wav_rms(all_data)
            print(f'[AUDIO] Assembled {wav_filename}  {len(all_data)} bytes  chunks={buf["received"]}/{buf["totalChunks"]}  rms={rms_val:.5f}')

            return {
                'type': 'audio_ready',
                'fileId': file_id,
                'filename': wav_filename,
                'url': f'/audio/{wav_filename}',
                'size': len(all_data),
                'chunks': buf['received'],
                'rms': round(rms_val, 5)
            }

        return {'type': 'audio_progress', 'fileId': file_id, 'chunk': chunk_index, 'received': buf['received'], 'total': buf['totalChunks']}


@bp.route('/api/audio-list')
def api_audio_list():
    """列出所有已组装的 WAV 文件"""
    files = []
    if os.path.isdir(config.AUDIO_DIR):
        wav_names = [f for f in os.listdir(config.AUDIO_DIR) if f.endswith('.wav')]
        wav_names.sort(key=lambda name: os.path.getmtime(os.path.join(config.AUDIO_DIR, name)), reverse=True)
        for f in wav_names:
            if f.endswith('.wav'):
                fp = os.path.join(config.AUDIO_DIR, f)
                files.append({'filename': f, 'url': f'/audio/{f}', 'size': os.path.getsize(fp), 'mtime': os.path.getmtime(fp)})
    return {'count': len(files), 'files': files[:50]}


@bp.route('/audio/<path:filename>')
def serve_audio(filename):
    return send_from_directory(config.AUDIO_DIR, filename, mimetype='audio/wav')
