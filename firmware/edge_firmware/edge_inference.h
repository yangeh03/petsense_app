#pragma once
#include "imu_model.h"
#include "audio_model.h"
#include "feature_test.h"

#ifndef PETSENSE_IMU_WINDOW_SAMPLES
#define PETSENSE_IMU_WINDOW_SAMPLES 20
#endif
#ifndef PETSENSE_IMU_MOTION_FEATURES
#define PETSENSE_IMU_MOTION_FEATURES 0
#endif

static constexpr int edgeImuWindowSamples = PETSENSE_IMU_WINDOW_SAMPLES;
static constexpr int edgeImuFeatureCount = PETSENSE_IMU_MOTION_FEATURES ? 30 : 12;
static constexpr int edgeAudioWindowSamples = 10;
static constexpr int edgeStrideSamples = 10;
static constexpr bool edgeIndependentWindows = edgeImuWindowSamples == 10;
static_assert(edgeImuWindowSamples == 10 || edgeImuWindowSamples == 20,
              "IMU inference expects a one- or two-second window");
static_assert(sizeof(imu_mean) / sizeof(imu_mean[0]) == edgeImuFeatureCount,
              "The IMU model and feature extractor must use the same input size");

struct EdgePrediction {
  const char* label;
  float confidence;
};

static constexpr EdgePrediction edgeWarmingUp = {"warming_up", 0};
static EdgePrediction edgeAction = edgeWarmingUp, edgeSound = edgeWarmingUp;
static float imuHistory[edgeImuWindowSamples][6], soundHistory[edgeAudioWindowSamples][40];
static float soundRms[edgeAudioWindowSamples];
static uint32_t edgeImuSamples = 0, edgeAudioSamples = 0, edgeLastAudioFrame = 0;
static String edgeSummary;
static String edgeLoggedPrediction = "null";
static bool edgeActionUpdated = false;

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
  float motion[edgeImuFeatureCount],acoustic[80],confidence;
  memcpy(motion,imu_test_input,sizeof(motion));memcpy(acoustic,audio_test_input,sizeof(acoustic));
  int a=predictMlp(motion,edgeImuFeatureCount,imu_mean,imu_scale,imu_w0,imu_b0,imu_w1,imu_b1,imu_w2,imu_b2,imu_class_count,confidence);
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

template<int Rows> static void summarizeImuWindow(float (&history)[Rows][6], float* output) {
  summarizeWindow(history,output);
#if PETSENSE_IMU_MOTION_FEATURES
  for (int sensor = 0; sensor < 2; sensor++) {
    float magnitude[Rows], mean = 0, change = 0, peakChange = 0, variance = 0;
    float minimum = INFINITY, maximum = 0;
    for (int i = 0; i < Rows; i++) {
      const float* v = history[i] + 3 * sensor;
      magnitude[i] = sqrtf(v[0]*v[0] + v[1]*v[1] + v[2]*v[2]);
      mean += magnitude[i] / Rows;
      minimum = fminf(minimum, magnitude[i]);
      maximum = fmaxf(maximum, magnitude[i]);
      if (i) {
        float delta = fabsf(magnitude[i] - magnitude[i-1]);
        change += delta / (Rows - 1);
        peakChange = fmaxf(peakChange, delta);
      }
    }
    for (int i = 0; i < Rows; i++) {
      float delta = magnitude[i] - mean;
      variance += delta * delta / Rows;
    }
    float* stats = output + 12 + 6 * sensor;
    stats[0] = mean;
    stats[1] = sqrtf(variance);
    stats[2] = minimum;
    stats[3] = maximum;
    stats[4] = change;
    stats[5] = peakChange;
  }
  for (int axis = 0; axis < 6; axis++) {
    float change = 0;
    for (int i = 1; i < Rows; i++) change += fabsf(history[i][axis] - history[i-1][axis]) / (Rows - 1);
    output[24 + axis] = change;
  }
#endif
}

