# Bridge Mode Implementation Progress

## Date: November 5, 2024

## What We Accomplished Today

### 1. OpenAI API Fixes for gpt-5-mini
- **Issue**: OpenAI API errors with `max_tokens` parameter for newer models
- **Fix**: Implemented dynamic parameter selection (`max_completion_tokens` for gpt-5-mini, `max_tokens` for older models)
- **Fix**: Removed `temperature` parameter for gpt-5 models (only support default value of 1)
- **Files Modified**: `src/ai/models.py`
- **Status**: ✅ Complete

### 2. Removed Mock Mode Dependency
- **Issue**: System was falling back to mock mode even when bridge was enabled
- **Fix**: Removed `_using_mock` check from connection initialization
- **Fix**: Connection now always uses bridge when `USE_PEPPER_BRIDGE=true`
- **Files Modified**: `src/pepper/connection.py`
- **Status**: ✅ Complete

### 3. Fixed Environment Variable Reading
- **Issue**: Environment variables were read at module import time, before `load_dotenv()` was called
- **Fix**: Moved environment variable reading to `__init__` method (runtime) instead of module level
- **Fix**: Changed default to `true` (bridge enabled unless explicitly set to false)
- **Files Modified**: `src/pepper/connection.py`
- **Status**: ✅ Complete

### 4. Added Animation Support
- **Added**: `play_animation()` method to bridge service (`pepper_bridge.py`)
- **Added**: `play_animation()` method to bridge client (`bridge_client.py`)
- **Added**: `/animation` endpoint to bridge HTTP handler
- **Enhanced**: Animation execution with existence checking and fallback alternatives
- **Enhanced**: Uses `runTag()` for non-blocking execution with fallback to `run()`
- **Files Modified**: `pepper_bridge.py`, `src/pepper/bridge_client.py`
- **Status**: ✅ Complete

### 5. Added LED Support
- **Added**: `set_eye_color()` method to bridge service (`pepper_bridge.py`)
- **Added**: `set_eye_color()` method to bridge client (`bridge_client.py`)
- **Added**: `/led/eyes` endpoint to bridge HTTP handler
- **Files Modified**: `pepper_bridge.py`, `src/pepper/bridge_client.py`
- **Status**: ✅ Complete

### 6. Updated All Actuators to Use Bridge
- **Updated**: `speak()` method to use bridge client
- **Updated**: `wave_hand()` method to use bridge for animations
- **Updated**: `nod_head()` method to use bridge for animations
- **Updated**: `set_eye_color()` method to use bridge
- **Enhanced**: All methods now use `getattr()` for reliable bridge detection
- **Added**: Comprehensive debug logging for troubleshooting
- **Files Modified**: `src/actuators/manager.py`
- **Status**: ✅ Complete

### 7. Fixed Action Execution
- **Issue**: Gesture actions were calling `robot.wave_hand()` instead of `robot.actuators.wave_hand()`
- **Fix**: Updated action execution to call actuator methods directly
- **Files Modified**: `src/ai/manager.py`
- **Status**: ✅ Complete

### 8. Enhanced Error Handling and Logging
- **Added**: Detailed debug logging throughout connection and actuator code
- **Added**: Connection object ID tracking for debugging
- **Added**: Bridge client availability checks
- **Status**: ✅ Complete

## Current Issues / What's Left to Work On

### 1. Bridge Mode Detection Issue ⚠️ **PRIORITY**
- **Problem**: Connection initializes with `use_bridge=True` and connects successfully via bridge, but when actions are called (e.g., `wave_hand()`), the `use_bridge` attribute shows `False`
- **Symptoms**:
  - Initialization logs show: `Connection initialized: use_bridge=True`
  - Bridge connection succeeds: `Successfully connected to Pepper robot via bridge: Pepper`
  - But action logs show: `wave_hand: use_bridge=False`
- **Potential Causes**:
  - Connection object may be modified after initialization
  - Different connection object instance being used
  - Attribute not being read correctly
- **Next Steps**:
  - Verify connection object identity (check if same instance)
  - Check if connection is being recreated or modified
  - Add connection object ID tracking to verify same instance
  - Investigate why `getattr(self.connection, 'use_bridge', False)` returns False

### 2. Debug Logs Not Always Appearing
- **Problem**: Enhanced debug logs with connection ID not always appearing in output
- **Potential Causes**:
  - Python bytecode caching
  - Log level filtering
  - Exception occurring before log line
- **Next Steps**:
  - Ensure all caches are cleared
  - Verify log level settings
  - Check for exceptions

### 3. Speech Recognition Vocabulary Issues
- **Problem**: Speech recognition shows warnings about empty or invalid vocabulary
- **Status**: Bridge has vocabulary handling, but warnings persist
- **Next Steps**: Review and improve vocabulary handling in bridge

### 4. Animation Alternatives
- **Status**: Added fallback logic, but may need to verify animation names available on Pepper
- **Next Steps**: Test with actual Pepper to verify animation names

## Files Changed

### Core Files
- `src/pepper/connection.py` - Bridge mode detection and connection management
- `src/actuators/manager.py` - All actuator methods updated for bridge
- `src/ai/models.py` - OpenAI API parameter fixes
- `src/ai/manager.py` - Action execution fixes
- `src/pepper/bridge_client.py` - Added animation and LED methods

### Bridge Service
- `pepper_bridge.py` - Added animation and LED support, improved error handling

### Configuration
- `.env` - Bridge configuration (already had `USE_PEPPER_BRIDGE=true`)

