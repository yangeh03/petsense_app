#pragma once
#include "imu_model.h"
#include "audio_model.h"
#include "feature_test.h"

static float imuHistory[20][6], soundHistory[10][40];
static float soundRms[10];
static uint32_t edgeSamples=0, edgeLastAudioFrame=0;
static String edgeSummary;

static void denseLayer(const float* input, int n, const float* weights,
                       const float* bias, int outputs, float* result, bool relu) {
  for(int j=0;j<outputs;j++) {
    float v=bias[j];
    for(int i=0;i<n;i++) v+=input[i]*weights[i*outputs+j];
    result[j]=relu && v<0 ? 0 : v;
  }
}

static int predictMlp(float* input, int n, const float* mean, const float* scale,
                      const float* w0, const float* b0, const float* w1,
                      const float* b1, const float* w2, const float* b2, int classes, float& confidence) {
  float h0[48],h1[24],scores[8];
  for(int i=0;i<n;i++) input[i]=(input[i]-mean[i])/scale[i];
  denseLayer(input,n,w0,b0,48,h0,true);
  denseLayer(h0,48,w1,b1,24,h1,true);
  denseLayer(h1,24,w2,b2,classes,scores,false);
  int best=0;for(int i=1;i<classes;i++) if(scores[i]>scores[best])best=i;
  float sum=0;for(int i=0;i<classes;i++)sum+=expf(scores[i]-scores[best]);
  confidence=1/sum;return best;
}

static bool edgeSelfTest() {
  float motion[12],acoustic[80],confidence;
  memcpy(motion,imu_test_input,sizeof(motion));memcpy(acoustic,audio_test_input,sizeof(acoustic));
  int a=predictMlp(motion,12,imu_mean,imu_scale,imu_w0,imu_b0,imu_w1,imu_b1,imu_w2,imu_b2,imu_class_count,confidence);
  int b=predictMlp(acoustic,80,audio_mean,audio_scale,audio_w0,audio_b0,audio_w1,audio_b1,audio_w2,audio_b2,audio_class_count,confidence);
  bool ok=a==imu_test_class && b==audio_test_class;
  DebugSerial.printf("EDGE_MLP_SELF_TEST %s imu=%d audio=%d\n",ok?"PASS":"FAIL",a,b);
  return ok;
}

static bool edgeFeatureSelfTest() {
  float rms,zcr,mel[40],error=0;
  bool ok=computeAudioFeatures(feature_test_pcm,256,rms,zcr,mel);
  for(int i=0;i<40;i++)error=fmaxf(error,fabsf(mel[i]-feature_test_mel[i]));
  ok=ok && error<0.02f;
  DebugSerial.printf("EDGE_FEATURE_SELF_TEST %s max_logmel_error=%f\n",ok?"PASS":"FAIL",error);
  return ok;
}

template<int Rows,int N> static void summarizeWindow(float (&history)[Rows][N], float* output) {
  for(int j=0;j<N;j++) {
    float mean=0,variance=0;
    for(int i=0;i<Rows;i++)mean+=history[i][j]/Rows;
    for(int i=0;i<Rows;i++){float d=history[i][j]-mean;variance+=d*d/Rows;}
    output[j]=mean;output[N+j]=sqrtf(variance);
  }
}

