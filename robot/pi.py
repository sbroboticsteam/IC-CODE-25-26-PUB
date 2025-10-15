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

from readonly import RobotBase

OPERATOR_IP = "" # your laptop/pc ip address on IC2026 Network

class Robot(RobotBase):
    def __init__(self, team_id):
        super().__init__(self, team_id)
        pass

    def run():
        pass

if __name__ == "__main__":
    team_id = 0  # your team id here

    robot = Robot(team_id)
    try:
        asyncio.run(robot.run())
    except KeyboardInterrupt:
        print("\n[Shutdown] Received interrupt")
    except Exception as e:
        print(f"[Error] {e}")
    finally:
        robot.cleanup()