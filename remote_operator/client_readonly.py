import socket
import threading
import sys
import os 
import json
import time
import datetime

class GVClient():
    def __init__(self, config):
        self.config = config
        self.team_id = config["team_id"]
        self.team_name = config["team_name"]
        self.robot_name = config.get("robot_name", self.team_name)

        self.robot_ip = config["robot_ip"]
        self.robot_port = config["robot_port"]

        self.operator_ip = config["operator_ip"]
        self.operator_input_port = config["operator_input_port"]
        self.operator_video_port = config["operator_video_port"]

        self.gv_listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.gv_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.gv_listener.settimeout(1.0)

        # Game State
        self.game_state = {
            "connected" : False,
            "game_active" : False,
            "is_ready" : False,
            "points" : 0,
            "deaths" : 0,
            "kills" : 0
        }

        listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        listener_thread.start()

    def discover_robot(self, retries = 5, timeout = 1):
        """Discover robot by sending a DISCOVER UDP packet to port 5500 and awaiting
        a DISCOVER_ACK response.
        """
        print("[DISCOVER] ")
        message = {
            "type": "DISCOVER",
            "team_id": self.team_id,
            "request_time": time.time()
        }

        data = json.dumps(message).encode('utf-8')

        disc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        disc.settimeout(timeout)

        try:
            for attempt in range(retries):
                try:
                    disc.sendto(data, (self.robot_ip, 6500))
                    resp, addr = disc.recvfrom(4096)
                    try:
                        resp_msg = json.loads(resp.decode('utf-8'))
                    except Exception:
                        print(f"[DISCOVER] Invalid JSON from {addr}")
                        continue

                    if resp_msg.get('type') == 'DISCOVER_ACK':
                        print(f"[DISCOVER] Robot discovered at {addr}: {resp_msg.get('robot_name')}")
                        return True
                    else:
                        print(f"[DISCOVER] Unexpected response type from {addr}: {resp_msg.get('type')}")
                except socket.timeout:
                    print(f"[DISCOVER] Attempt {attempt + 1} timed out, retrying...")
                    continue
                except Exception as e:
                    print(f"[DISCOVER] Error during discovery attempt {attempt + 1}: {e}")
                    continue

        finally:
            try:
                disc.close()
            except Exception:
                pass
            
        print("[DISCOVER] Robot not found")
        return False

    def send_registration(self, listen_port):
        """Register this laptop with Game Viewer"""
        message = {
            'type': 'REGISTER',
            'team_id': self.team_id,
            'team_name': self.team_name,  # Use team info from Pi
            'robot_name': self.robot_name,  # Use team info from Pi
            'listen_port': listen_port
        }
        self._send_to_gv(message)
        print(f"[GV] Sent registration")

    def _send_to_gv(self, message):
        """Send message to Game Viewer"""
        try:
            data = json.dumps(message).encode('utf-8')
            self.gv_listener.sendto(data, (self.config["gv_ip"], self.config["gv_comm_port"]))
        except Exception as e:
            print(f"[GameClient] Failed to send to GV: {e}")

    def _listen_loop(self):
        """Listen for messages from Game Viewer"""
        print("[GameClient] Listening for GV messages...")
        
        while True:
            try:
                data, addr = self.gv_listener.recvfrom(4096)
                message = json.loads(data.decode('utf-8'))
                self._handle_message(message)
            
            except socket.timeout:
                continue
            except json.JSONDecodeError:
                continue
            except Exception as e:
                print(f"[GameClient] Listen error: {e}")

    def _handle_message(self, message):
        """Handle incoming message from Game Viewer"""
        msg_type = message.get('type')
        
        if msg_type == 'READY_CHECK':
            print("[GameClient] Robot Readied up!")
            self.game_state["is_ready"] = True

        elif msg_type == 'GAME_START':
            print("[GameClient] GAME START!")
            self.game_state["game_active"] = True
        
        elif msg_type == 'GAME_END':
            print("[GameClient] GAME END!")
            self.game_state["game_active"] = False
            self.stop_all_motors()
            self.enter_standby()

        elif msg_type == 'POINTS_UPDATE':
            new_points = message.get('points', 0)
            kills = message.get('kills', 0)
            deaths = message.get('deaths', 0)
            
            self.points = new_points
            self.kills = kills
            self.deaths = deaths
            
            print(f"[GameClient] Points update: {new_points} (K:{kills} D:{deaths})")
            
            if self.on_points_update:
                self.on_points_update(new_points)
        
        elif msg_type == 'PING':
            # Respond to ping
            response = {
                "type": "PONG",
                "team_id": self.team_id,
                "timestamp": time.time()
            }
            self._send_to_gv(response)
