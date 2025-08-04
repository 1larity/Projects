
/*
  PCA9685 PWM Servo Driver Example
  pca9685-servomotor-demo.ino
  Demonstrates use of 16 channel I2C PWM driver board with 4 servo motors
  Uses Adafruit PWM library
  Uses 4 potentiometers for input

  DroneBot Workshop 2018
  https://dronebotworkshop.com
*/

// Include Wire Library for I2C Communications
#include <Wire.h>
// Inlude motor control functions
#include "motorControl.h"


//define servo pins
const int servo_pin[4][3] = { {0, 1, 2}, {4, 3, 5}, {7, 6, 8}, {10, 9, 11} };

void setup() 
{
  Serial.begin(9600);
  motorInit();
}


void loop() {
  for (int i = 0; i < 4; i++)
  {
    for (int j = 0; j < 3; j++)
    {
      moveMotorDegrees(90, servo_pin [i][j]);
      Serial.print("setmotor ");
      Serial.print(servo_pin [i][j]);
       Serial.println(".");
   //   servo[i][j].write(90);
      delay(20);
    }
  }

}
