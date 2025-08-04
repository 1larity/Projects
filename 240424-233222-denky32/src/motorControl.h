#define MIN_PULSE_WIDTH       600
#define MAX_PULSE_WIDTH       2200
#define FREQUENCY             50
// Include Adafruit PWM Library
#include <Adafruit_PWMServoDriver.h>
Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver();


void motorInit(){
    pwm.begin();
  pwm.setPWMFreq(FREQUENCY);

}

void moveMotorDegrees (int controlIn, int motorOut)
{
  int pulse_wide, pulse_width;
   
  // Convert to pulse width
  pulse_wide = map(controlIn, 0, 180, MIN_PULSE_WIDTH, MAX_PULSE_WIDTH);
  pulse_width = int(float(pulse_wide) / 1000000 * FREQUENCY * 4096);
  
  //Control Motor
  pwm.setPWM(motorOut, 0, pulse_width);
}
