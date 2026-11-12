#include "gnss_protocol.h"
#include <assert.h>
#include <math.h>
#include <string.h>
#include <string>

static const char *sample="+CGPSINFO:3113.343286,N,12121.234064,E,250311,072809.33,44.1,10.0,0.0";
static void feed(GnssController &c, const std::string &s, uint32_t now) {
  for(char ch:s)c.feed(ch,now);
}
static void start(GnssController &c, uint32_t base) {
  c.reset(base);
  assert(!strcmp(c.poll(base),"AT+CGNSSTST=0"));
  feed(c,"\r\nOK\r\n",base+1);
  assert(c.busy());
  assert(!strcmp(c.poll(base+1001),"AT+CGNSSPWR=1"));
  feed(c,"\r\nOK\r\n+CGNSSPWR: READY!\r\n",base+1002);
  assert(!strcmp(c.poll(base+6002),"AT+CGPSINFO"));
}
int main() {
  GnssFix f;
  assert(parseGnssInfo(sample,f));
  assert(fabs(f.latitude-31.2223881)<1e-7);
  assert(fabs(f.longitude-121.3539010667)<1e-7);
  assert(fabs(f.speedMps-5.14444444444)<1e-8);
  assert(!strcmp(f.utc,"2011-03-25T07:28:09.330Z"));
  assert(parseGnssInfo("+CGPSINFO:0000.0,S,00000.0,W,290224,000000,,,",f));
  assert(f.latitude==0 && f.longitude==0 && !f.hasSpeed);
  assert(parseGnssInfo("+CGPSINFO:1230,S,17930,W,010126,235959.999,-5,0,360",f));
  assert(f.latitude==-12.5 && f.longitude==-179.5);
  assert(parseGnssInfo("+CGPSINFO:1230,N,00130,E,010126,000000.123456,0,0,0",f));
  assert(!strcmp(f.utc,"2026-01-01T00:00:00.123Z"));
  for(const char *bad:{"+CGPSINFO:,,,,,,,,", "+CGPSINFO:3160,N,12100,E,010126,000000,0,0,0",
      "+CGPSINFO:9000.1,N,12100,E,010126,000000,0,0,0",
      "+CGPSINFO:3100,X,12100,E,010126,000000,0,0,0",
      "+CGPSINFO:3100,N,18100,E,010126,000000,0,0,0",
      "+CGPSINFO:3100,N,12100,E,290223,000000,0,0,0",
      "+CGPSINFO:3100,N,12100,E,010126,240000,0,0,0",
      "+CGPSINFO:3100,N,12100,E,010126,000000,nan,0,0",
      "+CGPSINFO:3100,N,12100,E,010126,000000,9999999999999999999999999999999999999999,0,0",
      "+CGPSINFO:3100,N,12100,E,010126,000000,0,-1,0",
      "+CGPSINFO:3100,N,12100,E,010126,000000,0,0,0,extra"})
    assert(!parseGnssInfo(bad,f));
  for(uint32_t base:{0u,0xfffff000u}) {
    GnssController c;start(c,base);
    feed(c,std::string(sample)+"\r\n",base+6100);
    assert(!c.latest().valid); // Do not commit incomplete transactions.
    feed(c,"OK\r\n",base+6101);
    assert(c.latest().valid && c.latest().sequence==1);
    assert(!strcmp(c.status(base+21101),"fixed"));
    assert(!strcmp(c.status(base+21102),"stale"));
    assert(!strcmp(c.poll(base+25000),"AT+CGPSINFO"));
    feed(c,std::string(sample)+"\r\nOK\r\n",base+25001);
    assert(c.latest().sequence==1 && c.latest().receivedMs==base+6101);
    assert(!strcmp(c.status(base+25001),"stale")); // Cached UTC must not refresh age.
    assert(!strcmp(c.poll(base+30001),"AT+CGPSINFO"));
    feed(c,"+CGPSINFO:,,,,,,,,\r\nOK\r\n",base+30002);
    assert(!c.latest().valid && !strcmp(c.status(base+30002),"searching"));
    assert(!strcmp(c.poll(base+35002),"AT+CGPSINFO"));
    assert(!c.poll(base+45002)); // Timeout releases channel after cooling.
    assert(c.busy());
    assert(!c.poll(base+46002) && !c.busy());
    assert(!strcmp(c.status(base+46002),"error"));
  }
  GnssController c;start(c,0);
  feed(c,std::string(500,'X')+"\r\n"+sample+"\r\nOK\r\n",6200);
  assert(c.latest().valid);
  c.reset(0);
  c.poll(0);feed(c,"OK\r\n",1);
  c.poll(1001);feed(c,"+CGNSSPWR: READY!\r\n",1002);
  assert(!strcmp(c.poll(6002),"AT+CGPSINFO")); // Board may emit READY without OK.
}
