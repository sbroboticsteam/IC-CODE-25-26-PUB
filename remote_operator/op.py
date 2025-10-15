import json
import socket
import subprocess
import sys
import time
import threading
import keyboard
import pygame
import os

# ============ USER CONFIG ============
PI_IP = "192.168.50.146"    # Your Pi's IP on IC2026 network
PI_PORT = 5005

AUTO_LAUNCH_GSTREAMER = True
GST_RECEIVER_CMD = (
    r'"C:\\Program Files\gstreamer\\1.0\msvc_x86_64\\bin\\gst-launch-1.0.exe" -v udpsrc port=5600 caps='
    '"application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000,packetization-mode=1" '
    '! rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! d3d11h264dec ! autovideosink sync=false'
)
GST_RECEIVER_CMD_AVD = (
    'gst-launch-1.0 -v udpsrc port=5600 caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000,packetization-mode=1" ! rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! avdec_h264 ! autovideosink sync=false'
)

SEND_HZ = 30 

### Sockets
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) # create a datagram socket 
addr = (PI_IP, PI_PORT)

### Video Stream
gst_proc = None # define a subprocess globally
def open_stream():
    global gst_proc

    if AUTO_LAUNCH_GSTREAMER:
        try:
            gst_proc = subprocess.Popen(GST_RECEIVER_CMD, shell=True) # run the command we wrote in the shell
            print("[Video] GStreamer started")
        except Exception as e:
            print(f"[Video] Failed: {e}")

### Clean Up
def clean_up():
    if gst_proc and gst_proc.poll() is None:
        gst_proc.terminate() # terminate the subprocess 

    sock.close() # stop the socket from running

### Input Loop
def input_loop():
    ### Keyboard Input
    while True:
        # Tank Drive
        left = 0
        if keyboard.is_pressed("w"):
            left = 1
        elif keyboard.is_pressed("s"):
            left = 1
        right = 0
        if keyboard.is_pressed("up"):
            right = 1
        if keyboard.is_pressed("down"):
            right = 1

        payload = {
            "Left": float(left),
            "Right": float(right),
        }

        # Mecanum Drive
        # vx = 0
        # vy = 0
        # if keyboard.is_pressed("w"):
        #     vy = 1
        # elif keyboard.is_pressed("s"):
        #     vy = 1

        # if keyboard.is_pressed("a"):
        #     vx += 1
        # if keyboard.is_pressed("d"):
        #     vx -= 1

        # rot = 0
        # if keyboard.is_pressed("right"):
        #     rot = 1
        # elif keyboard.is_pressed("left"):
        #     rot = -1
        # payload = {
        #     "vx": float(vx),
        #     "vy": float(vy),
        #     "rot": float(rot)
        # }
            
        try:
            sock.sendto(json.dumps(payload).encode("utf-8"), (PI_IP,PI_PORT)) # send our json to our Pi at the appropriate IP an Port
            sock.settimeout(0.001) # set a timoout for the socket
            try:
                data, addr = sock.recvfrom(1024) # try to get a response from the socket
                response = json.loads(data.decode("utf-8"))
                
                # Debug print for self-hits
                if response.get("is_self_hit", False):
                    print(f"[GUI] Self-hit detected in response: {response}")
                    
            except (socket.timeout, json.JSONDecodeError):
                pass
            finally:
                sock.settimeout(None)
            
        except Exception as e:
            pass
            # print(f"UDP error: {e}") # catch and report the error

input_thread = threading.Thread(target=input_loop, daemon=True)

### Main Loop
def main():
    open_stream()

    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    clock = pygame.time.Clock()
    running = True
    input_thread.start()

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                clean_up()
                running = False
                pygame.quit()
            screen.fill("black")

            pygame.display.flip()
        clock.tick(SEND_HZ)  

if __name__ == "__main__":
    main()