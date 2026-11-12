#pragma once
#include "edge_inference.h"
#include "uwb_runtime.h"

// Sampling owns all sensor drivers; storage owns writes; Arduino loop owns modem AT.
static constexpr size_t RECORD_MAX = 3072;
struct CaptureRecord { uint16_t length; char json[RECORD_MAX]; };
struct CaptureCommand { char type[32]; char session[81]; char marker[81]; };
struct ClockAnchor { int64_t mono; int64_t utc; uint64_t uncertainty; uint32_t version; };
static QueueHandle_t storageQueue, commandQueue;
static SemaphoreHandle_t diskMutex, liveMutex;
static portMUX_TYPE stateMux = portMUX_INITIALIZER_UNLOCKED;
static ClockAnchor clockAnchor = {};
static char bootId[32], activeSession[81] = {}, markerId[81] = {};
static volatile bool cardReady = false, captureActive = false, diskFault = false;
static volatile uint32_t sampleSequence = 0, missedDeadlines = 0, queueDrops = 0, diskErrors = 0;
static volatile uint32_t persistedRows = 0;
static String latestLive, pendingBatch, pendingId, acknowledgedBatch;
static String auditReport = "null";
static uint32_t lastStatus = 0;
static String replayPath;
static bool replayDone = false;
static uint32_t replayOffset = 0, pendingEnd = 0;
static uint32_t checkpointOffset = 0, networkPauseUntil = 0;
static uint64_t clockNonce = 0, clockSentUs = 0;
static uint32_t lastClockSent = 0, lastPublish = 0;
static uint32_t lastBatchAttempt = 0;
static File journal;
static String journalPath;
static uint32_t journalDurable = 0;
static uint64_t sdTotalBytes = 0, sdUsedBytes = 0;
static bool sdSpaceKnown = false, storageDraining = false;

// Only the storage worker touches the filesystem; telemetry reads this cache.
static void refreshStorageSpace() {
  uint64_t total = cardReady ? SD_MMC.totalBytes() : 0;
  uint64_t used = cardReady ? SD_MMC.usedBytes() : 0;
  portENTER_CRITICAL(&stateMux);
  sdTotalBytes = total; sdUsedBytes = used;
  sdSpaceKnown = total > 0 && used <= total;
  portEXIT_CRITICAL(&stateMux);
}

static uint32_t recordCrc(const uint8_t *data, size_t size) {
  uint32_t crc = 0xffffffff;
  for (size_t i=0; i<size; ++i) {
    crc ^= data[i];
    for (int bit=0; bit<8; ++bit) crc = (crc >> 1) ^ (0xedb88320 & -(int32_t)(crc & 1));
  }
  return ~crc;
}
static String decimal64(int64_t value) { char text[32]; snprintf(text,sizeof(text),"%lld",(long long)value); return String(text); }
static const char *jsonText(cJSON *object, const char *key) {
  cJSON *value=cJSON_GetObjectItemCaseSensitive(object,key);
  return cJSON_IsString(value) ? value->valuestring : "";
}
static int64_t jsonInteger(cJSON *object,const char *key) {
  cJSON *value=cJSON_GetObjectItemCaseSensitive(object,key);
  return cJSON_IsNumber(value) ? (int64_t)value->valuedouble : 0;
}
static bool safeId(const char *value) {
  size_t length=strlen(value); if (!length || length>80) return false;
  for(size_t i=0;i<length;i++) if(!isalnum(value[i]) && value[i]!='_' && value[i]!='-') return false;
  return true;
}

