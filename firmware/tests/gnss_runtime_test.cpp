// Exercise the firmware serializer and AT ownership gate with a simulated UART.
#include <cassert>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <string>
#include <type_traits>
#include <stdint.h>
class String : public std::string {
 public:
  using std::string::string;
  using std::string::operator+=;
  String(const std::string &value):std::string(value) {}
  template<class T, std::enable_if_t<std::is_integral<T>::value, int> = 0>
  String(T value):std::string(std::to_string(value)) {}
  String(double value, int decimals) {
    std::ostringstream out;out<<std::fixed<<std::setprecision(decimals)<<value;assign(out.str());
  }
  void remove(size_t offset) {erase(offset);}
  bool endsWith(const char *tail) const {return !empty() && back()==*tail;}
};
static uint32_t clockMs=0;
static uint32_t millis() {return clockMs;}
using portMUX_TYPE=int;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
static bool modemReady=true;
static std::string sent;
void modemSendLine(const String &command) {sent=command;}
#include "gnss_runtime.h"
static void receive(const char *reply) {while(*reply)feedGnss(*reply++);}
int main(int argc, char **argv) {
  assert(argc==2);
  assert(serviceGnss() && sent=="AT+CGNSSTST=0");
  clockMs=1;receive("OK\r\n");
  clockMs=1001;assert(serviceGnss() && sent=="AT+CGNSSPWR=1");
  clockMs=1002;receive("+CGNSSPWR: READY!\r\n");
  clockMs=6002;assert(serviceGnss() && sent=="AT+CGPSINFO");
  clockMs=6100;
  if(std::string(argv[1])!="empty")receive("+CGPSINFO:3113.343286,N,12121.234064,E,250311,072809.33,44.1,10,0\r\nOK\r\n");
  else receive("+CGPSINFO:,,,,,,,,\r\nOK\r\n");
  updateGnssSnapshot();
  if(std::string(argv[1])=="stale")clockMs+=15001;
  String json("{\"health\":{\"valid\":true}}");
  appendGnssSnapshot(json,snapshotGnss());
  std::cout<<json<<'\n';
}