// Called only by the sampler; inputs are taken from the same logged frame.
static void edgeStep(const String& raw, bool gap) {
  edgeActionUpdated=false;
  cJSON* frame=cJSON_Parse(raw.c_str());
  if(!frame)return;
  cJSON* imu=cJSON_GetObjectItem(frame,"imu");
  cJSON* audio=cJSON_GetObjectItem(frame,"audio");
  bool goodImu=cJSON_IsTrue(cJSON_GetObjectItem(imu,"ok"));
  bool goodAudio=cJSON_IsTrue(cJSON_GetObjectItem(audio,"ok"));
  bool fresh=audioFrameIndex!=edgeLastAudioFrame;edgeLastAudioFrame=audioFrameIndex;
  // The legacy two-second model retains the paired IMU/audio warmup.
  bool acceptImu = goodImu && (edgeIndependentWindows || (goodAudio && fresh));
  bool acceptAudio = goodAudio && fresh && (edgeIndependentWindows || goodImu);
  if (gap || !acceptImu) {
    edgeImuSamples = 0;
    edgeAction = edgeWarmingUp;
  }
  if (gap || !acceptAudio) {
    edgeAudioSamples = 0;
    edgeSound = edgeWarmingUp;
  }
  if (acceptImu) {
    int at = edgeImuSamples % edgeImuWindowSamples;
    for (int j = 0; j < 3; j++) {
      imuHistory[at][j] = imuData.accel[j];
      imuHistory[at][j+3] = imuData.gyro[j]*57.295779513f;
    }
    edgeImuSamples++;
  }
  if (acceptAudio) {
    int at = edgeAudioSamples % edgeAudioWindowSamples;
    float avg=0;for(int j=0;j<40;j++)avg+=audioMel[j]/40;
    for(int j=0;j<40;j++)soundHistory[at][j]=audioMel[j]-avg;
    float dc=0,ac=0;
    for(int j=0;j<256;j++)dc+=micSamples[j]/(32768.0f*256);
    for(int j=0;j<256;j++){float v=micSamples[j]/32768.0f-dc;ac+=v*v/256;}
    soundRms[at]=sqrtf(ac);
    edgeAudioSamples++;
  }
  bool imuReady = edgeImuSamples >= edgeImuWindowSamples;
  bool audioReady = edgeAudioSamples >= edgeAudioWindowSamples;
  bool inferAction = imuReady && edgeImuSamples % edgeStrideSamples == 0;
  bool inferSound = inferAction || (edgeIndependentWindows && edgeAudioSamples % edgeStrideSamples == 0);
  inferSound = audioReady && inferSound;
  if (!inferAction && !inferSound && imuReady && audioReady) {
    cJSON_Delete(frame);
    return;
  }
  float meanAcRms=0;for(int i=0;i<edgeAudioWindowSamples;i++)meanAcRms+=soundRms[i]/edgeAudioWindowSamples;
  uint64_t inferStart=esp_timer_get_time();
  if (inferAction) {
    float motion[edgeImuFeatureCount];
    summarizeImuWindow(imuHistory,motion);
    int label=predictMlp(motion,edgeImuFeatureCount,imu_mean,imu_scale,imu_w0,imu_b0,imu_w1,imu_b1,imu_w2,imu_b2,imu_class_count,edgeAction.confidence);
    edgeAction.label=edgeAction.confidence>=0.60f?imu_labels[label]:"unknown";
    edgeActionUpdated=true;
    cJSON *prediction=cJSON_CreateObject();
    cJSON_AddStringToObject(prediction,"model_version","edge-imu-1s-5class-mapped-20261125");
    cJSON_AddStringToObject(prediction,"label",edgeAction.label);
    cJSON_AddStringToObject(prediction,"top_label",imu_labels[label]);
    cJSON_AddNumberToObject(prediction,"confidence",edgeAction.confidence);
    cJSON_AddNumberToObject(prediction,"window_samples",edgeImuWindowSamples);
    const char *keys[]={"seq","mono_us","utc_us","boot_id"};
    for(const char *key:keys) {
      cJSON *v=cJSON_GetObjectItem(frame,key);
      if(v)cJSON_AddItemToObject(prediction,key,cJSON_Duplicate(v,true));
    }
    char *encoded=cJSON_PrintUnformatted(prediction);
    edgeLoggedPrediction=encoded?encoded:"null";
    cJSON_free(encoded);cJSON_Delete(prediction);
  }
  if (inferSound) {
    float acoustic[80];
    summarizeWindow(soundHistory,acoustic);
    int label=predictMlp(acoustic,80,audio_mean,audio_scale,audio_w0,audio_b0,audio_w1,audio_b1,audio_w2,audio_b2,audio_class_count,edgeSound.confidence);
    edgeSound.label=edgeSound.confidence>=0.60f?audio_labels[label]:"unknown";
    if(meanAcRms<0.001f)edgeSound={"quiet",0};
  }
  cJSON* result=cJSON_CreateObject();
  cJSON_AddStringToObject(result,"deviceId",DEVICE_ID);
  cJSON_AddStringToObject(result,"source","esp32_mlp");
  cJSON_AddStringToObject(result,"modelVersion","edge-dog-vocal-v3-whine");
  if (edgeIndependentWindows && imu_class_count == 5) {
    cJSON_AddStringToObject(result,"actionModelVersion","edge-imu-1s-5class-mapped-20261125");
  }
  cJSON_AddNumberToObject(result,"ts_ms",millis());
  const char* metadata[]={"boot_id","seq","mono_us","utc_us","sd_ready","capture_active","missed_deadlines","queue_drops","disk_errors","session_id","clock_age_us","clock_uncertainty_us","persisted_rows","sd_total_bytes","sd_used_bytes","sd_free_bytes","storage_draining","armed_marker","marker_ready","marker_id"};
  for(const char* key:metadata) {
    cJSON* v=cJSON_GetObjectItem(frame,key);
    if(v)cJSON_AddItemToObject(result,key,cJSON_Duplicate(v,true));
  }
  cJSON_AddNumberToObject(result,"action_window_samples",edgeImuWindowSamples);
  cJSON_AddNumberToObject(result,"window_samples",min((uint32_t)edgeImuWindowSamples,edgeImuSamples));
  cJSON_AddNumberToObject(result,"inference_us",esp_timer_get_time()-inferStart);
  cJSON* a=cJSON_AddObjectToObject(result,"action");
  cJSON_AddStringToObject(a,"label",edgeAction.label);cJSON_AddNumberToObject(a,"confidence",edgeAction.confidence);
  cJSON* b=cJSON_AddObjectToObject(result,"sound");
  cJSON_AddStringToObject(b,"label",edgeSound.label);cJSON_AddNumberToObject(b,"confidence",edgeSound.confidence);
  cJSON_AddNumberToObject(b,"ac_rms",meanAcRms);
  cJSON_AddBoolToObject(b,"experimental",strcmp(edgeSound.label,"whine")==0);
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