bool collectionHandleCommand() {
  if (modemAsyncBuffer.indexOf("collection_")<0 && modemAsyncBuffer.indexOf("clock_reply")<0) return false;
  if (modemAsyncBuffer.indexOf("+CMQTTRXEND")<0) return true;
  int first=modemAsyncBuffer.indexOf('{'), last=modemAsyncBuffer.lastIndexOf('}');
  String body=modemAsyncBuffer.substring(first,last+1); modemAsyncBuffer="";
  cJSON *doc=cJSON_Parse(body.c_str()); if(!doc) return true;
  String type=jsonText(doc,"type");
  if(type=="collection_pause_transport") {
    int64_t seconds=jsonInteger(doc,"seconds");
    if(seconds>0 && seconds<=60) networkPauseUntil=millis()+seconds*1000;
  } else if(type=="collection_ack") {
    acknowledgedBatch=jsonText(doc,"batch");
  } else if(type=="clock_reply" && String(jsonText(doc,"boot"))==bootId &&
            (uint64_t)jsonInteger(doc,"nonce")==clockNonce && clockSentUs) {
    int64_t t3=esp_timer_get_time(), t0=jsonInteger(doc,"t0_us");
    int64_t t1=jsonInteger(doc,"t1_us"), t2=jsonInteger(doc,"t2_us");
    int64_t rtt=(t3-t0)-(t2-t1);
    if(t0==(int64_t)clockSentUs && rtt>=0 && rtt<10000000 && t1>1700000000000000LL) {
      portENTER_CRITICAL(&stateMux);
      if(!clockAnchor.utc || (uint64_t)(rtt/2)<=clockAnchor.uncertainty || t3-clockAnchor.mono>300000000) {
        clockAnchor.mono=t3; clockAnchor.utc=t2+rtt/2; clockAnchor.uncertainty=rtt/2;
        clockAnchor.version++;
      }
      portEXIT_CRITICAL(&stateMux);
    }
  } else if(type=="collection_start" || type=="collection_stop" || type=="collection_arm" || type=="collection_audit") {
    CaptureCommand cmd={};
    strlcpy(cmd.type,type.c_str(),sizeof(cmd.type));
    strlcpy(cmd.session,jsonText(doc,"session"),sizeof(cmd.session));
    strlcpy(cmd.marker,jsonText(doc,"marker"),sizeof(cmd.marker));
    if(safeId(cmd.session)) xQueueSend(commandQueue,&cmd,0);
  }
  cJSON_Delete(doc); return true;
}

static void samplingWorker(void *) {
  TickType_t wake=xTaskGetTickCount(); uint32_t sequence=0;
  uint64_t last=0; uint32_t stillSamples=0; bool armed=false;
  char seenMarker[81]={};
  while(true) {
    uint64_t start=esp_timer_get_time();
    bool sampleGap=last && start-last>150000;
    if(sampleGap) { uint32_t lost=(start-last)/100000-1; missedDeadlines+=lost; sequence+=lost; }
    last=start;
    serviceHealthModule(); captureAudioFeatureStep();
    // Snapshot before buildCloudPayload stamps ts_ms: receivedMs must not be
    // newer than the frame timestamp when RX runs concurrently on the other core.
    const UwbPacket uwb=snapshotUwb();
    const GnssSnapshot gps=snapshotGnss();
    String payload=buildCloudPayload();
    appendUwbSnapshot(payload,uwb);
    appendGnssSnapshot(payload,gps);
    char session[81], marker[81]; bool active, spaceKnown, draining; ClockAnchor anchor;
    uint64_t totalBytes, usedBytes;
    portENTER_CRITICAL(&stateMux);
    active=captureActive; strlcpy(session,activeSession,sizeof(session));
    strlcpy(marker,markerId,sizeof(marker)); anchor=clockAnchor;
    totalBytes=sdTotalBytes; usedBytes=sdUsedBytes; spaceKnown=sdSpaceKnown; draining=storageDraining;
    portEXIT_CRITICAL(&stateMux);
    if(strcmp(seenMarker,marker)!=0) {strlcpy(seenMarker,marker,sizeof(seenMarker));stillSamples=0;armed=false;}
    float magnitude=sqrtf(imuData.accel[0]*imuData.accel[0]+imuData.accel[1]*imuData.accel[1]+imuData.accel[2]*imuData.accel[2]);
    bool trigger=false;
    if(marker[0]) {
      if(fabsf(magnitude-1.0f)<0.12f) stillSamples++; else if(!armed) stillSamples=0;
      if(stillSamples>=20) armed=true;
      if(armed && fabsf(magnitude-1.0f)>0.65f) {
        trigger=true;
        portENTER_CRITICAL(&stateMux);markerId[0]=0;portEXIT_CRITICAL(&stateMux);
      }
    }
    payload.remove(payload.length()-1);
    payload+=",\"schema_version\":2,\"boot_id\":\""+String(bootId)+"\",\"seq\":"+String(sequence);
    payload+=",\"session_id\":\""+String(active?session:"")+"\",\"mono_us\":"+decimal64(start);
    payload+=",\"utc_us\":"+(anchor.utc?decimal64(anchor.utc+(int64_t)start-anchor.mono):String("null"));
    payload+=",\"clock_version\":"+String(anchor.version)+",\"clock_uncertainty_us\":"+decimal64(anchor.uncertainty);
    payload+=",\"clock_age_us\":"+decimal64(anchor.utc?(int64_t)start-anchor.mono:0);
    payload+=",\"missed_deadlines\":"+String(missedDeadlines)+",\"queue_drops\":"+String(queueDrops);
    payload+=",\"disk_errors\":"+String(diskErrors)+",\"persisted_rows\":"+String(persistedRows);
    payload+=",\"sd_ready\":"+String(cardReady?"true":"false")+",\"capture_active\":"+String(active?"true":"false");
    payload+=",\"sd_total_bytes\":"+(spaceKnown?decimal64(totalBytes):String("null"));
    payload+=",\"sd_used_bytes\":"+(spaceKnown?decimal64(usedBytes):String("null"));
    payload+=",\"sd_free_bytes\":"+(spaceKnown?decimal64(totalBytes-usedBytes):String("null"));
    payload+=",\"storage_draining\":"+String(draining?"true":"false");
    payload+=",\"armed_marker\":\""+String(marker)+"\",\"marker_ready\":"+String(armed?"true":"false");
    payload+=",\"capture_cost_us\":"+decimal64(esp_timer_get_time()-start);
    payload+=",\"marker_id\":\""+String(trigger?marker:"")+"\"}";
    edgeStep(payload,sampleGap);
    sampleSequence=sequence++;
    if(xSemaphoreTake(liveMutex,0)==pdTRUE) {latestLive=edgeSummary; xSemaphoreGive(liveMutex);}
    if(active) {
      CaptureRecord record={}; record.length=payload.length();
      bool dropped=false;
      if(record.length>=RECORD_MAX) {dropped=true;}
      else {
        memcpy(record.json,payload.c_str(),record.length);
        if(xQueueSend(storageQueue,&record,0)!=pdTRUE) dropped=true;
      }
      if(dropped) {
        queueDrops++;diskFault=true;
        portENTER_CRITICAL(&stateMux);captureActive=false;portEXIT_CRITICAL(&stateMux);
      }
    }
    // Do not fabricate catch-up samples; report the missed slots explicitly.
    if(esp_timer_get_time()-start>=100000) wake=xTaskGetTickCount();
    vTaskDelayUntil(&wake,pdMS_TO_TICKS(100));
  }
}

