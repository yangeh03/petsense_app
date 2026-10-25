"""Train small two-hidden-layer MLPs and export exact float32 inference weights."""
import os
os.environ['OPENBLAS_NUM_THREADS']='2'
import json
import csv
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.signal import resample_poly
from scipy.io import wavfile
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, balanced_accuracy_score
import joblib

ROOT=Path(__file__).parent
DATA=ROOT/'data'
OUT=ROOT/'models'
OUT.mkdir(exist_ok=True)
RNG=np.random.default_rng(20261024)

def summary(window):
    return np.concatenate([window.mean(0), window.std(0)]).astype('float32')

def mel_filter():
    points=700*(10**(np.linspace(2595*np.log10(1+50/700),2595*np.log10(1+8000/700),42)/2595)-1)
    bins=np.clip(np.floor(257*points/16000).astype(int),0,128)
    filters=np.zeros((40,129),dtype='float32')
    for m in range(40):
        left,center,right=bins[m:m+3]
        if center<=left: center=left+1
        if right<=center: right=center+1
        if center>=129: center=127
        if right>=129: right=128
        if center<=left or right<=center: continue
        for k in range(left,center): filters[m,k]=(k-left)/(center-left)
        for k in range(center,right+1): filters[m,k]=(right-k)/(right-center)
    return filters

FILTER=mel_filter()
def audio_frames(pcm):
    # Match 256-point symmetric Hann, power / N, natural log and 100ms cadence.
    x=pcm.astype('float32')/32768
    frames=np.array([x[s:s+256] for s in range(0,len(x)-255,1600)])
    frames-=frames.mean(axis=1,keepdims=True)
    power=np.abs(np.fft.rfft(frames*np.hanning(256),axis=1))**2/256
    mel=np.log(power@FILTER.T+1e-9)
    # Remove overall gain in the model input, not in the logged features.
    mel-=mel.mean(axis=1,keepdims=True)
    return mel.astype('float32')

def train(name,x,y,split,classes,metadata):
    x=np.asarray(x,dtype='float32'); y=np.array(y); split=np.array(split)
    assert all(np.any(split==part) for part in ['train','val','test'])
    scaler=StandardScaler().fit(x[split=='train'])
    z=scaler.transform(x).astype('float32')
    best=None; best_score=-1
    import copy
    model=MLPClassifier(hidden_layer_sizes=(48,24),activation='relu',batch_size=64,
                        learning_rate_init=.001,alpha=.005,random_state=42,max_iter=1)
    weights=np.bincount(y[split=='train'],minlength=len(classes))
    if np.any(weights==0): raise ValueError(f'No training samples for class: {weights}')
    train_weights=len(y[split=='train'])/(len(classes)*weights[y[split=='train']])
    for epoch in range(180):
        model.partial_fit(z[split=='train'],y[split=='train'],classes=np.arange(len(classes)),sample_weight=train_weights)
        score=balanced_accuracy_score(y[split=='val'],model.predict(z[split=='val'])) if name=='audio' else accuracy_score(y[split=='val'],model.predict(z[split=='val']))
        if score>best_score:
            best_score=score;best=copy.deepcopy(model)
    model=best
    pred=model.predict(z[split=='test'])
    report={**metadata,'classes':classes,'split_counts':{p:int(sum(split==p)) for p in ['train','val','test']},
            'selection_metric':'validation balanced accuracy' if name=='audio' else 'validation accuracy',
            'selection_score':best_score,'validation_accuracy':accuracy_score(y[split=='val'],model.predict(z[split=='val'])),
            'test':classification_report(y[split=='test'],pred,labels=list(range(len(classes))),target_names=classes,output_dict=True,zero_division=0),
            'confusion_matrix':confusion_matrix(y[split=='test'],pred,labels=list(range(len(classes)))).tolist(),
            'parameters':sum(a.size for a in model.coefs_+model.intercepts_), 'architecture':[x.shape[1],48,24,len(classes)]}
    joblib.dump((model,scaler),OUT/(name+'.joblib'))
    (OUT/(name+'_report.json')).write_text(json.dumps(report,indent=2))
    arrays={'mean':scaler.mean_.astype('float32'),'scale':scaler.scale_.astype('float32')}
    for i,(w,b) in enumerate(zip(model.coefs_,model.intercepts_)):
        arrays['w'+str(i)]=w.astype('float32');arrays['b'+str(i)]=b.astype('float32')
    np.savez(OUT/(name+'.npz'),**arrays)
    def literal(v):
        text=f'{float(v):.9g}'
        if '.' not in text and 'e' not in text: text+='.0'
        return text+'f'
    lines=['#pragma once',f'// {name}: reproducible learned MLP weights; see model report and dataset licenses.']
    lines.append(f'static const char* {name}_labels[] = '+json.dumps(classes).replace('[','{').replace(']','}')+';')
    lines.append(f'static const int {name}_class_count = {len(classes)};')
    for key,a in arrays.items():
        lines.append(f'static const float {name}_{key}[{a.size}] = '+'{'+','.join(literal(v) for v in a.ravel())+'};')
    sample=x[split=='test'][0]
    lines.append(f'static const float {name}_test_input[{sample.size}] = '+'{'+','.join(literal(v) for v in sample)+'};')
    lines.append(f'static const int {name}_test_class = {int(pred[0])};')
    (OUT/(name+'_model.h')).write_text('\n'.join(lines)+'\n')
    # Independent float32 forward pass must agree with the training implementation.
    test=z[split=='test'][:100]
    forward=test
    for i in range(3):
        forward=forward@arrays['w'+str(i)]+arrays['b'+str(i)]
        if i<2:forward=np.maximum(forward,0)
    assert np.array_equal(forward.argmax(1),model.predict(test))
    print(name,json.dumps({'validation':best_score,'test_accuracy':report['test']['accuracy'],'counts':report['split_counts'],'parameters':report['parameters']}),flush=True)

