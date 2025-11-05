#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Pepper Bridge Service
Runs on Pepper robot to provide HTTP API for remote control
Connects to NAOqi locally and exposes REST API
"""

import sys
import os
import json
import time
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from urlparse import urlparse, parse_qs
import threading

# NAOqi imports (available on Pepper)
try:
    import qi
    import qi.logging
except ImportError:
    print("ERROR: NAOqi SDK not found. This script must run on Pepper robot.")
    sys.exit(1)

# Configuration
HOST = '0.0.0.0'  # Listen on all interfaces
PORT = 8888  # Bridge API port
NAOQI_URL = 'tcp://127.0.0.1:9559'  # Local NAOqi connection

class PepperBridge:
    """Bridge between HTTP API and NAOqi services"""
    
    def __init__(self):
        self.session = None
        self.services = {}
        self.logger = qi.logging.Logger("PepperBridge")
        
    def connect(self):
        """Connect to NAOqi"""
        try:
            self.session = qi.Session()
            self.session.connect(NAOQI_URL)
            
            if self.session.isConnected():
                self.logger.info("Connected to NAOqi at " + NAOQI_URL)
                
                # Disable autonomous mode to allow full control
                try:
                    autonomous_service = self.get_service("ALAutonomousLife")
                    if autonomous_service:
                        autonomous_service.setState("disabled")
                        self.logger.info("Autonomous mode disabled")
                        
                        # Also stop any speech recognition from autonomous mode
                        try:
                            asr_service = self.get_service("ALSpeechRecognition")
                            if asr_service:
                                # Unsubscribe any autonomous mode subscriptions
                                try:
                                    asr_service.pause(True)
                                    time.sleep(0.5)
                                except:
                                    pass
                        except:
                            pass
                except Exception as e:
                    self.logger.warning("Could not disable autonomous mode: " + str(e))
                
                # Wake up Pepper and set initial posture
                try:
                    motion_service = self.get_service("ALMotion")
                    if motion_service:
                        motion_service.wakeUp()
                        self.logger.info("Pepper woken up")
                        
                        # Set initial posture
                        try:
                            posture_service = self.get_service("ALRobotPosture")
                            if posture_service:
                                posture_service.goToPosture("StandInit", 0.5)
                                self.logger.info("Set initial posture to StandInit")
                        except Exception as e:
                            self.logger.warning("Could not set initial posture: " + str(e))
                except Exception as e:
                    self.logger.warning("Could not wake up Pepper: " + str(e))
                
                return True
            else:
                self.logger.error("Failed to connect to NAOqi")
                return False
        except Exception as e:
            self.logger.error("Connection error: " + str(e))
            return False
    
    def get_service(self, service_name):
        """Get a NAOqi service (with caching)"""
        if service_name not in self.services:
            try:
                self.services[service_name] = self.session.service(service_name)
            except Exception as e:
                self.logger.error("Failed to get service " + service_name + ": " + str(e))
                return None
        return self.services[service_name]
    
    def robot_name(self):
        """Get robot name"""
        try:
            service = self.get_service("ALSystem")
            return service.robotName()
        except Exception as e:
            return {"error": str(e)}
    
    def robot_model(self):
        """Get robot model"""
        try:
            service = self.get_service("ALSystem")
            return service.robotModel()
        except Exception as e:
            return {"error": str(e)}
    
    def system_version(self):
        """Get system version"""
        try:
            service = self.get_service("ALSystem")
            return service.systemVersion()
        except Exception as e:
            return {"error": str(e)}
    
    def battery_level(self):
        """Get battery level"""
        try:
            service = self.get_service("ALBattery")
            return service.getBatteryCharge()
        except Exception as e:
            return {"error": str(e)}
    
    def speak(self, text, language="English"):
        """Make robot speak"""
        try:
            # Ensure robot is awake (speaking requires robot to be awake)
            try:
                motion_service = self.get_service("ALMotion")
                motion_service.wakeUp()
            except:
                pass
            
            service = self.get_service("ALTextToSpeech")
            service.setLanguage(language)
            service.say(str(text))
            return {"success": True, "message": "Spoke: " + str(text)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def move_forward(self, distance):
        """Move robot forward"""
        try:
            service = self.get_service("ALMotion")
            # Ensure robot is awake
            try:
                service.wakeUp()
            except:
                pass
            service.moveTo(float(distance), 0, 0)
            return {"success": True, "message": "Moved forward " + str(distance) + "m"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def turn(self, angle):
        """Turn robot"""
        try:
            service = self.get_service("ALMotion")
            # Ensure robot is awake
            try:
                service.wakeUp()
            except:
                pass
            service.moveTo(0, 0, float(angle) * 3.14159 / 180.0)  # Convert to radians
            return {"success": True, "message": "Turned " + str(angle) + " degrees"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def wake_up(self):
        """Wake up Pepper robot"""
        try:
            service = self.get_service("ALMotion")
            service.wakeUp()
            
            # Set initial posture
            try:
                posture_service = self.get_service("ALRobotPosture")
                if posture_service:
                    posture_service.goToPosture("StandInit", 0.5)
            except:
                pass
            
            return {"success": True, "message": "Pepper woken up"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_posture(self):
        """Get current posture"""
        try:
            service = self.get_service("ALRobotPosture")
            return service.getPosture()
        except Exception as e:
            return {"error": str(e)}
    
    def stop_motion(self):
        """Stop all motion"""
        try:
            service = self.get_service("ALMotion")
            service.stopMove()
            return {"success": True, "message": "Motion stopped"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def play_animation(self, animation_name):
        """Play an animation on the robot"""
        try:
            service = self.get_service("ALAnimationPlayer")
            if not service:
                return {"success": False, "error": "Animation service not available"}
            
            # Ensure robot is awake
            try:
                motion_service = self.get_service("ALMotion")
                motion_service.wakeUp()
            except:
                pass
            
            # Check if animation exists
            try:
                available_animations = service.getAnimationList()
                if animation_name not in available_animations:
                    # Try to find similar animation
                    self.logger.warning("Animation " + animation_name + " not found. Available animations: " + str(len(available_animations)))
                    # Try common alternatives
                    alternatives = {
                        "animations/Stand/Gestures/Hey_1": ["animations/Stand/Gestures/Hey_1", "animations/Stand/Gestures/Hey_3", "animations/Stand/Gestures/Hey_4"],
                        "animations/Stand/Gestures/Enthusiastic_4": ["animations/Stand/Gestures/Enthusiastic_4", "animations/Stand/Gestures/Enthusiastic_3", "animations/Stand/Gestures/Yes_1"]
                    }
                    
                    if animation_name in alternatives:
                        for alt in alternatives[animation_name]:
                            if alt in available_animations:
                                animation_name = alt
                                self.logger.info("Using alternative animation: " + alt)
                                break
            except:
                # If we can't check, just try to play it
                pass
            
            # Play the animation - use runTag for non-blocking or run for blocking
            # Using runTag with a unique tag so we can track it
            import time
            tag = "PepperBridge_" + str(int(time.time() * 1000))
            try:
                service.runTag(animation_name, tag)
                self.logger.info("Playing animation: " + animation_name + " with tag: " + tag)
                return {"success": True, "message": f"Playing animation: {animation_name}", "tag": tag}
            except:
                # Fallback to run() if runTag fails
                try:
                    service.run(animation_name)
                    self.logger.info("Playing animation: " + animation_name + " (blocking)")
                    return {"success": True, "message": f"Playing animation: {animation_name}"}
                except Exception as e2:
                    return {"success": False, "error": str(e2)}
        except Exception as e:
            self.logger.error("Animation error: " + str(e))
            return {"success": False, "error": str(e)}
    
    def get_sensor_data(self):
        """Get sensor data"""
        try:
            data = {}
            
            # Battery
            try:
                battery = self.get_service("ALBattery")
                data["battery"] = battery.getBatteryCharge()
            except:
                pass
            
            # Get memory events (sensor data is often in memory)
            try:
                memory = self.get_service("ALMemory")
                # Get some common sensor values
                data["sensors"] = {}
                # You can extend this to get specific sensor values
            except:
                pass
            
            return data
        except Exception as e:
            return {"error": str(e)}
    
    def get_available_services(self):
        """List available NAOqi services"""
        try:
            service_manager = self.get_service("ServiceManager")
            services = service_manager.services()
            return {"services": list(services)}
        except Exception as e:
            return {"error": str(e)}
    
    def listen_for_speech(self, timeout=5.0, language="English"):
        """Listen for speech and return transcribed text using ALSpeechRecognition with dynamic vocabulary"""
        try:
            # Get speech recognition service
            asr_service = self.get_service("ALSpeechRecognition")
            if not asr_service:
                return {"success": False, "error": "Speech recognition service not available"}
            
            # First, cleanup any existing subscriptions
            try:
                # Unsubscribe first (this stops the subscription)
                asr_service.unsubscribe("PepperBridge")
                time.sleep(0.2)
            except:
                pass
            
            # Pause the engine to allow configuration changes
            try:
                asr_service.pause(True)
                time.sleep(0.3)
            except Exception as e:
                # If pause fails, try to continue - might already be paused
                self.logger.warning("Could not pause ASR: " + str(e))
            
            # Check if engine is paused - if not, we need to stop autonomous life's ASR
            try:
                is_paused = asr_service.isPaused()
                if not is_paused:
                    # Try harder to pause
                    try:
                        asr_service.pause(True)
                        time.sleep(0.3)
                    except:
                        pass
            except:
                pass
            
            # Set language
            try:
                asr_service.setLanguage(language)
            except Exception as e:
                self.logger.warning("Could not set language: " + str(e))
            
            # Use a large vocabulary for natural speech (common words)
            vocabulary = [
                "hello", "hi", "hey", "goodbye", "bye", "thanks", "thank you",
                "yes", "no", "please", "sorry", "excuse me",
                "what", "where", "when", "why", "how", "who",
                "can", "could", "would", "should", "will",
                "tell", "say", "speak", "talk", "listen",
                "move", "turn", "walk", "stop", "go",
                "wave", "gesture", "nod", "point",
                "my", "name", "is", "are", "am", "you", "your",
                "the", "a", "an", "and", "or", "but",
                "this", "that", "these", "those",
                "I", "me", "we", "us", "they", "them",
                "do", "does", "did", "done",
                "have", "has", "had",
                "be", "been", "being",
                "make", "made", "get", "got", "give", "gave",
                "see", "saw", "know", "knew", "think", "thought",
                "want", "need", "like", "love", "hate",
                "help", "show", "play", "work", "use",
                "time", "day", "now", "today", "tomorrow", "yesterday",
                "here", "there", "where", "everywhere",
                "good", "bad", "great", "nice", "fine", "okay", "ok",
                "robot", "pepper", "assistant", "friend"
            ]
            
            # Set vocabulary - must pause engine first
            try:
                asr_service.setVocabulary(vocabulary, False)  # False = no word spotting
            except Exception as e:
                self.logger.warning("Could not set vocabulary: " + str(e))
                # Try to continue without vocabulary
            
            # Resume/pause off
            try:
                asr_service.pause(False)
            except:
                pass
            
            # Subscribe to word recognition
            try:
                asr_service.subscribe("PepperBridge")
            except Exception as e:
                self.logger.warning("Could not subscribe: " + str(e))
                return {"success": False, "error": "Failed to subscribe to speech recognition: " + str(e)}
            
            # Wait for speech using WordRecognized memory event
            import time
            start_time = time.time()
            memory = self.get_service("ALMemory")
            
            # Clear any previous recognition
            try:
                memory.getData("WordRecognized")
            except:
                pass
            
            recognized_words_list = []
            
            while time.time() - start_time < timeout:
                try:
                    # Check for recognized words
                    word_data = memory.getData("WordRecognized")
                    if word_data and len(word_data) > 0:
                        # Format: [["word1", "word2", ...], confidence]
                        if isinstance(word_data, list) and len(word_data) > 0:
                            words = word_data[0]
                            confidence = word_data[1] if len(word_data) > 1 else 1.0
                            
                            if words and len(words) > 0:
                                # Collect words until we get a complete phrase or timeout
                                recognized_words_list.extend(words)
                                
                                # If confidence is high or we have multiple words, return
                                if confidence > 0.3 or len(recognized_words_list) >= 3:
                                    recognized_text = " ".join(recognized_words_list)
                                    asr_service.unsubscribe("PepperBridge")
                                    self.logger.info("Recognized: " + recognized_text)
                                    return {"success": True, "text": recognized_text}
                                
                                # Reset after short pause (word recognition gives words incrementally)
                                time.sleep(0.5)  # Wait for more words
                except Exception as e:
                    # No word recognized yet, continue waiting
                    pass
                
                time.sleep(0.2)  # Check every 200ms
            
            # If we collected some words, return them
            if recognized_words_list:
                recognized_text = " ".join(recognized_words_list)
                try:
                    asr_service.unsubscribe("PepperBridge")
                except:
                    pass
                self.logger.info("Recognized (partial): " + recognized_text)
                return {"success": True, "text": recognized_text}
            
            # Timeout - unsubscribe
            try:
                asr_service.unsubscribe("PepperBridge")
            except:
                pass
            
            return {"success": False, "text": "", "error": "Timeout - no speech detected"}
            
        except Exception as e:
            self.logger.error("Speech recognition error: " + str(e))
            return {"success": False, "error": str(e)}


class BridgeHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for bridge API"""
    
    bridge = None
    
    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        query_params = parse_qs(parsed_path.query)
        
        # Set CORS headers
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        
        try:
            if path == '/health' or path == '/':
                response = {
                    "status": "healthy",
                    "connected": self.bridge.session.isConnected() if self.bridge.session else False,
                    "robot": self.bridge.robot_name() if self.bridge.session else None
                }
            
            elif path == '/status':
                response = {
                    "robot_name": self.bridge.robot_name(),
                    "robot_model": self.bridge.robot_model(),
                    "system_version": self.bridge.system_version(),
                    "battery": self.bridge.battery_level(),
                    "posture": self.bridge.get_posture()
                }
            
            elif path == '/sensors':
                response = self.bridge.get_sensor_data()
            
            elif path == '/services':
                response = self.bridge.get_available_services()
            
            else:
                response = {"error": "Unknown endpoint: " + path}
            
            self.wfile.write(json.dumps(response))
            
        except Exception as e:
            error_response = {"error": str(e)}
            self.wfile.write(json.dumps(error_response))
    
    def do_POST(self):
        """Handle POST requests"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        # Read request body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}
        
        # Set CORS headers
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        
        try:
            if path == '/wakeup' or path == '/wake_up':
                response = self.bridge.wake_up()
            elif path == '/speak':
                text = data.get('message', data.get('text', ''))
                language = data.get('language', 'English')
                response = self.bridge.speak(text, language)
            
            elif path == '/move/forward':
                distance = data.get('distance', 0.5)
                response = self.bridge.move_forward(distance)
            
            elif path == '/move/turn':
                angle = data.get('angle', 90)
                response = self.bridge.turn(angle)
            
            elif path == '/stop':
                response = self.bridge.stop_motion()
            
            elif path == '/animation':
                animation_name = data.get('animation', '')
                if animation_name:
                    response = self.bridge.play_animation(animation_name)
                else:
                    response = {"error": "Animation name required"}
            
            elif path == '/led/eyes':
                color = data.get('color', 'blue')
                response = self.bridge.set_eye_color(color)
            
            elif path == '/listen':
                timeout = data.get('timeout', 5.0)
                language = data.get('language', 'English')
                response = self.bridge.listen_for_speech(timeout, language)
            
            else:
                response = {"error": "Unknown endpoint: " + path}
            
            self.wfile.write(json.dumps(response))
            
        except Exception as e:
            error_response = {"error": str(e)}
            self.wfile.write(json.dumps(error_response))
    
    def do_OPTIONS(self):
        """Handle CORS preflight"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def log_message(self, format, *args):
        """Override to use NAOqi logging"""
        if self.bridge:
            self.bridge.logger.info(format % args)


