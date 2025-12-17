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
    echo "  ./start_bridge.exp  # prefers v2 if present on robot"
    echo ""
    echo "Or manually via SSH:"
    echo "  ssh nao@$PEPPER_IP"
    echo "  if [ -f /home/nao/pepper_bridge_export/pepper_bridge_v2.py ]; then"
    echo "    cd /home/nao/pepper_bridge_export && nohup python pepper_bridge_v2.py > /tmp/pepper_bridge.log 2>&1 &"
    echo "  else"
    echo "    cd /home/nao && nohup python pepper_bridge.py > /tmp/pepper_bridge.log 2>&1 &"
    echo "  fi"
    echo ""
fi