static String auditCard() {
  String result="{\"card_bytes\":"+decimal64(SD_MMC.cardSize())+",\"files\":[";
  File dir=SD_MMC.open("/collection_v2"); bool comma=false;
  for(File file=dir.openNextFile();file;file=dir.openNextFile()) {
    if(file.isDirectory() || !String(file.path()).endsWith(".psj")) {file.close();continue;}
    uint32_t rows=0, first=0, last=0, gaps=0, sum=0, validBytes=0, errors=0;
    uint32_t size=file.size(); String path=file.path();
    while(file.available()) {
      uint32_t header[3]; char data[RECORD_MAX];
      if(file.read((uint8_t *)header,12)!=12 || header[0]!=0x32534e50 || !header[1] || header[1]>=RECORD_MAX ||
         file.read((uint8_t *)data,header[1])!=header[1] || recordCrc((uint8_t *)data,header[1])!=header[2]) {errors++;break;}
      data[header[1]]=0; cJSON *doc=cJSON_Parse(data);
      if(!doc) {errors++;break;}
      uint32_t seq=jsonInteger(doc,"seq"); cJSON_Delete(doc);
      if(!rows)first=seq;else if(seq!=last+1)gaps++;
      last=seq;rows++;sum+=header[2];validBytes+=12+header[1];
    }
    file.close();
    if(comma)result+=',';comma=true;
    result+="{\"path\":\""+path+"\",\"rows\":"+String(rows)+",\"first\":"+String(first)+",\"last\":"+String(last);
    result+=",\"gaps\":"+String(gaps)+",\"crc_sum\":"+String(sum)+",\"errors\":"+String(errors)+",\"bytes\":"+String(size)+",\"valid_bytes\":"+String(validBytes)+"}";
    if(result.length()>7000)break;
  }
  dir.close();return result+"]}";
}

