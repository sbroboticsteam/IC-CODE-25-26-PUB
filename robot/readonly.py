import time
import sys
import requests
import pigpio

IR_TX_GPIO = 17  # IR transmitter pin
IR_RX_GPIOS = [4, 27, 12]  # IR receiver pins

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

# Motor configuration
MOTORS = {
    "FL": {"EN": 18, "IN1": 23, "IN2": 24}, # Front Left
    "FR": {"EN": 19, "IN1": 25, "IN2": 8}, # Front Right
    "BL": {"EN": 5, "IN1": 22, "IN2": 26}, # Back Left
    "BR": {"EN": 6, "IN1": 16, "IN2": 20}, # Back Right
}
STBY_PINS = [9, 11]

# Game Viewer
GV_IP = "GameViewer.local:8080"

# ========== IR RECEPTION ==========
class IRReceiver():
    def __init__(self, gpio_pin, robot):
        self.gpio = gpio_pin
        self.bursts = []
        self.last_tick = 0
        self.last_burst_time = 0
        self.last_fire_time = 0
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

class RobotBase():
    def __init__(self, team_id):
        self.pi = pigpio.pi()
        self.team_id = team_id

        if not self.pi.connected:
            print("ERROR: pigpiod not running. Run: sudo pigpiod", file=sys.stderr)
            sys.exit(1)

        self.ir_state = {
            "is_hit": False,
            "hit_by_team": 0,
            "hit_time": 0,
            "time_remaining": 0,
            "is_self_hit": False,  # Added for self-hit detection
        }

        self.ir_receivers = []
        for gpio in IR_RX_GPIOS:
            self.ir_receivers.append(IRReceiver(gpio,self))

        try:
            r = requests.put(f"http://{GV_IP}/robots",{"team_id":self.team_id})
        except:
            print("FAILED TO CONNECT TO GAME VIEWER")

    def _send_ir_burst(self, burst_us, pi):
        """Send modulated IR burst"""
        pi.wave_clear()
        cycle = [
            pigpio.pulse(1 << IR_TX_GPIO, 0, PULSE_ON_US),
            pigpio.pulse(0, 1 << IR_TX_GPIO, PULSE_OFF_US)
        ]
        pi.wave_add_generic(cycle)
        wid = pi.wave_create()
        cycles = burst_us // CARRIER_PERIOD_US
        pi.wave_chain([255, 0, wid, 255, 1, cycles & 255, (cycles >> 8) & 255])
        while pi.wave_tx_busy():
            time.sleep(0.0001)
        pi.wave_delete(wid)

    def _send_ir_bit(self,bit):
        """Send IR bit"""
        if bit == 1:
            self._send_ir_burst(BIT_1_BURST)
        else:
            self._send_ir_burst(BIT_0_BURST)
        time.sleep(0.0008)

    def fire_ir(self, team_id):
        """Send team ID via IR"""
        if self.ir_state["is_hit"]:
            return  # Can't fire when hit
        current_time = time.time()
        if current_time - self.last_fire_time < FIRE_COOLDOWN:
            return # Can't fire during cooldown
        
        print(f"[IR] Firing! Team {team_id}")
        
        # Start bit
        self._send_ir_burst(START_END_BURST)
        time.sleep(0.0008)
        
        # Send 8-bit team ID
        for i in range(8):
            self._send_ir_bit((team_id >> (7 - i)) & 1)
        
        # End burst
        self._send_ir_burst(START_END_BURST)
        self.last_fire_time = time.time()
        
    def on_laser_hit(self, attacking_team):
        """Handle being hit by laser - UPDATED with self-hit detection"""
        
        if self.ir_state["is_hit"]:
            return  # Already hit
        
        # Check for self-hit (for testing)
        if attacking_team == self.team_id:
            print(f"[IR] SELF HIT DETECTED! Team {attacking_team} hit themselves!")
            # For testing, we'll still register it but mark it as a self-hit
            self.ir_state.update({
                "is_hit": True,
                "hit_by_team": attacking_team,
                "hit_time": time.time(),
                "time_remaining": HIT_DISABLE_TIME,
                "is_self_hit": True  # Add this flag
            })
        else:
            print(f"[IR] HIT! Attacked by team {attacking_team}")
            self.ir_state.update({
                "is_hit": True,
                "hit_by_team": attacking_team,
                "hit_time": time.time(),
                "time_remaining": HIT_DISABLE_TIME,
                "is_self_hit": False
            })

            hit_data = {"team_attacked":self.team_id, "attacking_team":attacking_team}
            r = requests.put(f"http://{GV_IP}/robots/attacked",hit_data)

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

    # OVERRIDEABLE
    def stream(self):
        pass       

    def cleanup(self):
        pass