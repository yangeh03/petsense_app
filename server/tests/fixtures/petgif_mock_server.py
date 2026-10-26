"""Local PetGIF API mock for simulator E2E only; never calls the external service."""
import io
import os
import time
from uuid import uuid4

from flask import Flask, jsonify, request, send_file
from PIL import Image, ImageDraw

app = Flask('petgif-mock')
PETS = {
    '0104965e-0ea3-4b79-92a6-975af775950b': ('Marni', 'coral'),
    'f02154ee-193d-4c6c-98e7-26d86ebf67ba': ('冬瓜', 'teal'),
    'personal-e2e': ('E2E Puppy', 'purple'),
}
DIARIES = {}


def media_url(path):
    return f"{os.environ.get('PETGIF_MOCK_PUBLIC', 'http://127.0.0.1:8094/storage/')}{path}"


def make_pet(pet_id):
    name, _ = PETS.get(pet_id, ('Test Pet', 'coral'))
    behaviors = {}
    for action in ('stand', 'sit', 'lie_down', 'walk', 'run', 'sniff', 'jump', 'eat', 'sleep'):
        behaviors[action] = {
            'status': 'complete',
            'assets': {
                'colorGif': {'publicUrl': media_url(f'{pet_id}/{action}.gif')},
                'video': {'publicUrl': None},
            },
            'states': {
                'interaction': {'assets': {'colorGif': {'publicUrl': media_url(f'{pet_id}/{action}-interaction.gif')}}},
                'barking': {'assets': {'colorGif': {'publicUrl': media_url(f'{pet_id}/{action}-barking.gif')}}},
            },
        }
    return {
        'id': pet_id,
        'name': name,
        'status': 'complete',
        'stage': 'complete',
        'profile': {'name': name, 'breed': '测试犬', 'traits': [], 'summary': ''},
        'characterCard': {'publicUrl': media_url(f'{pet_id}/card.png')},
        'behaviors': behaviors,
        'diaries': [],
    }


@app.get('/api/pets/<pet_id>')
def get_pet(pet_id):
    return jsonify({'success': True, 'pet': make_pet(pet_id)})


@app.post('/api/pets/<pet_id>/diaries')
def create_diary(pet_id):
    if pet_id not in PETS:
        return jsonify({'error': 'pet not found'}), 404
    if not request.files.getlist('photos'):
        return jsonify({'error': 'photo required'}), 400
    diary_id = str(uuid4())
    DIARIES[diary_id] = {'pet_id': pet_id, 'created': time.monotonic()}
    return jsonify({'diary': {'id': diary_id, 'status': 'queued'}}), 202


@app.get('/api/pets/<pet_id>/diaries/<diary_id>')
def get_diary(pet_id, diary_id):
    diary = DIARIES.get(diary_id)
    if not diary or diary['pet_id'] != pet_id:
        return jsonify({'error': 'diary not found'}), 404
    if time.monotonic() - diary['created'] < 3:
        return jsonify({'diary': {'id': diary_id, 'status': 'running'}})
    return jsonify({'diary': {
        'id': diary_id,
        'status': 'complete',
        'assets': {'image': {'publicUrl': media_url(f'{pet_id}/diaries/{diary_id}.png')}},
        'stats': {'date': time.strftime('%Y-%m-%d'), 'durationMin': 25, 'steps': 1200},
    }})


@app.get('/storage/<path:asset>')
def get_media(asset):
    output = io.BytesIO()
    if asset.lower().endswith('.gif'):
        frames = []
        for index, color in enumerate(((245, 130, 90), (255, 191, 95), (90, 166, 132))):
            image = Image.new('RGB', (320, 320), color)
            draw = ImageDraw.Draw(image)
            draw.ellipse((65 + index * 8, 68, 255, 258), fill=(255, 245, 225))
            draw.ellipse((111, 132, 135, 156), fill=(65, 54, 48))
            draw.ellipse((189, 132, 213, 156), fill=(65, 54, 48))
            frames.append(image)
        frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:], duration=250, loop=0)
        output.seek(0)
        return send_file(output, mimetype='image/gif')
    color = (119, 101, 165) if 'personal-e2e' in asset else (245, 130, 90)
    image = Image.new('RGB', (600, 600), color)
    draw = ImageDraw.Draw(image)
    draw.ellipse((100, 100, 500, 500), fill=(255, 245, 225))
    draw.ellipse((215, 240, 255, 280), fill=(65, 54, 48))
    draw.ellipse((345, 240, 385, 280), fill=(65, 54, 48))
    image.save(output, format='PNG')
    output.seek(0)
    return send_file(output, mimetype='image/png')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PETGIF_MOCK_PORT', '8094')), threaded=True)
