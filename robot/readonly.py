import time
import sys
import signal
import os
import threading
from datetime import datetime
import socket
import json 
import subprocess
import threading
import pigpio

IR_TX_GPIO = 20  # IR transmitter pin
IR_RX_GPIOS = [3, 25, 21]  # IR receiver pins

CARRIER_FREQ = 38000
CARRIER_PERIOD_US = int(1_000_000 / CARRIER_FREQ)
PULSE_ON_US = CARRIER_PERIOD_US // 2
PULSE_OFF_US = CARRIER_PERIOD_US - PULSE_ON_US

BIT_0_BURST = 800
BIT_1_BURST = 1600
START_END_BURST = 2400
TOLERANCE = 200

FIRE_COOLDOWN = 2

COMMAND_TIMEOUT_S = 0.8
POWER_SAVE_TIMEOUT_S = 10.0
HIT_DISABLE_TIME = 10.0  # Seconds robot is disabled when hit

# Motor configurationws
MOTORS = {
    "MOTOR 1": {"EN": 18, "IN1": 4, "IN2": 17},
    "MOTOR 2": {"EN": 23, "IN1": 15, "IN2": 27},
    "MOTOR 3": {"EN": 8, "IN1": 16, "IN2": 7},
    "MOTOR 4": {"EN": 13, "IN1": 12, "IN2": 6},
}

PWM_FREQ_HZ = 10000
STBY_PINS = [14, 5]

# Game Viewer
# GV_IP = '192.168.50.67'
# GV_PORT = 6000 # GameViewer uses port 6000 for robot 

# ========== IR RECEPTION ==================================

class IRReceiver():
    def __init__(self, gpio_pin, robot):
        self.gpio = gpio_pin
        self.bursts = []
        self.last_tick = 0
        self.last_burst_time = 0
        self.robot = robot
        self.pi = robot.pi

        self.pi.set_mode(self.gpio, pigpio.INPUT)
        self.pi.set_pull_up_down(self.gpio, pigpio.PUD_UP)
        
        self.cb = self.pi.callback(self.gpio, pigpio.EITHER_EDGE, self.edge_callback)
        print(f"[IR] Monitoring receiver on GPIO {self.gpio}")
    
    def edge_callback(self, gpio, level, tick):
        current_time = time.time()
        
        if level == 0:  # Start of IR burst
            self.last_tick = tick
        elif level == 1 and self.last_tick:  # End of IR burst
            burst_width = pigpio.tickDiff(self.last_tick, tick)
            
            # New transmission if gap > 100ms
            if current_time - self.last_burst_time > 0.1:
                if len(self.bursts) > 0:
                    self.process_bursts()
                self.bursts = []
            
            self.bursts.append(burst_width)
            self.last_burst_time = current_time
            
            # Process when we have complete transmission
            if len(self.bursts) == 10:
                self.process_bursts()
                self.bursts = []
    
    def process_bursts(self):
        """Process received IR bursts to decode team ID"""
        if len(self.bursts) != 10:
            return
        
        # Check start and end bursts
        if (abs(self.bursts[0] - START_END_BURST) > TOLERANCE or 
            abs(self.bursts[9] - START_END_BURST) > TOLERANCE):
            return
        
        # Decode middle 8 bits
        team_id = 0
        for i in range(1, 9):
            burst = self.bursts[i]
            bit_pos = 7 - (i - 1)
            
            if abs(burst - BIT_1_BURST) <= TOLERANCE:
                team_id |= (1 << bit_pos)
            elif abs(burst - BIT_0_BURST) <= TOLERANCE:
                pass  # bit is 0
            else:
                return  # Invalid burst
        
        # Valid hit received
        self.robot.on_laser_hit(team_id)
    
    def cleanup(self):
        self.cb.cancel()

# ========== Robot/IR Control Threads ==========================

def begin_hitstun_timer(robot): # Updates the time remaining for hitstun
    print("[Game] Waiting to respawn")
    while robot.ir_state["time_remaining"] > 0:
        print(f"    {robot.ir_state["time_remaining"]}s...")
        robot.ir_state["time_remaining"] -= 1
        time.sleep(1)
    print("[Game] Respawned!")
    robot.exit_standby()
    robot.ir_state.update({"is_hit" : False})

# ========== ROBOT BASE CLASS - Inherit From Here! ==========