def train_audio():
    classes=['dog','rain','crying_baby','clock_alarm','vacuum_cleaner']
    x=[];y=[];splits=[]
    for row in csv.DictReader((DATA/'esc50.csv').open()):
        if row['category'] not in classes:continue
        rate,pcm=wavfile.read(DATA/'audio'/row['filename'])
        if pcm.ndim==2:pcm=pcm.mean(1)
        pcm=np.clip(np.rint(resample_poly(pcm.astype(float),160,441)), -32768,32767).astype('int16')
        assert rate==44100
        features=audio_frames(pcm)
        part='test' if row['fold']=='5' else 'val' if row['fold']=='4' else 'train'
        for start in range(0,len(features)-19,10):
            x.append(summary(features[start:start+20]));y.append(classes.index(row['category']));splits.append(part)
    train('audio',x,y,splits,classes,{'dataset':'ESC-50 selected five classes','split':'official folds 1-3 train,4 validation,5 test; grouped source recordings','window_s':2,'feature':'40 gain-centered log-Mel means + stds, 256 samples/16kHz every100ms','license':'CC BY-NC 3.0; per-file attribution in ESC50_LICENSE'})

def train_imu():
    columns=['Timestamp','Subject','Position']+[f'Chest.{sensor}.{axis}' for sensor in ['Acc','Gyr'] for axis in 'XYZ']
    df=pd.read_csv(DATA/'df_raw.csv',usecols=columns)
    classes=['standing','sitting','lying down','walking']
    print('IMU labels',df.Position.value_counts().to_dict(),flush=True)
    dogs=np.array(sorted(df.Subject.unique()));RNG.shuffle(dogs)
    tests=set(dogs[:8]);vals=set(dogs[8:16])
    df['time']=pd.to_datetime(df.Timestamp)
    # Never join across dogs, annotation transitions, or gaps in the original stream.
    boundary=(df.Subject!=df.Subject.shift())|(df.Position!=df.Position.shift())|(df.time.diff().dt.total_seconds().sub(.01).abs()>.003)
    x=[];y=[];splits=[]
    for _,run in df.groupby(boundary.cumsum(),sort=False):
        label=run.Position.iloc[0];dog=run.Subject.iloc[0]
        if label not in classes or len(run)<220:continue
        values=run[columns[3:]].to_numpy(dtype=float)
        if not np.isfinite(values).all():continue
        # Anti-alias filtering before decimation, independently per contiguous run.
        values=resample_poly(values,1,10,axis=0,padtype='line')[1:-1]
        part='test' if dog in tests else 'val' if dog in vals else 'train'
        for start in range(0,len(values)-19,10):
            x.append(summary(values[start:start+20]));y.append(classes.index(label));splits.append(part)
    train('imu',x,y,splits,classes,{'dataset':'Mendeley mpph6bmn7g v1, chest only','source_hz':100,'target_hz':10,'window_s':2,'feature':'six-axis means + population stds; accelerometer g, gyro degrees/s','test_dogs':sorted(tests),'validation_dogs':sorted(vals),'train_dogs':sorted(set(dogs)-tests-vals),'license':'CC BY 4.0','caveat':'Chest mounting orientation must match training; not collar-validated.'})

if __name__=='__main__':
    import sys
    if sys.argv[1]=='audio':train_audio()
    else:train_imu()
