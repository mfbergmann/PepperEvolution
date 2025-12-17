#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Pepper Bridge Service v2
Runs ON the Pepper robot - provides HTTP API + WebSocket streams for remote AI control

Deploy to: /home/nao/pepper_bridge_v2.py on Pepper robot
Run with: python2 /home/nao/pepper_bridge_v2.py
"""

import sys
import os
import json
import time
import struct
import base64
import hashlib
import threading
import socket
import select
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from urlparse import urlparse, parse_qs

try:
    import qi
    import qi.logging
except ImportError:
    print("ERROR: NAOqi SDK not found. This script must run on Pepper robot.")
    sys.exit(1)

# Configuration
HOST = '0.0.0.0'
HTTP_PORT = 8888
WS_PORT = 8889
NAOQI_URL = 'tcp://127.0.0.1:9559'

# Camera settings
CAMERA_ID = 0  # 0 = top, 1 = bottom, 2 = depth
CAMERA_RESOLUTION = 1  # 0=QQVGA, 1=QVGA(320x240), 2=VGA(640x480)
CAMERA_COLORSPACE = 11  # RGB
CAMERA_FPS = 15

# Audio settings
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1


class WebSocketServer:
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    
    def __init__(self, host, port, bridge):
        self.host = host
        self.port = port
        self.bridge = bridge
        self.clients = {}
        self.running = False
        self.server_socket = None
        self.lock = threading.Lock()
        self.logger = qi.logging.Logger("WebSocketServer")
        
    def start(self):
        self.running = True
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        self.server_socket.setblocking(False)
        thread = threading.Thread(target=self._run)
        thread.daemon = True
        thread.start()
        self.logger.info("WebSocket server started on port " + str(self.port))
        
    def stop(self):
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        with self.lock:
            for client in self.clients.keys():
                try:
                    client.close()
                except:
                    pass
            self.clients.clear()
            
    def _run(self):
        while self.running:
            try:
                readable, _, _ = select.select([self.server_socket], [], [], 0.1)
                if readable:
                    client_socket, address = self.server_socket.accept()
                    self.logger.info("New connection from " + str(address))
                    thread = threading.Thread(target=self._handle_client, args=(client_socket, address))
                    thread.daemon = True
                    thread.start()
            except Exception as e:
                if self.running:
                    self.logger.warning("Server error: " + str(e))
                    
    def _handle_client(self, client_socket, address):
        try:
            request = client_socket.recv(4096).decode('utf-8')
            key = None
            path = "/"
            for line in request.split('\r\n'):
                if line.startswith('Sec-WebSocket-Key:'):
                    key = line.split(': ')[1].strip()
                elif line.startswith('GET '):
                    path = line.split(' ')[1]
            if not key:
                client_socket.close()
                return
            stream_type = 'video'
            if '/audio' in path:
                stream_type = 'audio'
            elif '/sensors' in path:
                stream_type = 'sensors'
            accept_key = base64.b64encode(hashlib.sha1((key + self.GUID).encode()).digest()).decode()
            response = "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept_key + "\r\n\r\n"
            client_socket.send(response.encode())
            with self.lock:
                self.clients[client_socket] = {'type': stream_type, 'subscribed': True, 'address': address}
            self.logger.info("Client subscribed to " + stream_type + " stream")
            client_socket.setblocking(False)
            while self.running:
                try:
                    readable, _, _ = select.select([client_socket], [], [], 0.5)
                    if readable:
                        data = client_socket.recv(4096)
                        if not data:
                            break
                        if len(data) >= 2 and (data[0] & 0x0F) == 0x08:
                            break
                except socket.error:
                    break
        except Exception as e:
            self.logger.warning("Client handler error: " + str(e))
        finally:
            with self.lock:
                if client_socket in self.clients:
                    del self.clients[client_socket]
            try:
                client_socket.close()
            except:
                pass
            self.logger.info("Client disconnected: " + str(address))
            
    def _create_ws_frame(self, data, opcode=0x02):
        frame = bytearray()
        frame.append(0x80 | opcode)
        length = len(data)
        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.append(126)
            frame.extend(struct.pack('>H', length))
        else:
            frame.append(127)
            frame.extend(struct.pack('>Q', length))
        frame.extend(data)
        return bytes(frame)
        
    def broadcast(self, stream_type, data):
        frame = self._create_ws_frame(data)
        with self.lock:
            dead_clients = []
            for client_socket, info in self.clients.items():
                if info['type'] == stream_type and info['subscribed']:
                    try:
                        client_socket.send(frame)
                    except:
                        dead_clients.append(client_socket)
            for client in dead_clients:
                try:
                    del self.clients[client]
                    client.close()
                except:
                    pass
                    
    def has_subscribers(self, stream_type):
        with self.lock:
            for info in self.clients.values():
                if info['type'] == stream_type and info['subscribed']:
                    return True
        return False


class VideoStreamer:
    def __init__(self, bridge, ws_server):
        self.bridge = bridge
        self.ws_server = ws_server
        self.running = False
        self.video_client = None
        self.logger = qi.logging.Logger("VideoStreamer")
        
    def start(self):
        self.running = True
        thread = threading.Thread(target=self._stream_loop)
        thread.daemon = True
        thread.start()
        self.logger.info("Video streamer started")
        
    def stop(self):
        self.running = False
        self._unsubscribe_camera()
        
    def _subscribe_camera(self):
        if self.video_client:
            return True
        try:
            video_service = self.bridge.get_service("ALVideoDevice")
            if video_service:
                try:
                    video_service.unsubscribe("PepperBridge_Video")
                except:
                    pass
                self.video_client = video_service.subscribeCamera("PepperBridge_Video", CAMERA_ID, CAMERA_RESOLUTION, CAMERA_COLORSPACE, CAMERA_FPS)
                self.logger.info("Subscribed to camera: " + str(self.video_client))
                return True
        except Exception as e:
            self.logger.error("Failed to subscribe to camera: " + str(e))
        return False
        
    def _unsubscribe_camera(self):
        if self.video_client:
            try:
                video_service = self.bridge.get_service("ALVideoDevice")
                if video_service:
                    video_service.unsubscribe(self.video_client)
            except:
                pass
            self.video_client = None
            
    def _stream_loop(self):
        while self.running:
            try:
                if not self.ws_server.has_subscribers('video'):
                    self._unsubscribe_camera()
                    time.sleep(0.5)
                    continue
                if not self._subscribe_camera():
                    time.sleep(1)
                    continue
                video_service = self.bridge.get_service("ALVideoDevice")
                if video_service and self.video_client:
                    image = video_service.getImageRemote(self.video_client)
                    if image:
                        width, height, raw_data = image[0], image[1], image[6]
                        header = json.dumps({'type': 'video', 'width': width, 'height': height, 'format': 'RGB', 'timestamp': time.time()})
                        header_bytes = header.encode('utf-8')
                        packet = struct.pack('>I', len(header_bytes)) + header_bytes + raw_data
                        self.ws_server.broadcast('video', packet)
                time.sleep(1.0 / CAMERA_FPS)
            except Exception as e:
                self.logger.warning("Video stream error: " + str(e))
                self._unsubscribe_camera()
                time.sleep(1)


class AudioStreamer:
    def __init__(self, bridge, ws_server):
        self.bridge = bridge
        self.ws_server = ws_server
        self.running = False
        self.logger = qi.logging.Logger("AudioStreamer")
        
    def start(self):
        self.running = True
        thread = threading.Thread(target=self._stream_loop)
        thread.daemon = True
        thread.start()
        self.logger.info("Audio streamer started")
        
    def stop(self):
        self.running = False
        
    def _stream_loop(self):
        audio_file = "/tmp/pepper_audio_stream.wav"
        chunk_duration = 0.5
        while self.running:
            try:
                if not self.ws_server.has_subscribers('audio'):
                    time.sleep(0.5)
                    continue
                audio_recorder = self.bridge.get_service("ALAudioRecorder")
                if not audio_recorder:
                    time.sleep(1)
                    continue
                try:
                    try:
                        audio_recorder.stopMicrophonesRecording()
                    except:
                        pass
                    audio_recorder.startMicrophonesRecording(audio_file, "wav", AUDIO_SAMPLE_RATE, [0, 0, 1, 0])
                    time.sleep(chunk_duration)
                    audio_recorder.stopMicrophonesRecording()
                    if os.path.exists(audio_file):
                        with open(audio_file, 'rb') as f:
                            audio_data = f.read()
                        header = json.dumps({'type': 'audio', 'format': 'wav', 'sample_rate': AUDIO_SAMPLE_RATE, 'channels': 1, 'timestamp': time.time()})
                        header_bytes = header.encode('utf-8')
                        packet = struct.pack('>I', len(header_bytes)) + header_bytes + audio_data
                        self.ws_server.broadcast('audio', packet)
                        try:
                            os.remove(audio_file)
                        except:
                            pass
                except Exception as e:
                    self.logger.warning("Audio recording error: " + str(e))
                    time.sleep(0.5)
            except Exception as e:
                self.logger.warning("Audio stream error: " + str(e))
                time.sleep(1)


class SensorStreamer:
    def __init__(self, bridge, ws_server):
        self.bridge = bridge
        self.ws_server = ws_server
        self.running = False
        self.logger = qi.logging.Logger("SensorStreamer")
        
    def start(self):
        self.running = True
        thread = threading.Thread(target=self._stream_loop)
        thread.daemon = True
        thread.start()
        self.logger.info("Sensor streamer started")
        
    def stop(self):
        self.running = False
        
    def _stream_loop(self):
        while self.running:
            try:
                if not self.ws_server.has_subscribers('sensors'):
                    time.sleep(0.5)
                    continue
                sensor_data = self._collect_sensor_data()
                if sensor_data:
                    packet = json.dumps(sensor_data).encode('utf-8')
                    self.ws_server.broadcast('sensors', packet)
                time.sleep(0.1)
            except Exception as e:
                self.logger.warning("Sensor stream error: " + str(e))
                time.sleep(1)
                
    def _collect_sensor_data(self):
        data = {'timestamp': time.time()}
        memory = self.bridge.get_service("ALMemory")
        if not memory:
            return None
        try:
            try:
                battery = self.bridge.get_service("ALBattery")
                data['battery'] = {'level': battery.getBatteryCharge(), 'charging': battery.isCharging() if hasattr(battery, 'isCharging') else False}
            except:
                pass
            try:
                data['sonar'] = {
                    'front': memory.getData("Device/SubDeviceList/Platform/Front/Sonar/Sensor/Value"),
                    'back': memory.getData("Device/SubDeviceList/Platform/Back/Sonar/Sensor/Value")
                }
            except:
                pass
            try:
                data['touch'] = {
                    'head_front': memory.getData("Device/SubDeviceList/Head/Touch/Front/Sensor/Value"),
                    'head_middle': memory.getData("Device/SubDeviceList/Head/Touch/Middle/Sensor/Value"),
                    'head_rear': memory.getData("Device/SubDeviceList/Head/Touch/Rear/Sensor/Value"),
                    'left_hand': memory.getData("Device/SubDeviceList/LHand/Touch/Back/Sensor/Value"),
                    'right_hand': memory.getData("Device/SubDeviceList/RHand/Touch/Back/Sensor/Value")
                }
            except:
                pass
            try:
                motion = self.bridge.get_service("ALMotion")
                if motion:
                    data['position'] = {
                        'head_yaw': motion.getAngles("HeadYaw", True)[0] if motion.getAngles("HeadYaw", True) else 0,
                        'head_pitch': motion.getAngles("HeadPitch", True)[0] if motion.getAngles("HeadPitch", True) else 0
                    }
            except:
                pass
            try:
                people_data = memory.getData("PeoplePerception/PeopleDetected")
                if people_data and len(people_data) > 0:
                    data['people'] = {'count': len(people_data[1]) if len(people_data) > 1 else 0, 'data': people_data}
            except:
                pass
        except Exception as e:
            self.logger.warning("Error collecting sensor data: " + str(e))
        return data


class PepperBridge:
    def __init__(self):
        self.session = None
        self.services = {}
        self.logger = qi.logging.Logger("PepperBridge")
        
    def connect(self):
        try:
            self.session = qi.Session()
            self.session.connect(NAOQI_URL)
            if self.session.isConnected():
                self.logger.info("Connected to NAOqi at " + NAOQI_URL)
                try:
                    auto = self.get_service("ALAutonomousLife")
                    if auto:
                        auto.setState("disabled")
                        self.logger.info("Autonomous mode disabled")
                except Exception as e:
                    self.logger.warning("Could not disable autonomous mode: " + str(e))
                try:
                    motion = self.get_service("ALMotion")
                    if motion:
                        motion.wakeUp()
                        posture = self.get_service("ALRobotPosture")
                        if posture:
                            posture.goToPosture("StandInit", 0.5)
                except Exception as e:
                    self.logger.warning("Could not wake up: " + str(e))
                return True
            return False
        except Exception as e:
            self.logger.error("Connection error: " + str(e))
            return False
    
    def get_service(self, service_name):
        if service_name not in self.services:
            try:
                self.services[service_name] = self.session.service(service_name)
            except Exception as e:
                self.logger.error("Failed to get service " + service_name + ": " + str(e))
                return None
        return self.services[service_name]
    
    def robot_name(self):
        try:
            return self.get_service("ALSystem").robotName()
        except:
            return "Unknown"
    
    def robot_model(self):
        try:
            return self.get_service("ALSystem").robotModel()
        except:
            return "Unknown"
    
    def system_version(self):
        try:
            return self.get_service("ALSystem").systemVersion()
        except:
            return "Unknown"
    
    def battery_level(self):
        try:
            return self.get_service("ALBattery").getBatteryCharge()
        except:
            return -1
    
    def wake_up(self):
        try:
            self.get_service("ALMotion").wakeUp()
            self.get_service("ALRobotPosture").goToPosture("StandInit", 0.5)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def rest(self):
        try:
            self.get_service("ALMotion").rest()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_posture(self):
        try:
            return self.get_service("ALRobotPosture").getPosture()
        except:
            return "Unknown"
    
    def set_posture(self, posture, speed=0.5):
        try:
            self.get_service("ALRobotPosture").goToPosture(posture, speed)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def move_forward(self, distance):
        try:
            motion = self.get_service("ALMotion")
            motion.wakeUp()
            motion.moveTo(float(distance), 0, 0)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def turn(self, angle):
        try:
            motion = self.get_service("ALMotion")
            motion.wakeUp()
            motion.moveTo(0, 0, float(angle) * 3.14159 / 180.0)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def move_head(self, yaw, pitch, speed=0.2):
        try:
            motion = self.get_service("ALMotion")
            motion.setStiffnesses("Head", 1.0)
            yaw_rad = float(yaw) * 3.14159 / 180.0
            pitch_rad = float(pitch) * 3.14159 / 180.0
            motion.setAngles(["HeadYaw", "HeadPitch"], [yaw_rad, pitch_rad], speed)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def stop_motion(self):
        try:
            self.get_service("ALMotion").stopMove()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def speak(self, text, language="English", animated=True):
        try:
            self.get_service("ALMotion").wakeUp()
            if animated:
                tts = self.get_service("ALAnimatedSpeech")
                tts.say(str(text))
            else:
                tts = self.get_service("ALTextToSpeech")
                tts.setLanguage(language)
                tts.say(str(text))
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def set_volume(self, volume):
        try:
            tts = self.get_service("ALTextToSpeech")
            tts.setVolume(float(volume) / 100.0)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def play_animation(self, animation_name):
        try:
            self.get_service("ALMotion").wakeUp()
            anim = self.get_service("ALAnimationPlayer")
            anim.run(animation_name)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def list_animations(self):
        try:
            anim = self.get_service("ALAnimationPlayer")
            return {"animations": anim.getAnimationList()}
        except Exception as e:
            return {"error": str(e)}
    
    def set_eye_color(self, color):
        colors = {'red': 0xFF0000, 'green': 0x00FF00, 'blue': 0x0000FF, 'white': 0xFFFFFF, 'yellow': 0xFFFF00, 'magenta': 0xFF00FF, 'cyan': 0x00FFFF, 'off': 0x000000}
        try:
            leds = self.get_service("ALLeds")
            color_val = colors.get(color.lower(), colors['white'])
            leds.fadeRGB("FaceLeds", color_val, 0.5)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def tablet_show_image(self, url):
        try:
            tablet = self.get_service("ALTabletService")
            tablet.showImage(url)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def tablet_show_web(self, url):
        try:
            tablet = self.get_service("ALTabletService")
            tablet.showWebview()
            tablet.loadUrl(url)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def tablet_show_text(self, text, background_color="#000000"):
        try:
            tablet = self.get_service("ALTabletService")
            html = """<html><head><style>body{{background-color:{bg};color:white;font-family:Arial;font-size:48px;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;padding:20px;text-align:center;}}</style></head><body>{text}</body></html>""".format(bg=background_color, text=text)
            html_file = "/tmp/pepper_display.html"
            with open(html_file, 'w') as f:
                f.write(html)
            tablet.showWebview()
            tablet.loadUrl("file://" + html_file)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def tablet_hide(self):
        try:
            tablet = self.get_service("ALTabletService")
            tablet.hideWebview()
            tablet.hideImage()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def tablet_set_brightness(self, brightness):
        try:
            tablet = self.get_service("ALTabletService")
            tablet.setBrightness(float(brightness) / 100.0)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def set_awareness(self, enabled):
        try:
            awareness = self.get_service("ALBasicAwareness")
            if enabled:
                awareness.startAwareness()
            else:
                awareness.stopAwareness()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def take_picture(self):
        try:
            video = self.get_service("ALVideoDevice")
            client = video.subscribeCamera("PepperBridge_Snap", 0, 2, 11, 5)
            image = video.getImageRemote(client)
            video.unsubscribe(client)
            if image:
                return {"success": True, "width": image[0], "height": image[1], "format": "RGB", "data": base64.b64encode(image[6]).decode('utf-8')}
            return {"success": False, "error": "No image captured"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class BridgeHTTPHandler(BaseHTTPRequestHandler):
    bridge = None
    
    def _send_response(self, response):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(json.dumps(response))
    
    def do_GET(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        try:
            if path == '/health' or path == '/':
                response = {"status": "healthy", "version": "2.0", "streams": {"video": "ws://" + self.headers.get('Host', '').split(':')[0] + ":" + str(WS_PORT) + "/video", "audio": "ws://" + self.headers.get('Host', '').split(':')[0] + ":" + str(WS_PORT) + "/audio", "sensors": "ws://" + self.headers.get('Host', '').split(':')[0] + ":" + str(WS_PORT) + "/sensors"}}
            elif path == '/status':
                response = {"robot_name": self.bridge.robot_name(), "robot_model": self.bridge.robot_model(), "system_version": self.bridge.system_version(), "battery": self.bridge.battery_level(), "posture": self.bridge.get_posture()}
            elif path == '/animations':
                response = self.bridge.list_animations()
            elif path == '/picture':
                response = self.bridge.take_picture()
            else:
                response = {"error": "Unknown endpoint: " + path}
            self._send_response(response)
        except Exception as e:
            self._send_response({"error": str(e)})
    
    def do_POST(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}
        try:
            if path == '/wake_up' or path == '/wakeup':
                response = self.bridge.wake_up()
            elif path == '/rest':
                response = self.bridge.rest()
            elif path == '/posture':
                response = self.bridge.set_posture(data.get('posture', 'StandInit'), data.get('speed', 0.5))
            elif path == '/move/forward':
                response = self.bridge.move_forward(data.get('distance', 0.5))
            elif path == '/move/turn':
                response = self.bridge.turn(data.get('angle', 90))
            elif path == '/move/head':
                response = self.bridge.move_head(data.get('yaw', 0), data.get('pitch', 0), data.get('speed', 0.2))
            elif path == '/stop':
                response = self.bridge.stop_motion()
            elif path == '/speak':
                response = self.bridge.speak(data.get('text', data.get('message', '')), data.get('language', 'English'), data.get('animated', True))
            elif path == '/volume':
                response = self.bridge.set_volume(data.get('volume', 50))
            elif path == '/animation':
                response = self.bridge.play_animation(data.get('animation', ''))
            elif path == '/leds/eyes':
                response = self.bridge.set_eye_color(data.get('color', 'white'))
            elif path == '/tablet/image':
                response = self.bridge.tablet_show_image(data.get('url', ''))
            elif path == '/tablet/web':
                response = self.bridge.tablet_show_web(data.get('url', ''))
            elif path == '/tablet/text':
                response = self.bridge.tablet_show_text(data.get('text', ''), data.get('background', '#000000'))
            elif path == '/tablet/hide':
                response = self.bridge.tablet_hide()
            elif path == '/tablet/brightness':
                response = self.bridge.tablet_set_brightness(data.get('brightness', 100))
            elif path == '/awareness':
                response = self.bridge.set_awareness(data.get('enabled', False))
            else:
                response = {"error": "Unknown endpoint: " + path}
            self._send_response(response)
        except Exception as e:
            self._send_response({"error": str(e)})
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def log_message(self, format, *args):
        pass


def main():
    print("=" * 60)
    print("Pepper Bridge Service v2")
    print("=" * 60)
    print("Connecting to NAOqi...")
    bridge = PepperBridge()
    if not bridge.connect():
        print("ERROR: Failed to connect to NAOqi")
        sys.exit(1)
    print("[OK] Connected to NAOqi")
    print("Starting WebSocket server...")
    ws_server = WebSocketServer(HOST, WS_PORT, bridge)
    ws_server.start()
    print("[OK] WebSocket server on port " + str(WS_PORT))
    print("Starting video streamer...")
    video_streamer = VideoStreamer(bridge, ws_server)
    video_streamer.start()
    print("[OK] Video streamer ready")
    print("Starting audio streamer...")
    audio_streamer = AudioStreamer(bridge, ws_server)
    audio_streamer.start()
    print("[OK] Audio streamer ready")
    print("Starting sensor streamer...")
    sensor_streamer = SensorStreamer(bridge, ws_server)
    sensor_streamer.start()
    print("[OK] Sensor streamer ready")
    print("Starting HTTP server...")
    BridgeHTTPHandler.bridge = bridge
    http_server = HTTPServer((HOST, HTTP_PORT), BridgeHTTPHandler)
    print("[OK] HTTP server on port " + str(HTTP_PORT))
    print("=" * 60)
    print("Bridge ready!")
    print("")
    print("HTTP API:      http://0.0.0.0:" + str(HTTP_PORT))
    print("Video stream:  ws://0.0.0.0:" + str(WS_PORT) + "/video")
    print("Audio stream:  ws://0.0.0.0:" + str(WS_PORT) + "/audio")
    print("Sensor stream: ws://0.0.0.0:" + str(WS_PORT) + "/sensors")
    print("=" * 60)
    print("Press Ctrl+C to stop")
    print("")
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        video_streamer.stop()
        audio_streamer.stop()
        sensor_streamer.stop()
        ws_server.stop()
        http_server.shutdown()
        print("Stopped")


if __name__ == "__main__":
    main()