class RobotBase():
    def __init__(self, config):
        self.pi = pigpio.pi()
        self.config = config
        self.team_id = config["team_id"]
        self.team_name = config["team_name"]
        self.robot_name = config.get("robot_name", self.team_name)

        self.robot_ip = config["robot_ip"]
        self.robot_port = config["robot_port"]

        self.operator_ip = config["operator_ip"]
        self.operator_input_port = config["operator_input_port"]
        self.operator_video_port = config["operator_video_port"]

        self.game_start_time = time.time()

        if not self.pi.connected:
            print("ERROR: pigpiod not running. Run: sudo pigpiod", file=sys.stderr)
            sys.exit(1)

        self.stream_proc = None

        # initialization
        self.init_standby()
        self.init_ir()

        # IR state
        self.last_fire_time = 0
        self.ir_state = {
            "is_hit": False,
            "hit_by_team": 0,
            "hit_time": 0,
            "time_remaining": 0,
            "is_self_hit": False,  # Added for self-hit detection
        }
        
        self.init_gv()

        # GV Listener Thread
        self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.listener_thread.start()

        # Heartbeat Thread
        print("[Heartbeat] Heartbeat thread started")
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

    # ------- Initialization ------- 

    def init_standby(self):
        for s in STBY_PINS:
            self.pi.set_mode(s, pigpio.OUTPUT)
            self.pi.write(s, 1)

    def init_ir(self):
        #init ir transmitter
        self.pi.set_mode(IR_TX_GPIO, pigpio.OUTPUT)
        self.pi.write(IR_TX_GPIO, 0)

        #init ir receivers
        self.ir_receivers = []
        for gpio in IR_RX_GPIOS:
            self.ir_receivers.append(IRReceiver(gpio,self))

    def init_gv(self):
        self.listen_port = 6000 + self.team_id
        self.gv_comm_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.gv_comm_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.gv_comm_sock.settimeout(0.1)  # Non-blocking with timeout

        try:
            self.gv_comm_sock.bind(('0.0.0.0', self.listen_port))
        except Exception as e:
            print(f"[GameClient] Failed to bind to port {self.listen_port}: {e}")
            return False
        
        # # --- Discovery responder: listen for DISCOVER messages on UDP 5500 ---
        try:
            self._discovery_port = 6500
            self._discovery_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._discovery_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # bind to all interfaces so controllers can discover via IP
            self._discovery_sock.bind(('0.0.0.0', self._discovery_port))
            self._discovery_loop()
            # self._discovery_thread = threading.Thread(target=self._discovery_loop, daemon=True)
            # self._discovery_thread.start()
            print(f"[Discovery] Listening for discovery packets on port {self._discovery_port}")
        except Exception as e:
            print(f"[Discovery] Failed to start discovery responder: {e}")
            self._discovery_sock = None

    def _discovery_loop(self):
        """Simple UDP discovery responder. Replies with a JSON DISCOVER_ACK containing robot info."""
        if not hasattr(self, '_discovery_sock') or not self._discovery_sock:
            return

        sock = self._discovery_sock
        while True:
            try:
                data, addr = sock.recvfrom(4096)
                try:
                    msg = json.loads(data.decode('utf-8'))
                except Exception:
                    # ignore invalid payloads
                    continue

                if msg.get('type') == 'DISCOVER':
                    resp = {
                        'type': 'DISCOVER_ACK',
                        'team_id': self.team_id,
                        'team_name': self.team_name,
                        'robot_name': self.robot_name,
                        'robot_ip': self.robot_ip,
                        'robot_port': self.robot_port,
                        'timestamp': time.time()
                    }
                    try:
                        sock.sendto(json.dumps(resp).encode('utf-8'), addr)
                        print(f"[Discovery] Sent DISCOVER_ACK to {addr}")
                        break
                    except Exception as e:
                        print(f"[Discovery] Failed to send ACK to {addr}: {e}")
                        break
            except Exception as e:
                # Avoid tight-spin on persistent errors
                print(f"[Discovery] Loop error: {e}")
                time.sleep(0.1)

    # ------- IR logic -------

    def _send_ir_burst(self, burst_us):
        """Send modulated IR burst"""
        self.pi.wave_clear()
        cycle = [
            pigpio.pulse(1 << IR_TX_GPIO, 0, PULSE_ON_US),
            pigpio.pulse(0, 1 << IR_TX_GPIO, PULSE_OFF_US)
        ]
        self.pi.wave_add_generic(cycle)
        wid = self.pi.wave_create()
        cycles = burst_us // CARRIER_PERIOD_US
        self.pi.wave_chain([255, 0, wid, 255, 1, cycles & 255, (cycles >> 8) & 255])
        while self.pi.wave_tx_busy():
            time.sleep(0.0001)
        self.pi.wave_delete(wid)

    def _send_ir_bit(self, bit):
        """Send IR bit"""
        if bit == 1:
            self._send_ir_burst(BIT_1_BURST)
        else:
            self._send_ir_burst(BIT_0_BURST)
        time.sleep(0.0008)

    def fire_ir(self):
        """Send team ID via IR"""
        if self.ir_state["is_hit"]:
            return  # Can't fire when hit
        current_time = time.time()
        if current_time - self.last_fire_time < FIRE_COOLDOWN:
            return # Can't fire during cooldown
        
        print(f"[IR] Firing! Team {self.team_id}")
        
        # Start bit
        self._send_ir_burst(START_END_BURST)
        time.sleep(0.0008)
        
        # Send 8-bit team ID
        for i in range(8):
            self._send_ir_bit((self.team_id >> (7 - i)) & 1)
        
        # End burst
        self._send_ir_burst(START_END_BURST)
        self.last_fire_time = time.time()
        
    def on_laser_hit(self, attacking_team):
        """Handle being hit by laser - UPDATED with self-hit detection"""
        
        if self.ir_state["is_hit"]:
            return  # Already hit
        
        current_time = time.time()
        # Check for self-hit (for testing)
        if attacking_team == self.team_id:
            print(f"[IR] SELF HIT DETECTED! Team {attacking_team} hit themselves!")
            # For testing, we'll still register it but mark it as a self-hit
            # self.ir_state.update({
            #     "is_hit": True,
            #     "hit_by_team": attacking_team,
            #     "hit_time": time.time(),
            #     "time_remaining": HIT_DISABLE_TIME,
            #     "is_self_hit": True  # Add this flag
            # })
            # threading.Thread(target = begin_hitstun_timer, args=(self,)).start()

            # return
        else:
            print(f"[IR] HIT! Attacked by team {attacking_team}")
            self.ir_state.update({
                "is_hit": True,
                "hit_by_team": attacking_team,
                "hit_time": time.time(),
                "time_remaining": HIT_DISABLE_TIME,
                "is_self_hit": False
            })

            # send data to GV
            # Log the hit
            hit_record = {
                "timestamp": datetime.now().isoformat(),
                "game_time": current_time - self.game_start_time if self.game_start_time else 0,
                "attacking_team": attacking_team,
                "defending_team": self.team_id
            }

            # Send hit notification to Game Viewer
            self.send_hit_report(hit_record)        

            self.stop_all_motors()
            self.enter_standby()
            threading.Thread(target = begin_hitstun_timer, args=(self,)).start()

    def stop_all_motors(self):
        """Stop all motors"""
        for m in MOTORS.values():
            self.pi.set_PWM_dutycycle(m["EN"], 0)
            self.pi.write(m["IN1"], 0)
            self.pi.write(m["IN2"], 0)

    def enter_standby(self):
        """Enter power saving mode"""
        print("[Power] Entering standby mode")
        self.stop_all_motors()
        for s in STBY_PINS:
            self.pi.write(s, 0)

    def exit_standby(self):
        """Exit power saving mode"""
        print("[Power] Exiting standby mode")
        for s in STBY_PINS:
            self.pi.write(s, 1)
        time.sleep(0.01)

    # ------- Communication -------

    def _heartbeat_loop(self):
        """Send periodic heartbeat"""
        while True:
            self.send_heartbeat()
            time.sleep(1)

    def send_heartbeat(self):
        """Send heartbeat to Game Viewer"""
        message = {
            "type": "HEARTBEAT",
            "team_id": self.team_id,
            # "game_active": self.game_state["game_active"],
            "game_active": False,
            # "points": self.game_state["points"],
            "points": 0,
            "timestamp": time.time()
        }
        self._send_to_gv(message)

    def send_hit_report(self, hit_data):
        """Send hit report to Game Viewer"""
        message = {
            "type": "HIT_REPORT",
            "team_id": self.team_id,
            "data": hit_data,
            "timestamp": time.time()
        }
        self._send_to_gv(message)

    def _send_to_gv(self, message):
        """Send message to Game Viewer"""
        try:
            data = json.dumps(message).encode('utf-8')
            self.gv_comm_sock.sendto(data, (self.config["gv_ip"], self.config["gv_comm_port"]))
        except Exception as e:
            print(f"[GameClient] Failed to send to GV: {e}")

    def _listen_loop(self):
        """Listen for messages from Game Viewer"""
        print("[GameClient] Listening for GV messages...")
        
        while True:
            try:
                data, addr = self.gv_comm_sock.recvfrom(4096)
                message = json.loads(data.decode('utf-8'))
                self._handle_message(message)
            
            except socket.timeout:
                continue
            except json.JSONDecodeError:
                continue
            except Exception as e:
                print(f"[GameClient] Listen error: {e}")

    # def _handle_message(self, message):
    #     """Handle incoming message from Game Viewer"""
    #     msg_type = message.get('type')
        
    #     if msg_type == 'READY_CHECK':
    #         print("[GameClient] Ready check received")
    #         # robots stop moving when they are confirmed to be ready
    #         self.stop_all_motors()
    #         self.enter_standby()
        
    #     elif msg_type == 'GAME_START':
    #         print("[GameClient] GAME START!")
    #         self.game_state["game_active"] = True
    #         self.exit_standby()

        
    #     elif msg_type == 'GAME_END':
    #         print("[GameClient] GAME END!")
    #         self.game_state["game_active"] = False
    #         self.stop_all_motors()
    #         self.enter_standby()

        
    #     elif msg_type == 'POINTS_UPDATE':
    #         new_points = message.get('points', 0)
    #         kills = message.get('kills', 0)
    #         deaths = message.get('deaths', 0)
            
    #         self.points = new_points
    #         self.kills = kills
    #         self.deaths = deaths
            
    #         print(f"[GameClient] Points update: {new_points} (K:{kills} D:{deaths})")
            
    #         if self.on_points_update:
    #             self.on_points_update(new_points)
        
    #     elif msg_type == 'PING':
    #         # Respond to ping
    #         response = {
    #             "type": "PONG",
    #             "team_id": self.team_id,
    #             "timestamp": time.time()
    #         }
    #         self._send_to_gv(response)


    def stream(self):
        """Start camera stream to both laptop and game viewer"""
        
        print("[Camera] Starting dual video stream...")
        print(f"[Camera]    | Laptop: {self.operator_ip}:{self.operator_video_port}")
        print(f"[Camera]    | Game Viewer: {self.config["gv_ip"]}:{self.config["gv_video_port"] + self.team_id}")
        
        # Build GStreamer pipeline with tee element for dual output
        cmd = (
            f"rpicam-vid -t 0 "
            f"--width {1280} --height {720} --framerate {30} "
            f"--codec h264 --bitrate {4000000} --profile baseline "
            f"--intra 30 --inline --nopreview -o - | "
            f"gst-launch-1.0 -v fdsrc ! h264parse ! "
            f"tee name=t "
            f"t. ! queue ! rtph264pay config-interval=1 pt=96 ! "
            f"udpsink host={self.operator_ip} port={self.operator_video_port} sync=false async=false "
            f"t. ! queue ! rtph264pay config-interval=1 pt=96 ! "
            f"udpsink host={self.config["gv_ip"]} port={self.config["gv_video_port"] + self.team_id} sync=false async=false"
        )
        
        try:
            self.stream_proc = subprocess.Popen(
                cmd,
                shell=True,
                preexec_fn=os.setsid,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            self.is_streaming = True
            print("[Camera] Streaming started")
            return True
        
        except Exception as e:
            print(f"[Camera] Failed to start stream: {e}")
            return False

    def cleanup_stream(self):
        if self.stream_proc and self.stream_proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.stream_proc.pid), signal.SIGTERM)
                self.stream_proc.wait(timeout=2)
            except Exception as e:
                print(f"[Camera] Error stopping stream: {e}")
                try:
                    os.killpg(os.getpgid(self.stream_proc.pid), signal.SIGKILL)
                except:
                    pass
        
        self.stream_proc = None
        # self.is_streaming = False
        print("[Camera] Stream stopped")

    def cleanup(self):
        pass