static void storageWorker(void *) {
  uint32_t unflushed=0, lastFlush=millis();
  xSemaphoreTake(diskMutex,portMAX_DELAY);
  SD_MMC.setPins(SDMMC_CLK_PIN,SDMMC_CMD_PIN,SDMMC_D0_PIN);
  cardReady=SD_MMC.begin("/sdcard",true,false,SDMMC_INIT_FREQ_KHZ,8) && SD_MMC.cardType()!=CARD_NONE;
  if(cardReady) {
    refreshStorageSpace();
    String report=auditCard();
    xSemaphoreTake(liveMutex,portMAX_DELAY);auditReport=report;xSemaphoreGive(liveMutex);
  }
  xSemaphoreGive(diskMutex);
  CaptureRecord record; uint32_t lastSpace=millis(), lastStop=0;
  while(true) {
    CaptureCommand command;
    if(xQueueReceive(commandQueue,&command,0)==pdTRUE) {
      portENTER_CRITICAL(&stateMux);
      bool clockReady=clockAnchor.utc && esp_timer_get_time()-clockAnchor.mono<300000000;
      portEXIT_CRITICAL(&stateMux);
      if(strcmp(command.type,"collection_start")==0 && !captureActive && !diskFault && clockReady) {
        if(xSemaphoreTake(diskMutex,pdMS_TO_TICKS(500))==pdTRUE) {
          if(!cardReady) {
            SD_MMC.setPins(SDMMC_CLK_PIN,SDMMC_CMD_PIN,SDMMC_D0_PIN);
            cardReady=SD_MMC.begin("/sdcard",true,false,SDMMC_INIT_FREQ_KHZ,8) && SD_MMC.cardType()!=CARD_NONE;
          }
          refreshStorageSpace();
          if(cardReady && sdSpaceKnown && sdTotalBytes-sdUsedBytes>1024*1024 && uxQueueMessagesWaiting(storageQueue)==0) {
            if(journal) {journal.flush();journal.close();}
            SD_MMC.mkdir("/collection_v2");
            journalPath="/collection_v2/"+String(command.session)+"_"+String(bootId)+".psj";
            // Never overwrite a previous session. Resume with the same file if retried.
            journal=SD_MMC.open(journalPath,FILE_APPEND);
            if(journal) {
              if(replayDone) {replayPath=journalPath;replayOffset=0;replayDone=false;}
              journalDurable=journal.size();persistedRows=0;
              portENTER_CRITICAL(&stateMux);
              strlcpy(activeSession,command.session,sizeof(activeSession));markerId[0]=0;captureActive=true;
              portEXIT_CRITICAL(&stateMux);
            } else {diskErrors++;diskFault=true;}
          }
          xSemaphoreGive(diskMutex);
        }
      } else if(strcmp(command.type,"collection_audit")==0 && !captureActive && uxQueueMessagesWaiting(storageQueue)==0 && cardReady) {
        xSemaphoreTake(diskMutex,portMAX_DELAY);
        if(journal) {journal.flush();journalDurable=journal.size();persistedRows+=unflushed;unflushed=0;}
        String report=auditCard();
        xSemaphoreGive(diskMutex);
        xSemaphoreTake(liveMutex,portMAX_DELAY);auditReport=report;xSemaphoreGive(liveMutex);
      } else if(strcmp(command.type,"collection_stop")==0) {
        portENTER_CRITICAL(&stateMux);
        if(strcmp(command.session,activeSession)==0) {captureActive=false;markerId[0]=0;lastStop=millis();storageDraining=true;}
        portEXIT_CRITICAL(&stateMux);
      } else if(strcmp(command.type,"collection_arm")==0 && safeId(command.marker)) {
        portENTER_CRITICAL(&stateMux);
        if(captureActive && strcmp(command.session,activeSession)==0) strlcpy(markerId,command.marker,sizeof(markerId));
        portEXIT_CRITICAL(&stateMux);
      }
    }
    if(xQueueReceive(storageQueue,&record,pdMS_TO_TICKS(10))==pdTRUE) {
      portENTER_CRITICAL(&stateMux);storageDraining=true;portEXIT_CRITICAL(&stateMux);
      xSemaphoreTake(diskMutex,portMAX_DELAY);
      uint32_t header[]={0x32534e50,record.length,recordCrc((uint8_t *)record.json,record.length)};
      if(!journal || journal.write((uint8_t *)header,sizeof(header))!=sizeof(header) ||
          journal.write((uint8_t *)record.json,record.length)!=record.length) {
        diskErrors++; diskFault=true;
        portENTER_CRITICAL(&stateMux);captureActive=false;portEXIT_CRITICAL(&stateMux);
      } else unflushed++;
      xSemaphoreGive(diskMutex);
    }
    if(journal && (unflushed>=10 || (unflushed && millis()-lastFlush>=1000))) {
      xSemaphoreTake(diskMutex,portMAX_DELAY);
      journal.flush(); journalDurable=journal.size(); persistedRows+=unflushed; unflushed=0;lastFlush=millis();
      xSemaphoreGive(diskMutex);
    }
    bool draining=unflushed>0 || uxQueueMessagesWaiting(storageQueue)>0 || (lastStop && millis()-lastStop<300);
    portENTER_CRITICAL(&stateMux);
    storageDraining=draining;
    portEXIT_CRITICAL(&stateMux);
    if(millis()-lastSpace>=10000) {
      xSemaphoreTake(diskMutex,portMAX_DELAY);refreshStorageSpace();xSemaphoreGive(diskMutex);
      lastSpace=millis();
    }
    vTaskDelay(1);
  }
}

