#include "gnss_protocol.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool number(const char *s, double &value) {
  if (!*s) return false;
  // strtod accepts NaN, infinity, exponent and hex forms; AT fields do not.
  for (const char *p=s; *p; ++p)
    if ((*p<'0' || *p>'9') && *p!='.' && !(p==s && *p=='-')) return false;
  char *end; value=strtod(s,&end);
  return end!=s && !*end && isfinite(value);
}
static bool coordinate(const char *s, const char *hem, bool latitude, double &out) {
  double raw;
  if (!number(s,raw) || raw<0 || raw>18000 || strlen(hem)!=1) return false;
  const int degrees=(int)(raw/100);
  const double minutes=raw-degrees*100;
  const int limit=latitude?90:180;
  if (degrees>limit || minutes>=60 || (degrees==limit && minutes!=0)) return false;
  if (latitude ? (*hem!='N' && *hem!='S') : (*hem!='E' && *hem!='W')) return false;
  out=(degrees+minutes/60)*((*hem=='S' || *hem=='W')?-1:1);
  return true;
}
static bool digits(const char *s, size_t count) {
  for (size_t i=0;i<count;i++) if(s[i]<'0'||s[i]>'9') return false;
  return true;
}
bool parseGnssInfo(const char *line, GnssFix &fix) {
  fix=GnssFix();
  const char *prefix="+CGPSINFO:";
  if (strncmp(line,prefix,strlen(prefix))) return false;
  line+=strlen(prefix); while(*line==' ')line++;
  if(strlen(line)>=224) return false;
  char copy[224]; strcpy(copy,line);
  char *fields[9]; fields[0]=copy; size_t count=1;
  for(char *p=copy;*p;p++) if(*p==',') {
    if(count==9)return false;
    *p=0; fields[count++]=p+1;
  }
  if(count!=9) return false;
  if(!coordinate(fields[0],fields[1],true,fix.latitude) ||
     !coordinate(fields[2],fields[3],false,fix.longitude)) return false;
  const char *date=fields[4], *time=fields[5];
  const size_t length=strlen(time);
  if(strlen(date)!=6 || !digits(date,6) || length<6 || !digits(time,6))return false;
  if(length>6 && (time[6]!='.' || length==7 || length>16 || !digits(time+7,length-7)))return false;
  int day=(date[0]-'0')*10+date[1]-'0', month=(date[2]-'0')*10+date[3]-'0';
  int year=2000+(date[4]-'0')*10+date[5]-'0';
  int hour=(time[0]-'0')*10+time[1]-'0', minute=(time[2]-'0')*10+time[3]-'0';
  int second=(time[4]-'0')*10+time[5]-'0';
  const int days[]={31,28,31,30,31,30,31,31,30,31,30,31};
  if(month<1||month>12||day<1||day>days[month-1]+(month==2&&year%4==0)||hour>23||minute>59||second>59)return false;
  strcpy(fix.utc,"2000-00-00T00:00:00.000Z");
  fix.utc[2]=date[4];fix.utc[3]=date[5];
  memcpy(fix.utc+5,date+2,2);memcpy(fix.utc+8,date,2);
  memcpy(fix.utc+11,time,2);memcpy(fix.utc+14,time+2,2);memcpy(fix.utc+17,time+4,2);
  for(size_t i=7;i<length && i<10;i++)fix.utc[20+i-7]=time[i];
  fix.hasAltitude=number(fields[6],fix.altitudeM);
  fix.hasSpeed=number(fields[7],fix.speedMps);
  fix.hasCourse=number(fields[8],fix.courseDeg);
  if((*fields[6]&&(!fix.hasAltitude||fix.altitudeM < -20000||fix.altitudeM>100000))||
     (*fields[7]&&(!fix.hasSpeed||fix.speedMps<0||fix.speedMps>2000))||
     (*fields[8]&&(!fix.hasCourse||fix.courseDeg<0||fix.courseDeg>360)))return false;
  fix.speedMps*=0.514444444444;
  return fix.valid=true;
}
void GnssController::reset(uint32_t now) {
  *this=GnssController(); due=now;
}
const char *GnssController::status(uint32_t now) const {
  if(failed)return "error";
  if(stage<2)return "starting";
  if(!fix.valid)return "searching";
  return (uint32_t)(now-fix.receivedMs)<=15000 ? "fixed":"stale";
}
const char *GnssController::poll(uint32_t now) {
  if(cooling && (uint32_t)(now-started)>=1000)cooling=false;
  if(waiting) {
    if((uint32_t)(now-started)>=10000)finish(false,now);
    return nullptr;
  }
  if((int32_t)(now-due)<0)return nullptr;
  cooling=false;
  waiting=true; started=now; gotInfo=false; used=0; overflow=false;
  return stage==0?"AT+CGNSSTST=0":stage==1?"AT+CGNSSPWR=1":"AT+CGPSINFO";
}
void GnssController::finish(bool ok, uint32_t now) {
  waiting=false; cooling=true;
  if(!ok) {
    fix.valid=false; failed=true; stage=0; due=now+60000;
    // Only reserve UART for one second to drain a late response, then let MQTT run.
    started=now;
  } else {
    failed=false;
    if(stage<2){stage++;due=now+(stage==2?5000:1000);}
    else {
      if(gotInfo && candidate.valid) {
        if(strcmp(candidate.utc,fix.utc)>0) {
          candidate.sequence=fix.sequence+1; candidate.receivedMs=now; fix=candidate;
        }
      } else fix.valid=false;
      due=now+5000;
    }
    started=now;
  }
}
void GnssController::consume(uint32_t now) {
  if(!waiting)return;
  if(stage==2 && !strncmp(line,"+CGPSINFO:",10)) {
    gotInfo=true; parseGnssInfo(line,candidate);
  } else if(stage==1 && !strcmp(line,"+CGNSSPWR: READY!")) {
    finish(true,now);
  } else if(!strcmp(line,"OK"))finish(stage!=2||gotInfo,now);
  else if(!strcmp(line,"ERROR")||!strncmp(line,"+CME ERROR:",11))finish(false,now);
}
void GnssController::feed(char ch, uint32_t now) {
  // A quiet interval after every transaction separates delayed terminal lines.
  if(cooling && (uint32_t)(now-started)>=1000)cooling=false;
  if(ch=='\r')return;
  if(ch=='\n') {
    if(!overflow){line[used]=0;consume(now);} used=0;overflow=false;
  } else if(used+1<sizeof(line) && !overflow)line[used++]=ch;
  else overflow=true;
}
