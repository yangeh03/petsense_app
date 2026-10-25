"""Download public CC previews with positive FSD dog-whimper annotations."""
from pathlib import Path
import hashlib
import json
import subprocess
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent / 'data' / 'whine_fsd'
ROOT.mkdir(parents=True, exist_ok=True)
CATEGORY = 'https://fsannotator.upf.edu/fsd/explore/%252Ft%252Fdd00136/'
session = requests.Session()
page = session.get(CATEGORY, timeout=60)
page.raise_for_status()
soup = BeautifulSoup(page.text, 'html.parser')
records = []
candidates = []
for row in soup.select('tr'):
    link = row.find('a', href=lambda s: s and 'freesound.org/s/' in s)
    if not link:
        continue
    cells = [c.get_text(' ', strip=True) for c in row.select('td')]
    votes = [int(c) for c in cells if c.isdigit()]
    if len(votes) < 4 or sum(votes[:2]) < 1 or votes[2] > 0:
        continue
    sound_id = link['href'].rstrip('/').split('/')[-1]
    # Author describes this as a crying animal, under human sounds: ambiguous.
    if sound_id != '244448':
        continue
    candidates.append((sound_id, votes, CATEGORY))
# Explicit author-labeled whines, not all sounds tagged generically as dog.
for sound_id in ['118966', '118967', '118970', '505828', '462660', '495806', '556035']:
    candidates.append((sound_id, [], 'author title and description'))
for sound_id, votes, annotation_source in candidates:
    response = session.get('https://freesound.org/s/' + sound_id, timeout=60)
    response.raise_for_status()
    detail = BeautifulSoup(response.text, 'html.parser')
    player = detail.select_one('[data-mp3]')
    licenses = [a['href'] for a in detail.select('a[href]') if 'creativecommons.org/' in a['href']]
    if not player or not licenses:
        raise RuntimeError('Missing preview/license: ' + sound_id)
    url = player['data-mp3']
    mp3 = ROOT / (sound_id + '.mp3')
    if not mp3.exists():
        audio = session.get(url, timeout=60)
        audio.raise_for_status()
        mp3.write_bytes(audio.content)
    wav = ROOT / (sound_id + '.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(mp3), '-ac', '1', '-ar', '16000', str(wav)], check=True)
    record = {'id': sound_id, 'page': response.url, 'preview_url': url,
              'uploader': response.url.split('/people/')[1].split('/')[0],
              'license': licenses[0], 'votes_pp_pnp_np_u': votes[:4],
              'label': 'whine', 'wav': wav.name, 'sha256': hashlib.sha256(mp3.read_bytes()).hexdigest(),
              'annotation_source': annotation_source, 'title': detail.title.get_text(),
              'label_scope': 'weak clip-level positive; not time-localized events'}
    records.append(record)
    print(json.dumps(record), flush=True)
(ROOT / 'manifest.json').write_text(json.dumps(records, indent=2), encoding='utf8')
print('Downloaded', len(records), 'recordings', flush=True)
