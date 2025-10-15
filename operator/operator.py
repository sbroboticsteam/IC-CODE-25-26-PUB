import json
import socket
import subprocess
import sys
import time
import threading
import pygame
import os

# ============ USER CONFIG ============
PI_IP = ""    # Your Pi's IP on IC2026 network
PI_PORT = 5005

AUTO_LAUNCH_GSTREAMER = True
GST_RECEIVER_CMD = (
    'gst-launch-1.0 -v udpsrc port=5600 caps='
    '"application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000,packetization-mode=1" '
    '! rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! d3d11h264dec ! autovideosink sync=false'
)
GST_RECEIVER_CMD_AVD = (
    'gst-launch-1.0 -v udpsrc port=5600 caps="'
    'application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000,packetization-mode=1" '
    '! rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! avdec_h264 ! autovideosink sync=false'
)


def main():
    pass
if __name__ == "__main__":
    main()