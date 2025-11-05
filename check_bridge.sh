#!/bin/bash
# Quick script to check bridge service status

PEPPER_IP="10.0.100.100"

echo "Checking bridge service status..."
echo ""

# Check if bridge is responding
if curl -s http://$PEPPER_IP:8888/health > /dev/null 2>&1; then
    echo "✓ Bridge service is running"
    echo ""
    echo "Health check response:"
    curl -s http://$PEPPER_IP:8888/health | python3 -m json.tool 2>/dev/null || curl -s http://$PEPPER_IP:8888/health
    echo ""
else
    echo "✗ Bridge service is not responding"
    echo ""
    echo "To start the bridge service, run:"
    echo "  ./start_bridge.exp"
    echo ""
    echo "Or manually via SSH:"
    echo "  ssh nao@$PEPPER_IP"
    echo "  cd /home/nao"
    echo "  nohup python pepper_bridge.py > /tmp/pepper_bridge.log 2>&1 &"
    echo ""
fi

