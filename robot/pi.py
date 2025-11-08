import asyncio
import json
import math
import os
import signal
import subprocess
import sys
import time
import threading
import pigpio
import socket

from readonly import RobotBase, MOTORS
# Motor information
motor_map = {
    "FR":"MOTOR 1",
    "FL":"MOTOR 2",
    "BR":"MOTOR 3",
    "BL":"MOTOR 4"
}

MIN_DUTY_FLOOR = 30
PURE_DC_THRESHOLD = 80
PWM_FREQ_HZ = 10000

class Robot(RobotBase):
    def __init__(self, config):
        super().__init__(config)
        ### Initialization/Start Up
        self.init_motors()

        ### Bind Socket
        self.input_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.input_sock.bind((self.robot_ip, self.robot_port))
        self.inputQ = []

        ### Socket Receive Thread
        self.input_thread = threading.Thread(target=self.get_input, daemon=True)
        self.input_thread.start()
    
    ### Input Receiving Loop
    def get_input(self):
        while True:
            try:
                data, addr = self.input_sock.recvfrom(1024)  # buffer size = 1024 bytes
                msg = json.loads(data.decode('utf-8'))
                self.inputQ.append(msg)
            except Exception as e:
                print("[Receiver Error]", e)

    def run(self):
        try:
            while True:
                ### Interpret Inputs
                if len(self.inputQ) > 0:
                    inputJSON = self.inputQ.pop(0)

                    # self.tank_drive(inputJSON)
                    self.mecanum_drive(inputJSON)

                    if (inputJSON["Firing"]):
                        self.fire_ir()
        except KeyboardInterrupt:
            sys.stderr.write("\n[Shutdown] Keyboard interrupt\n")
        except Exception as e:
            sys.stderr.write(f"[Runtime Error] {e}\n")
        finally:
            self.cleanup()

    def init_motors(self):
        for m in MOTORS.values():
            self.pi.set_mode(m["EN"], pigpio.OUTPUT)
            self.pi.set_PWM_frequency(m["EN"], PWM_FREQ_HZ)
            self.pi.write(m["EN"], 0)
            
            self.pi.set_mode(m["IN1"], pigpio.OUTPUT)
            self.pi.write(m["IN1"], 0)
            self.pi.set_mode(m["IN2"], pigpio.OUTPUT)
            self.pi.write(m["IN2"], 0)

    def tank_drive(self, inputJSON):
        # invert left side
        self.set_motor(motor_map["FL"], inputJSON["Left"])
        self.set_motor(motor_map["BL"], inputJSON["Left"])
        self.set_motor(motor_map["FR"], -inputJSON["Right"])
        self.set_motor(motor_map["BR"], -inputJSON["Right"])


    def mecanum_drive(self, inputJSON):
        vx = inputJSON["vx"]
        vy = inputJSON["vy"]
        rot = inputJSON["rot"]

        fl = vy + vx + rot
        fr = vy - vx - rot
        bl = vy - vx + rot
        br = vy + vx - rot
        
        scale = max(1.0, abs(fl), abs(fr), abs(bl), abs(br))
        fl /= scale; fr /= scale; bl /= scale; br /= scale # normalize each speed

        # invert a side
        self.set_motor(motor_map["FL"], fl)
        self.set_motor(motor_map["BL"], bl)
        self.set_motor(motor_map["FR"], -fr)
        self.set_motor(motor_map["BR"], -br)

    # Set PWM Value to Motor
    def set_motor(self, motor, value):  
        """
        Set the pwm input of a motor, given its key: "MOTOR 1", "MOTOR 2", "MOTOR 3","MOTOR 4"
        """
        value = max(-1.0, min(1.0, value))
        pins = MOTORS[motor]
        
        if abs(value) < 1e-3:
            self.pi.set_PWM_dutycycle(pins["EN"], 0)
            self.pi.write(pins["IN1"], 0)
            self.pi.write(pins["IN2"], 0)
            return
        
        forward = value > 0
        self.pi.write(pins["IN1"], 1 if forward else 0)
        self.pi.write(pins["IN2"], 0 if forward else 1)
        
        pct = int(abs(value) * 100)
        if pct >= PURE_DC_THRESHOLD:
            self.pi.write(pins["EN"], 1)
        else:
            pct = max(MIN_DUTY_FLOOR, pct)
            duty = pct * 255 // 100
            self.pi.set_PWM_dutycycle(pins["EN"], duty)

    def cleanup(self):
        # end camera stream
        self.cleanup_stream()

        #ir clean up
        for receiver in self.ir_receivers:
            receiver.cleanup()

        self.input_sock.close()

if __name__ == "__main__":
    config = None
    try:
        with open("../config.json") as file:
            config = json.load(file)
    except FileNotFoundError:
        print(f"Config File not found in parent directory!")
    except json.JSONDecodeError:
        print(f"Failed to decode config file!")

    robot = Robot(config)
    robot.stream()

    try:
        robot.run()
    except KeyboardInterrupt:
        print("\n[Shutdown] Received interrupt")
    except Exception as e:
        print(f"[Error] {e}")
    finally:
        robot.cleanup()