// Object deltas refer to the first full frame. Arrays are replaced as complete values.
static cJSON *collectionDelta(cJSON *base,cJSON *current) {
  cJSON *delta=cJSON_CreateObject();
  for(cJSON *item=current->child;item;item=item->next) {
    cJSON *old=cJSON_GetObjectItemCaseSensitive(base,item->string);
    if(old && cJSON_Compare(old,item,true))continue;
    cJSON *value=(old && cJSON_IsObject(old) && cJSON_IsObject(item))?
      collectionDelta(old,item):cJSON_Duplicate(item,true);
    cJSON_AddItemToObject(delta,item->string,value);
  }
  return delta;
}

static bool readJournalBatch() {
  if(!cardReady || replayDone || xSemaphoreTake(diskMutex,pdMS_TO_TICKS(10))!=pdTRUE) return false;
  if(!replayPath.length()) {
    File dir=SD_MMC.open("/collection_v2"); File entry=dir.openNextFile();
    while(entry && (entry.isDirectory() || !String(entry.path()).endsWith(".psj"))) {entry.close();entry=dir.openNextFile();}
    if(entry) replayPath=String(entry.path());
    entry.close();dir.close();replayOffset=0;
  }
  File file=SD_MMC.open(replayPath,FILE_READ);
  uint32_t limit=replayPath==journalPath?journalDurable:(file?file.size():0);
  if(replayOffset==0 && file) {
    File ack=SD_MMC.open(replayPath+".ack",FILE_READ);uint32_t saved[3];
    if(ack && ack.read((uint8_t *)saved,12)==12 && saved[0]==0x41434b32 && saved[1]<=limit && saved[2]==recordCrc((uint8_t *)saved,8)) replayOffset=saved[1];
    ack.close();checkpointOffset=replayOffset;
  }
  String frames, crcs; uint32_t position=replayOffset; uint8_t count=0;
  cJSON *base=nullptr; bool badTail=false;
  if(file) {
    file.seek(position);
    while(count<10 && position+12<=limit) {
      uint32_t header[3];
      if(file.read((uint8_t *)header,12)!=12 || header[0]!=0x32534e50 || header[1]>=RECORD_MAX || header[1]==0 || position+12+header[1]>limit) {badTail=true;break;}
      // A76XX CMQTTPAYLOAD is limited to 10240 bytes, including the JSON envelope.
      char data[RECORD_MAX];
      if(file.read((uint8_t *)data,header[1])!=header[1] || recordCrc((uint8_t *)data,header[1])!=header[2]) {badTail=true;break;}
      data[header[1]]=0;
      cJSON *current=cJSON_Parse(data);
      if(!current) {badTail=true;break;}
      String encoded;
      if(!count) {base=cJSON_Duplicate(current,true);encoded=data;}
      else {
        cJSON *patch=collectionDelta(base,current);
        char *text=cJSON_PrintUnformatted(patch);encoded=text?text:"";
        free(text);cJSON_Delete(patch);
      }
      cJSON_Delete(current);
      if(!encoded.length() || (count && frames.length()+encoded.length()>8500))break;
      if(count) {frames+=',';crcs+=',';}
      frames+=encoded;crcs+=String(header[2]);count++;position+=12+header[1];
    }
    file.close();
    if(!count && position<limit && position+12>limit)badTail=true;
  }
  cJSON_Delete(base);
  if(!count && badTail) {
    diskErrors++;
    if(replayPath==journalPath) {
      diskFault=true;replayDone=true;
      portENTER_CRITICAL(&stateMux);captureActive=false;portEXIT_CRITICAL(&stateMux);
    }
  }
  // Preserve a torn historical tail for audit; it must not block other valid files.
  if(!count && (replayOffset>=limit || badTail) && replayPath!=journalPath) {
    File dir=SD_MMC.open("/collection_v2");bool found=false, advanced=false;
    for(File entry=dir.openNextFile();entry;entry=dir.openNextFile()) {
      String path=entry.path();entry.close();
      if(!path.endsWith(".psj"))continue;
      if(found) {replayPath=path;replayOffset=0;advanced=true;break;}
      if(path==replayPath)found=true;
    }
    dir.close();
    if(!advanced) {
      if(journalPath.length()) {replayPath=journalPath;replayOffset=0;}
      else replayDone=true;
    }
  }
  xSemaphoreGive(diskMutex);
  if(!count) return false;
  pendingId=String(bootId)+"-"+String(millis())+"-"+String(replayOffset);
  pendingEnd=position;
  pendingBatch="{\"device\":\""+String(MQTT_CLIENT_ID)+"\",\"batch\":\""+pendingId+"\",\"delta\":true,\"crcs\":["+crcs+"],\"frames\":["+frames+"]}";
  return true;
}

