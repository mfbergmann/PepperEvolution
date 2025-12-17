#!/bin/bash
# Deploy Pepper Bridge Service to Pepper robot

PEPPER_IP="10.0.100.100"
PEPPER_USER="nao"
PEPPER_PASS="nao"
BRIDGE_FILE="pepper_bridge.py"
BRIDGE_DIR="pepper_bridge_export"
REMOTE_PATH="/home/nao/pepper_bridge.py"
REMOTE_DIR="/home/nao/pepper_bridge_export"
REMOTE_V2="/home/nao/pepper_bridge_v2.py"

echo "Deploying Pepper Bridge Service..."
echo ""

# If a v2 export directory exists locally, deploy it first
if [ -d "$BRIDGE_DIR" ]; then
    echo "Found $BRIDGE_DIR – deploying v2 export directory..."
    expect << EOF
spawn scp -o StrictHostKeyChecking=no -r $BRIDGE_DIR $PEPPER_USER@$PEPPER_IP:$REMOTE_DIR
expect {
    "password:" { send "$PEPPER_PASS\r"; exp_continue }
    "Password:" { send "$PEPPER_PASS\r"; exp_continue }
    eof {}
}
EOF
    if [ $? -ne 0 ]; then
        echo "✗ Failed to deploy $BRIDGE_DIR"
        exit 1
    fi
    # Ensure v2 script is executable
    expect << EOF
spawn ssh -o StrictHostKeyChecking=no $PEPPER_USER@$PEPPER_IP "chmod +x $REMOTE_DIR/pepper_bridge_v2.py && ln -sf $REMOTE_DIR/pepper_bridge_v2.py $REMOTE_V2"
expect {
    "password:" { send "$PEPPER_PASS\r"; exp_continue }
    "Password:" { send "$PEPPER_PASS\r"; exp_continue }
    eof {}
}
EOF
    echo "✓ Deployed v2 export to $REMOTE_DIR"
fi

# Deploy legacy single-file bridge for compatibility
if [ -f "$BRIDGE_FILE" ]; then
    echo "Deploying $BRIDGE_FILE for compatibility..."
    expect << EOF
spawn scp -o StrictHostKeyChecking=no $BRIDGE_FILE $PEPPER_USER@$PEPPER_IP:$REMOTE_PATH
expect {
    "password:" { send "$PEPPER_PASS\r"; exp_continue }
    "Password:" { send "$PEPPER_PASS\r"; exp_continue }
    eof {}
}
EOF
    if [ $? -eq 0 ]; then
        echo "✓ File deployed successfully"
        # Make it executable
        expect << EOF
spawn ssh -o StrictHostKeyChecking=no $PEPPER_USER@$PEPPER_IP "chmod +x $REMOTE_PATH"
expect {
    "password:" { send "$PEPPER_PASS\r"; exp_continue }
    "Password:" { send "$PEPPER_PASS\r"; exp_continue }
    eof {}
}
EOF
        echo "✓ Made executable"
    else
        echo "✗ Deployment of $BRIDGE_FILE failed"
        exit 1
    fi
fi

echo ""
echo "To run the bridge service on Pepper:"
echo "  ssh $PEPPER_USER@$PEPPER_IP"
if [ -d "$BRIDGE_DIR" ]; then
  echo "  cd $REMOTE_DIR && nohup python pepper_bridge_v2.py > /tmp/pepper_bridge.log 2>&1 &"
else
  echo "  nohup python $REMOTE_PATH > /tmp/pepper_bridge.log 2>&1 &"
fi

