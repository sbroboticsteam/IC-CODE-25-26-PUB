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

OPERATOR_IP = "192.168.50.200" # your laptop/pc ip address on IC2026 Network
OPERATOR_PORT = 5600 # the port for video streaming 
TEAM_ID = -1 # Your team ID

PI_IP = "192.168.50.146" # Your pi IP
PI_PORT = 5005  # 

MIN_DUTY_FLOOR = 30
PURE_DC_THRESHOLD = 80

### Bind Socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((PI_IP, PI_PORT))


### Input Receiving Loop
inputQ = []
def get_input():
    while True:
        try:
            data, addr = sock.recvfrom(1024)  # buffer size = 1024 bytes
            msg = json.loads(data.decode('utf-8'))
            inputQ.append(msg)
            print(f"[Received from {addr}] {msg}")
        except Exception as e:
            print("[Receiver Error]", e)

class Robot(RobotBase):
    def __init__(self, team_id):
        super().__init__(team_id)
        ### Initialization/Start Up
        self.stream_proc = None

        ### Socket Receive Thread
        self.input_thread = threading.Thread(target=get_input, daemon=True)
        self.input_thread.start()

    def run(self):
        try:
            while True:
                self.tank_drive()
        except KeyboardInterrupt:
            sys.stderr.write("\n[Shutdown] Keyboard interrupt\n")
        except Exception as e:
            sys.stderr.write(f"[Runtime Error] {e}\n")
        finally:
            self.cleanup()

    def stream(self):
        cmd = (
            f"rpicam-vid -t 0 --width 1280 --height 720 --framerate 30 "
            f"--codec h264 --bitrate 4000000 --profile baseline --intra 30 --inline "
            f"--nopreview -o - | "
            f"gst-launch-1.0 -v fdsrc ! h264parse ! "
            f"rtph264pay config-interval=1 pt=96 ! "
            f"udpsink host={OPERATOR_IP} port={OPERATOR_PORT} sync=false async=false"
        )
    
        self.stream_proc = subprocess.Popen(cmd, shell=True, preexec_fn=os.setsid, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[Video] Stream started -> {OPERATOR_IP}:{OPERATOR_PORT}")


    def tank_drive(self):
        if len(inputQ) > 0:
            inputJSON = inputQ.pop(0)
            self.set_motor("FL", inputJSON["Left"])
            self.set_motor("BL", inputJSON["Left"])
            self.set_motor("FR", inputJSON["Right"])
            self.set_motor("BR", inputJSON["Right"])

    def mecanum_drive(self):
        if len(inputQ) > 0:
            inputJSON = inputQ.pop(0)
            vx = inputJSON["vx"]
            vy = inputJSON["vy"]
            rot = inputJSON["rot"]

            fl = vy + vx + rot
            fr = -vy + vx - rot
            bl = -vy + vx + rot
            br = vy + vx - rot
            
            scale = max(1.0, abs(fl), abs(fr), abs(bl), abs(br))
            fl /= scale; fr /= scale; rl /= scale; rr /= scale # normalize each speed

            self.set_motor("FL", fl)
            self.set_motor("BL", bl)
            self.set_motor("FR", fr)
            self.set_motor("BR", br)

    # Set PWM Value to Motor
    def set_motor(self, motor, value):  
        """
        Set the pwm input of a motor, given its key: FL, FR, BL, BR
        """
        value = max(min(value,-1), 1)
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
        if self.stream_proc and self.stream_proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.stream_proc.pid), signal.SIGTERM)
                self.stream_proc.wait(timeout=2)
            except:
                pass
            print("[Video] Stream stopped")
        self.stream_proc = None

        for receiver in self.ir_receivers:
            receiver.cleanup()

if __name__ == "__main__":
    robot = Robot(TEAM_ID)
    robot.stream()
    try:
        # asyncio.run(robot.run())
        robot.run()
    except KeyboardInterrupt:
        print("\n[Shutdown] Received interrupt")
    except Exception as e:
        print(f"[Error] {e}")
    finally:
        robot.cleanup()