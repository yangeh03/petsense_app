"""Pilot dog vocalization classifier: expert event labels plus published howl data."""
from pathlib import Path
import csv
import json
import math
import subprocess
import numpy as np
import pandas as pd
import soundfile as sf
from scipy.signal import resample_poly
from train import train, audio_frames, summary, ROOT, DATA, OUT

classes=['bark','howl','whine','other']
mapping={'Bark/yelp':'bark','Whine':'whine','Pant':'other','Human':'other'}
# Participant IDs are held out before cutting any overlapping windows.
test_dogs={'P-06','P-13','P-04','P-12'}
val_dogs={'P-03','P-05','P-09','P-14'}
root=DATA/'dog_vocal'
waves={p.stem:p for p in root.rglob('*.wav') if not p.name.startswith('._')}
if not waves:raise RuntimeError('No verified session audio extracted yet')
x=[];y=[];splits=[];records=[]
def add(pcm, label, split, meta):
    if len(pcm)!=16000: return
    features=audio_frames(pcm)
    assert features.shape==(10,40)
    x.append(summary(features));y.append(classes.index(label));splits.append(split)
    records.append({**meta,'label':label,'split':split})

for table in sorted((root/'04 Annotations/SecondLayer').glob('*.Table')):
    stem=table.stem;dog=stem[:4]
    if stem not in waves:raise RuntimeError('Missing '+stem)
    audio,rate=sf.read(waves[stem],dtype='float32',always_2d=True)
    audio=resample_poly(audio.mean(1),16000//math.gcd(rate,16000),rate//math.gcd(rate,16000))
    pcm=np.clip(np.rint(audio*32768),-32768,32767).astype('int16')
    annotations=pd.read_csv(table,sep='\t');annotations['text']=annotations.text.str.strip()
    split='test' if dog in test_dogs else 'val' if dog in val_dogs else 'train'
    other_count=0
    for index,event in annotations.iterrows():
        label=mapping.get(event.text)
        if label is None or event.tmax-event.tmin<.1:continue
        if label=='other':
            other_count+=1
            if other_count>20 or event.tmax-event.tmin<1:continue
        centers=[(event.tmin+event.tmax)/2] if event.tmax-event.tmin<1 else np.arange(event.tmin+.5,event.tmax-.49,.5)
        for center in centers:
            start=max(0,min(len(pcm)/16000-1,float(center)-.5));end=start+1
            conflicting=annotations[(annotations.tmin<end)&(annotations.tmax>start)&annotations.text.isin(['Bark/yelp','Growl','Whine'])&(annotations.text!=event.text)]
            if len(conflicting):continue
            offsets=[0,.02,.04,.06,.08] if split=='train' and label!='other' else [0]
            for offset in offsets:
                shifted=max(0,min(len(pcm)/16000-1,start+offset))
                sample=pcm[round(shifted*16000):round(shifted*16000)+16000]
                add(sample,label,split,{'source':str(waves[stem]),'dog':dog,'event':int(index),'start_s':shifted,'duration_s':1,'dataset':'zenodo.18972388','phase_augmentation_s':offset})

# Published clips have no dog IDs: recording-level, NOT dog-independent, partition.
rng=np.random.default_rng(42)
for category in ['Bark','Howl']:
    files=sorted((DATA/'bark_howl'/category).glob('*.wav'))
    rng.shuffle(files)
    n=len(files)
    if n<30:raise RuntimeError('Incomplete supplement: '+category)
    for i,path in enumerate(files):
        split='test' if i<n//5 else 'val' if i<2*(n//5) else 'train'
        a,rate=sf.read(path,dtype='float32',always_2d=True)
        a=resample_poly(a.mean(1),16000//math.gcd(rate,16000),rate//math.gcd(rate,16000))
        pcm=np.clip(np.rint(a*32768),-32768,32767).astype('int16')
        for start in range(0,min(len(pcm)-15999,16000*10),16000):
            sample=pcm[start:start+16000]
            if np.std(sample.astype(float))<10:continue
            add(sample,category.lower(),split,{'source':str(path),'dog':None,'start_s':start/16000,'duration_s':1,'dataset':'Karaaslan2024'})

# Preserve uploader groups: adjacent recordings may be the same animal/session.
manifest=json.loads((DATA/'whine_fsd/manifest.json').read_text())
seen_hashes=set()
for record in manifest:
    if record['sha256'] in seen_hashes:continue
    seen_hashes.add(record['sha256'])
    split='test' if record['uploader']=='jedg' else 'val' if record['uploader']=='Breviceps' else 'train'
    path=DATA/'whine_fsd'/record['wav']
    pcm,rate=sf.read(path,dtype='int16')
    assert rate==16000 and pcm.ndim==1
    for start in range(0,min(len(pcm)-15999,16000*60),16000):
        offsets=[0,320,640,960,1280] if split=='train' else [0]
        for offset in offsets:
            sample=pcm[start+offset:start+offset+16000]
            if len(sample)!=16000 or np.std(sample.astype(float))<32:continue
            add(sample,'whine',split,{'source':str(path),'dog':None,'uploader':record['uploader'],
                'start_s':(start+offset)/16000,'duration_s':1,'dataset':'Freesound author/FSD weak labels',
                'recording_id':record['id'],'phase_augmentation_s':offset/16000,'license':record['license']})

assert not any(r['label']=='growl' for r in records)
print(pd.crosstab(pd.Series(splits,name='split'),pd.Series([classes[v] for v in y],name='class')).to_string(),flush=True)
train('audio',x,y,splits,classes,{'dataset':'Zenodo 18972388 + Karaaslan2024 Bark/Howl + Freesound whine supplement',
    'supplement_manifest':'data/whine_fsd/manifest.json','supplement_recordings':len(manifest),
    'window_s':1,'feature':'10Hz frame-DC removal, 256-point Hann power log-Mel40 gain centered, mean + std',
    'test_dogs':sorted(test_dogs),'validation_dogs':sorted(val_dogs),
    'license':'Zenodo metadata CC BY4.0 but included README CC BY-NC4.0: use research/noncommercial pending clarification. Supplement repository MIT; preserve original recording provenance.',
    'caveats':['Whine supplement has weak whole-recording labels, not expert event boundaries; uploader-grouped holdout, not guaranteed dog independence.',
               'Growl is excluded, not a recognized class. Unknown growls can still be confused with supported classes.',
               'Primary Bark/yelp category includes yelps.',
               'Howl supplement lacks dog IDs; source/domain confounding and identity leakage cannot be excluded.',
               'Test windows from the same event/recording are correlated; report is window-level, not independent dog accuracy.',
               'Other covers selected pant/human examples, not all unknown sounds.']})
(OUT/'dog_audio_windows.json').write_text(json.dumps(records,indent=2))
out=ROOT/'dog_test_audio';out.mkdir(exist_ok=True)
examples=[]
for label in classes[:3]:
    record=next(r for r in records if r['label']==label and r['split']=='test')
    # Repeat the held-out one-second segment three times with one-second quiet gaps.
    a,rate=sf.read(record['source'],dtype='float32',always_2d=True)
    clip=a[round(record['start_s']*rate):round((record['start_s']+1)*rate)].mean(1)
    playback=np.concatenate([np.zeros(rate),clip,np.zeros(rate),clip,np.zeros(rate),clip,np.zeros(rate)])
    wav=out/(label+'.wav');mp3=out/(label+'.mp3');sf.write(wav,playback,rate)
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(wav),'-c:a','libmp3lame','-b:a','192k',str(mp3)],check=True)
    subprocess.run(['ffmpeg','-v','error','-i',str(mp3),'-f','null','-'],check=True)
    examples.append({**record,'mp3':mp3.name,'conversion':'held-out 1s segment repeated 3 times with 1s silence; MP3 192kbps; first eligible example, not selected by prediction'})
(out/'sources.json').write_text(json.dumps(examples,indent=2))