// Called only by the sampler; inputs are taken from the same logged frame.
static void edgeStep(const String& raw, bool gap) {
  cJSON* frame=cJSON_Parse(raw.c_str());
  if(!frame)return;
  cJSON* imu=cJSON_GetObjectItem(frame,"imu");
  cJSON* audio=cJSON_GetObjectItem(frame,"audio");
  bool goodImu=cJSON_IsTrue(cJSON_GetObjectItem(imu,"ok"));
  bool goodAudio=cJSON_IsTrue(cJSON_GetObjectItem(audio,"ok"));
  bool fresh=audioFrameIndex!=edgeLastAudioFrame;edgeLastAudioFrame=audioFrameIndex;
  if(gap || !goodImu || !goodAudio || !fresh)edgeSamples=0;
  if(goodImu && goodAudio && fresh) {
    int at=edgeSamples%20;
    float avg=0;for(int j=0;j<40;j++)avg+=audioMel[j]/40;
    for(int j=0;j<3;j++) {
      imuHistory[at][j]=imuData.accel[j];
      imuHistory[at][j+3]=imuData.gyro[j]*57.295779513f;
    }
    for(int j=0;j<40;j++)soundHistory[edgeSamples%10][j]=audioMel[j]-avg;
    float dc=0,ac=0;
    for(int j=0;j<256;j++)dc+=micSamples[j]/(32768.0f*256);
    for(int j=0;j<256;j++){float v=micSamples[j]/32768.0f-dc;ac+=v*v/256;}
    soundRms[edgeSamples%10]=sqrtf(ac);
    edgeSamples++;
  }
  const char* action="warming_up";const char* sound="warming_up";
  float actionConfidence=0,soundConfidence=0;
  float meanAcRms=0;for(int i=0;i<10;i++)meanAcRms+=soundRms[i]/10;
  uint64_t inferStart=esp_timer_get_time();
  if(edgeSamples>=20 && edgeSamples%10==0) {
    float motion[12],acoustic[80];
    summarizeWindow(imuHistory,motion);summarizeWindow(soundHistory,acoustic);
    int a=predictMlp(motion,12,imu_mean,imu_scale,imu_w0,imu_b0,imu_w1,imu_b1,imu_w2,imu_b2,imu_class_count,actionConfidence);
    int b=predictMlp(acoustic,80,audio_mean,audio_scale,audio_w0,audio_b0,audio_w1,audio_b1,audio_w2,audio_b2,audio_class_count,soundConfidence);
    action=actionConfidence>=0.60f?imu_labels[a]:"unknown";
    sound=soundConfidence>=0.60f?audio_labels[b]:"unknown";
    if(meanAcRms<0.001f){sound="quiet";soundConfidence=0;}
  } else if(edgeSamples>=20) {cJSON_Delete(frame);return;}
  cJSON* result=cJSON_CreateObject();
  cJSON_AddStringToObject(result,"deviceId",DEVICE_ID);
  cJSON_AddStringToObject(result,"source","esp32_mlp");
  cJSON_AddStringToObject(result,"modelVersion","edge-dog-vocal-v3-whine");
  cJSON_AddNumberToObject(result,"ts_ms",millis());
  const char* metadata[]={"boot_id","seq","mono_us","utc_us","sd_ready","capture_active","missed_deadlines","queue_drops","disk_errors",
    "session_id","clock_age_us","clock_uncertainty_us","persisted_rows","sd_total_bytes","sd_used_bytes","sd_free_bytes","storage_draining","armed_marker","marker_ready","marker_id"};
  for(const char* key:metadata) {
    cJSON* v=cJSON_GetObjectItem(frame,key);
    if(v)cJSON_AddItemToObject(result,key,cJSON_Duplicate(v,true));
  }
  cJSON_AddNumberToObject(result,"window_samples",min((uint32_t)20,edgeSamples));
  cJSON_AddNumberToObject(result,"inference_us",esp_timer_get_time()-inferStart);
  cJSON* a=cJSON_AddObjectToObject(result,"action");
  cJSON_AddStringToObject(a,"label",action);cJSON_AddNumberToObject(a,"confidence",actionConfidence);
  cJSON* b=cJSON_AddObjectToObject(result,"sound");
  cJSON_AddStringToObject(b,"label",sound);cJSON_AddNumberToObject(b,"confidence",soundConfidence);
  cJSON_AddNumberToObject(b,"ac_rms",meanAcRms);
  cJSON_AddBoolToObject(b,"experimental",strcmp(sound,"whine")==0);
  cJSON* health=cJSON_GetObjectItem(frame,"health");
  if(health)cJSON_AddItemToObject(result,"health",cJSON_Duplicate(health,true));
  cJSON* battery=cJSON_GetObjectItem(frame,"battery");
  if(battery)cJSON_AddItemToObject(result,"battery",cJSON_Duplicate(battery,true));
  // UWB remains available during model warmup or invalid IMU/audio windows.
  cJSON* uwb=cJSON_GetObjectItem(frame,"uwb");
  if(uwb)cJSON_AddItemToObject(result,"uwb",cJSON_Duplicate(uwb,true));
  cJSON* gps=cJSON_GetObjectItem(frame,"gps");
  if(gps)cJSON_AddItemToObject(result,"gps",cJSON_Duplicate(gps,true));
  char* text=cJSON_PrintUnformatted(result);
  if(text){edgeSummary=text;cJSON_free(text);}
  cJSON_Delete(result);cJSON_Delete(frame);
}