## Testing Performed

1. ✅ Bridge deployment and restart
2. ✅ Application startup and initialization
3. ✅ API chat endpoint testing
4. ✅ Bridge connection verification
5. ✅ Direct bridge command testing (speak works)
6. ⚠️ Action execution via AI responses (gestures not executing - bridge mode detection issue)

## Research Conducted

- Reviewed external repositories for Pepper control patterns
- Researched ALAnimationPlayer usage and best practices
- Researched OpenAI API parameter requirements for gpt-5-mini
- Reviewed animation execution patterns from other projects

## Next Session Priorities

1. **Debug bridge mode detection issue** - Why `use_bridge` shows False during action execution
2. **Verify connection object identity** - Ensure same connection instance is used
3. **Test gesture execution** - Once bridge mode is confirmed working, test wave and nod gestures
4. **Test LED control** - Verify eye color changes work
5. **Test speech recognition** - Verify speech input works correctly
6. **End-to-end testing** - Full conversation flow with gestures and responses

## Notes

- System is successfully connecting to bridge
- Bridge health checks pass
- Direct bridge commands work (tested speak)
- The main issue is bridge mode not being detected during action execution
- All code changes are in place, just need to debug why attribute appears False

---

## Date: December 17, 2025

### Summary
- Integrated Bridge v2 (HTTP + WebSocket) compatibility into the application while keeping Bridge v1 support.
- Exposed new v2 capabilities (head control, animations list/play, tablet controls, awareness/volume, camera snapshot) through the local REST API.
- Updated deploy/start scripts to prefer the v2 export if present on the robot.

### What We Accomplished Today
- Bridge client (src/pepper/bridge_client.py)
  - Detects bridge version and WS stream URLs from /health; caches version/streams.
  - Adds v2 endpoints: move_head, wake_up/wakeup, rest, set_volume, set_awareness, list_animations, tablet_show_{image,web,text,hide,brightness}, take_picture.
  - LED eyes endpoint auto-fallback: tries /leds/eyes (v2) and falls back to /led/eyes (v1).
- Connection health (src/pepper/connection.py)
  - Includes bridge_version and streams when available; returns the actual bridge_url.
- Sensors (src/sensors/manager.py)
  - take_photo uses v2 /picture (base64 RGB) in bridge mode; falls back to NAOqi in direct mode.
  - listen_for_speech: detects v2 (no /listen) and avoids failing call (returns None for now).
- Optional streaming helper
  - New src/pepper/bridge_stream.py provides a client for v2 WebSocket streams (video/audio/sensors).
- Actuators (src/actuators/manager.py)
  - New helpers: move_head, wake_up, rest, set_volume, set_awareness, tablet_* helpers, list_animations, play_animation.
- REST API (src/communication/api.py)
  - New endpoints: POST /move/head, /wake_up, /rest, /volume, /awareness, /tablet/{text,web,image,hide,brightness}, GET /animations, POST /animation.
- Scripts
  - deploy_bridge.sh now deploys pepper_bridge_export/ and ensures pepper_bridge_v2.py is executable, but still deploys pepper_bridge.py for v1 compatibility.
  - start_bridge.exp prefers v2 if present on robot; check_bridge.sh and start_system.sh updated accordingly.

### Files Changed
- src/pepper/bridge_client.py, src/pepper/connection.py, src/sensors/manager.py, src/pepper/bridge_stream.py (NEW)
- src/actuators/manager.py, src/communication/api.py
- deploy_bridge.sh, start_bridge.exp, check_bridge.sh, start_system.sh

### Testing Performed (CLI)
- Bridge health:
  - ./check_bridge.sh
  - curl http://localhost:8000/health | jq
- Head movement:
  - curl -X POST http://localhost:8000/move/head -H "Content-Type: application/json" -d '{"yaw":30,"pitch":-10,"speed":0.25}'
- Wake/rest:
  - curl -X POST http://localhost:8000/wake_up
  - curl -X POST http://localhost:8000/rest
- Volume/awareness:
  - curl -X POST http://localhost:8000/volume -H "Content-Type: application/json" -d '{"volume":70}'
  - curl -X POST http://localhost:8000/awareness -H "Content-Type: application/json" -d '{"enabled":true}'
- Animations:
  - curl http://localhost:8000/animations
  - curl -X POST "http://localhost:8000/animation?name=animations/Stand/Gestures/Hey_1"
- Tablet:
  - curl -X POST http://localhost:8000/tablet/text -H "Content-Type: application/json" -d '{"text":"Hello from v2!","background":"#003366"}'
  - curl -X POST http://localhost:8000/tablet/web -H "Content-Type: application/json" -d '{"url":"https://example.com"}'
  - curl -X POST http://localhost:8000/tablet/hide
  - curl -X POST "http://localhost:8000/tablet/brightness?brightness=60"
- Photo (v2 /picture under the hood):
  - curl -X POST http://localhost:8000/photo | jq '.success,.photo!=null'

### Known Limitations / Next Steps
- Continuous speech (v2): Implement local STT that subscribes to ws://…/audio via BridgeStreamClient and forwards recognized text to AIManager.
- Optionally add background tasks to consume video/sensors WS streams.

### Deployment Notes
- If pepper_bridge_export/ exists locally, ./deploy_bridge.sh will copy it to /home/nao/pepper_bridge_export and make pepper_bridge_v2.py executable. start_bridge.exp will prefer the v2 script on the robot.