void setup() {
  DebugSerial.begin(UWB_BAUD,SERIAL_8N1,UWB_RX_PIN,DEBUG_TX_PIN);delay(1500);
  if(xTaskCreatePinnedToCore(uwbWorker,"uwb-rx",3072,nullptr,2,nullptr,0)!=pdPASS) {
    DebugSerial.println("FATAL: UWB task creation failed");while(true)delay(1000);
  }
  if(!edgeSelfTest()) {while(true)delay(1000);}
  snprintf(bootId,sizeof(bootId),"%08lx%08lx",(unsigned long)esp_random(),(unsigned long)esp_random());
  storageQueue=xQueueCreate(24,sizeof(CaptureRecord));commandQueue=xQueueCreate(8,sizeof(CaptureCommand));
  diskMutex=xSemaphoreCreateMutex();liveMutex=xSemaphoreCreateMutex();
  if(!storageQueue || !commandQueue || !diskMutex || !liveMutex) {DebugSerial.println("FATAL: queue allocation failed");while(true)delay(1000);}
  initAudioFeatureExtractor();
  if(!edgeFeatureSelfTest()) {while(true)delay(1000);}
  deviceMicOK=initMicrophone();imuFound=initIMU();initHealthModule();deviceBatteryOK=initBatteryGauge();
  Wire.setTimeOut(10);BatteryWire.setTimeOut(10);
  if(xTaskCreatePinnedToCore(samplingWorker,"sample-10Hz",12000,nullptr,3,&collectorTask,1)!=pdPASS ||
     xTaskCreatePinnedToCore(storageWorker,"sd-journal",10000,nullptr,1,nullptr,0)!=pdPASS) {
    DebugSerial.println("FATAL: task creation failed");while(true)delay(1000);
  }
  DebugSerial.println("EDGE DOG MLP + UWB READY; BU03 TX1 -> GPIO42 at 115200; 1Hz summaries; no automatic SD recording");
  modemReady=initCellularNetwork();if(modemReady)mqttConnect();
}

void loop() {
  processModemAsyncInput();
  if(networkPauseUntil && (int32_t)(millis()-networkPauseUntil)<0) {delay(10);return;}
  networkPauseUntil=0;
  if(serviceGnss()) {delay(5);return;}
  // Reconnect before taking latestLive: reconnects may take tens of seconds.
  if(!mqttConnected && !mqttConnect()) {delay(5);return;}
  if(!clockSentUs || millis()-lastClockSent>=30000) {
    clockSentUs=esp_timer_get_time();clockNonce++;lastClockSent=millis();
    String request="{\"device\":\""+String(DEVICE_ID)+"\",\"boot\":\""+String(bootId)+"\",\"nonce\":"+decimal64(clockNonce)+",\"t0_us\":"+decimal64(clockSentUs)+"}";
    mqttPublishText("petsense/sensor/clock_v2",request);
  }
  if(millis()-lastPublish>=1000) {
    lastPublish += 1000;
    if(millis()-lastPublish>=1000)lastPublish=millis();
    // Edge demo sends summaries only; existing SD journals remain untouched.
    String live;
    if(xSemaphoreTake(liveMutex,pdMS_TO_TICKS(5))==pdTRUE) {live=latestLive;xSemaphoreGive(liveMutex);}
    if(live.length()) {
      mqttPublishText("petsense/sensor/edge_v1",live);
      String status="{\"device\":\""+String(DEVICE_ID)+"\",\"frame\":"+live+"}";
      mqttPublishText("petsense/sensor/collection_status",status);
    }
  }
  delay(5);
}