def run_server():
    """Run the bridge server"""
    print("=" * 60)
    print("Pepper Bridge Service")
    print("=" * 60)
    print("Connecting to NAOqi...")
    
    bridge = PepperBridge()
    BridgeHTTPHandler.bridge = bridge
    
    if not bridge.connect():
        print("ERROR: Failed to connect to NAOqi. Exiting.")
        sys.exit(1)
    
    print("✓ Connected to NAOqi")
    print("Starting HTTP server on " + HOST + ":" + str(PORT) + "...")
    
    server = HTTPServer((HOST, PORT), BridgeHTTPHandler)
    
    print("✓ Server started")
    print("=" * 60)
    print("Bridge API available at: http://" + HOST + ":" + str(PORT))
    print("Endpoints:")
    print("  GET  /health    - Health check")
    print("  GET  /status    - Robot status")
    print("  GET  /sensors   - Sensor data")
    print("  GET  /services  - Available services")
    print("  POST /speak     - Make robot speak")
    print("  POST /move/forward - Move forward")
    print("  POST /move/turn - Turn robot")
    print("  POST /stop      - Stop motion")
    print("=" * 60)
    print("Press Ctrl+C to stop")
    print("")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.shutdown()
        if bridge.session:
            bridge.session.close()
        print("Server stopped")


if __name__ == "__main__":
    run_server()

