#!/bin/bash
# Start PepperEvolution system - Bridge service and main application

PEPPER_IP="10.0.100.100"
PEPPER_USER="nao"
PEPPER_PASS="nao"

echo "=========================================="
echo "PepperEvolution System Startup"
echo "=========================================="
echo ""

# Check if bridge is already running
echo "1. Checking if bridge service is running..."
if curl -s http://$PEPPER_IP:8888/health > /dev/null 2>&1; then
    echo "   ✓ Bridge service is already running"
    BRIDGE_RUNNING=true
else
    echo "   ✗ Bridge service is not running"
    BRIDGE_RUNNING=false
fi

# Start bridge if not running
if [ "$BRIDGE_RUNNING" = false ]; then
    echo ""
    echo "2. Starting bridge service..."
    
    # Use expect script to start bridge
    if [ -f "./start_bridge.exp" ]; then
        ./start_bridge.exp
        sleep 3
        
        # Verify it started
        if curl -s http://$PEPPER_IP:8888/health > /dev/null 2>&1; then
            echo "   ✓ Bridge service started successfully"
            BRIDGE_RUNNING=true
        else
            echo "   ✗ Failed to start bridge service"
            echo "   Try manually: ssh $PEPPER_USER@$PEPPER_IP"
            echo "   Then run: if [ -f /home/nao/pepper_bridge_export/pepper_bridge_v2.py ]; then cd /home/nao/pepper_bridge_export && nohup python pepper_bridge_v2.py > /tmp/pepper_bridge.log 2>&1 &; else cd /home/nao && nohup python pepper_bridge.py > /tmp/pepper_bridge.log 2>&1 &; fi"
            exit 1
        fi
    else
        echo "   ✗ start_bridge.exp not found"
        echo "   Please deploy bridge first: ./deploy_bridge.sh"
        exit 1
    fi
fi

# Check environment
echo ""
echo "3. Checking environment configuration..."
if [ ! -f ".env" ]; then
    echo "   ✗ .env file not found"
    echo "   Creating from env.example..."
    cp env.example .env
    echo "   ⚠️  Please edit .env with your settings (especially OPENAI_API_KEY)"
    echo "   Press Enter to continue or Ctrl+C to exit..."
    read
fi

# Check for OpenAI API key
if grep -q "OPENAI_API_KEY=your_openai_api_key_here" .env 2>/dev/null || ! grep -q "OPENAI_API_KEY=" .env 2>/dev/null; then
    echo "   ⚠️  OPENAI_API_KEY not set in .env"
    echo "   Please set your OpenAI API key in .env file"
    echo "   Press Enter to continue or Ctrl+C to exit..."
    read
fi

# Activate virtual environment
echo ""
echo "4. Activating virtual environment..."
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "   ✓ Virtual environment activated"
else
    echo "   ✗ Virtual environment not found"
    echo "   Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    echo "   Installing dependencies..."
    pip install -r requirements.txt
fi

# Verify bridge one more time
echo ""
echo "5. Final bridge check..."
BRIDGE_HEALTH=$(curl -s http://$PEPPER_IP:8888/health 2>&1)
if echo "$BRIDGE_HEALTH" | grep -q "healthy"; then
    echo "   ✓ Bridge is healthy"
    echo "   Response: $BRIDGE_HEALTH"
else
    echo "   ⚠️  Bridge health check failed"
    echo "   Response: $BRIDGE_HEALTH"
    echo "   Continuing anyway..."
fi

# Start main application
echo ""
echo "=========================================="
echo "Starting PepperEvolution Application"
echo "=========================================="
echo ""
echo "Bridge service: ✓ Running on port 8888 (WS on 8889 in v2)"
echo "Main application: Starting..."
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Start the main application
python main.py

